"""Ingestion Worker 계약 테스트 (ARCHITECTURE.md 3.9).

지키는 것은 넷이다.

  1. **수집 대상은 파이프라인에서 자동 도출된다** — 손으로 관리하면 파이프라인을
     고칠 때마다 어긋나고, 어긋난 종목은 캐시가 비어 조용히 빠진다
  2. **하루에 같은 봉을 두 번 받지 않는다** — 무료 소스를 밟는 횟수가 이 워커의
     존재 이유다
  3. **한 종목의 실패가 수집을 멈추지 않고, `ingestion_jobs`에 누적된다**
  4. ★ **폐지 종목은 폐지 시점을 `end`로 잡는다** — 오늘 기준으로 조회하면 그
     구간에 봉이 없어 **빈 결과가 성공처럼 보인다** (4.8 서바이버십)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.engine.context import RunContext
from app.ingest import worker
from app.market.instrument import InstrumentRef
from app.providers.registry import ProviderRegistry
from app.providers.synthetic import SyntheticProvider
from app.schemas.pipeline import EdgeSpec, NodeSpec, PipelineSpec
from app.storage import ohlcv_cache
from app.storage.models import Base, IngestionJobRow

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def spec(instruments: list[str], lookback: int = 30) -> PipelineSpec:
    return PipelineSpec(
        pipeline_id="pipe_ingest",
        name="수집 테스트",
        nodes=[
            NodeSpec(
                id="data",
                type="marketData",
                params={
                    "instruments": instruments,
                    "timeframe": "1d",
                    "lookback": lookback,
                    "source": "synthetic",
                },
            ),
            NodeSpec(
                id="strategy",
                type="strategyRunner",
                params={"strategy_id": "demo_momentum"},
            ),
        ],
        edges=[EdgeSpec(id="e1", source="data", target="strategy")],
    )


def context() -> RunContext:
    registry = ProviderRegistry()
    registry.register(SyntheticProvider())
    return RunContext.create(now=NOW, providers=registry, pipeline_id="pipe_ingest")


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ------------------------------------------------------------------------ 계획
async def test_targets_come_from_the_pipeline():
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC", "krx:005930"]), context())

    assert {t.instrument.key for t in plan.targets} == {"upbit:KRW-BTC", "krx:005930"}
    assert all(t.lookback == 30 for t in plan.targets)
    # 노드가 소스를 못 박았으면 수집도 그것을 따른다 — 캐시를 채운 소스와
    # 파이프라인이 쓰려던 소스가 갈리면 3.8이 그 자리에서 깨진다.
    assert all(t.source == "synthetic" for t in plan.targets)


async def test_end_is_the_last_closed_bar_of_each_market():
    """시장마다 마감이 다르다. 하나로 뭉뚱그리면 없는 세션의 봉을 요청한다."""
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC", "krx:005930"]), context())
    ends = {t.instrument.key: t.end for t in plan.targets}

    assert ends["upbit:KRW-BTC"] == datetime(2026, 3, 10, tzinfo=UTC)
    assert ends["krx:005930"] == datetime(2026, 3, 10, 6, 30, tzinfo=UTC)


async def test_lookback_override_wins():
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC"]), context(), lookback=500)
    assert plan.targets[0].lookback == 500


async def test_a_universe_node_is_actually_resolved():
    """컷 조건을 다시 구현하지 않고 노드를 돌린다 — 갈라지면 캐시가 비게 된다."""
    pipeline = spec([])
    pipeline.nodes.insert(
        0,
        NodeSpec(
            id="universe",
            type="symbolUniverse",
            params={"instruments": ["upbit:KRW-BTC", "upbit:KRW-ETH"]},
        ),
    )
    plan = await worker.plan_targets(pipeline, context())

    assert {t.instrument.key for t in plan.targets} == {"upbit:KRW-BTC", "upbit:KRW-ETH"}
    assert all(t.origin == "universe" for t in plan.targets)


# ------------------------------------------------------------------------ 수집
async def test_ingest_fills_the_cache(maker):
    ctx = context()
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC"]), ctx)
    report = await worker.ingest(plan, ctx, maker)

    assert report.fetched == 1
    assert report.inserted == 30
    async with maker() as session:
        cov = await ohlcv_cache.coverage(
            session, InstrumentRef.parse("upbit:KRW-BTC"), "1d", adjusted=True
        )
    assert cov.bars == 30
    assert cov.last == plan.targets[0].end


async def test_second_run_does_not_touch_the_source(maker):
    """하루 1회 수집이 전제다. 두 번 불러도 소스를 두 번 밟지 않는다."""
    ctx = context()
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC"]), ctx)
    await worker.ingest(plan, ctx, maker)

    again = await worker.ingest(plan, ctx, maker)
    assert (again.fetched, again.skipped_fresh) == (0, 1)

    forced = await worker.ingest(plan, ctx, maker, force=True)
    assert forced.fetched == 1


async def test_one_failure_does_not_stop_the_rest(maker):
    ctx = context()
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC", "upbit:KRW-ETH"]), ctx)
    original = ctx.providers.fetch_ohlcv

    async def flaky(instrument, *args, **kwargs):
        if instrument.symbol == "KRW-BTC":
            raise RuntimeError("소스가 죽었다")
        return await original(instrument, *args, **kwargs)

    ctx.providers.fetch_ohlcv = flaky  # type: ignore[method-assign]
    report = await worker.ingest(plan, ctx, maker)

    assert report.fetched == 1
    assert [k for k, _ in report.failures] == ["upbit:KRW-BTC"]
    async with maker() as session:
        failed = await session.get(IngestionJobRow, ("upbit", "KRW-BTC", "1d", True))
    assert failed.failure_count == 1
    assert "죽었다" in failed.last_error


async def test_conflicting_closes_surface_in_the_report(maker):
    """3.8 — 두 소스가 같은 봉을 다르게 주면 수집 리포트에 뜬다."""
    ctx = context()
    plan = await worker.plan_targets(spec(["upbit:KRW-BTC"]), ctx)
    btc = InstrumentRef.parse("upbit:KRW-BTC")
    end = plan.targets[0].end

    async with maker() as session:
        await ohlcv_cache.write_bars(
            session,
            btc,
            "1d",
            pd.DataFrame(
                {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                index=pd.DatetimeIndex([end], tz="UTC", name="time"),
            ),
            adjusted=True,
            source_id="다른소스",
        )
        await session.commit()

    report = await worker.ingest(plan, ctx, maker)
    assert any("다른소스" in c for c in report.conflicts)


# ------------------------------------------------------------- 서바이버십 (4.8)
async def test_delisted_targets_end_at_the_delisting_date(monkeypatch: pytest.MonkeyPatch):
    """★ 오늘을 end로 잡으면 그 구간에 봉이 없어 **빈 결과가 성공처럼 보인다.**"""
    ctx = context()

    class FakeFdr:
        async def list_delisted(self, venue, since=None):
            return pd.DataFrame(
                {
                    "Symbol": ["221670"],
                    "SecuGroup": ["주권"],
                    "DelistingDate": [pd.Timestamp("2019-07-16")],
                }
            )

    monkeypatch.setattr(ctx.providers, "get", lambda pid: FakeFdr())
    plan = await worker.plan_targets(spec(["krx:005930"]), ctx, include_delisted=True)

    delisted = [t for t in plan.targets if t.origin == "delisted"]
    assert [t.instrument.key for t in delisted] == ["krx:221670"]
    # 폐지일(2019-07-16) 장 마감. 오늘(2026-03-10)이 아니어야 한다.
    assert delisted[0].end == datetime(2019, 7, 16, 6, 30, tzinfo=UTC)


async def test_a_live_target_is_not_overwritten_by_the_delisted_list(
    monkeypatch: pytest.MonkeyPatch,
):
    """폐지 목록이 살아 있는 종목을 덮으면 그 종목의 최신 봉을 더는 모으지 않는다."""
    ctx = context()

    class FakeFdr:
        async def list_delisted(self, venue, since=None):
            return pd.DataFrame(
                {"Symbol": ["005930"], "DelistingDate": [pd.Timestamp("2019-07-16")]}
            )

    monkeypatch.setattr(ctx.providers, "get", lambda pid: FakeFdr())
    plan = await worker.plan_targets(spec(["krx:005930"]), ctx, include_delisted=True)

    assert len(plan.targets) == 1
    assert plan.targets[0].origin == "pipeline"
    assert plan.targets[0].end == datetime(2026, 3, 10, 6, 30, tzinfo=UTC)
