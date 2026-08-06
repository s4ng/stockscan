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
from app.storage import history, ohlcv_cache
from app.storage.models import Base, IngestionJobRow, SignalRow
from tests.conftest import make_config

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


#: synthetic 소스가 주는 목록의 앞부분. `SYNTHETIC_LISTING`과 짝을 이룬다.
NASDAQ = ["nasdaq:AAPL", "nasdaq:MSFT"]
KRX = ["krx:005930", "krx:000660"]


def spec(universe: dict[str, int] | None = None):
    """수집 대상은 **설정의 universe를 실제로 풀어서** 나온다.

    ⚠️ 예전에는 `marketData` 노드의 `instruments`를 읽었는데, 설정에서 종목을 손으로
    적는 자리가 사라졌다. 컷 조건을 여기서 다시 구현하지 않고 파이프라인과 같은
    함수를 부르는 것이 요점이다 — 갈라지면 실행이 훑는 것과 캐시가 담는 것이
    달라지고, 갈라진 종목은 캐시 미스로 조용히 소스를 두드린다 (3.9).
    """
    return make_config(universe=universe or {"nasdaq": 1})


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
async def test_targets_come_from_the_universe():
    plan = await worker.plan_targets(spec({"nasdaq": 2, "krx": 2}), context())

    assert {t.instrument.key for t in plan.targets} == set(NASDAQ) | set(KRX)
    assert all(t.origin == "universe" for t in plan.targets)


async def test_depth_is_derived_from_the_strategy():
    """★ 설정의 lookback과 전략의 워밍업이 어긋나면 그 종목이 조용히 전량 제외된다.

    유도하면 어긋날 자리가 없어진다.
    """
    from app.config import lookback_for
    from app.strategies.registry import load_strategy

    plan = await worker.plan_targets(spec(), context())
    expected = lookback_for(load_strategy("demo_momentum").strategy.startup_candles)

    assert all(t.lookback == expected for t in plan.targets)


async def test_end_is_the_last_closed_bar_of_each_market():
    """시장마다 마감이 다르다. 하나로 뭉뚱그리면 없는 세션의 봉을 요청한다."""
    plan = await worker.plan_targets(spec({"nasdaq": 1, "krx": 1}), context())
    ends = {t.instrument.key: t.end for t in plan.targets}

    # NOW는 3/10 12:00 UTC — KRX는 그날 15:30 KST(06:30 UTC)에 이미 닫혔지만
    # 미국은 아직 열리지도 않아 직전 세션(3/9)이 마지막 마감이다.
    assert ends["krx:005930"] == datetime(2026, 3, 10, 6, 30, tzinfo=UTC)
    assert ends["nasdaq:AAPL"] == datetime(2026, 3, 9, 20, 0, tzinfo=UTC)
    assert ends["nasdaq:AAPL"] != ends["krx:005930"]


async def test_lookback_override_wins():
    plan = await worker.plan_targets(spec(), context(), lookback=500)
    assert plan.targets[0].lookback == 500


# ------------------------------------------------------------------------ 수집
async def test_ingest_fills_the_cache(maker):
    ctx = context()
    plan = await worker.plan_targets(spec(), ctx)
    report = await worker.ingest(plan, ctx, maker)

    depth = plan.targets[0].lookback
    assert report.fetched == 1
    assert report.inserted == depth
    async with maker() as session:
        cov = await ohlcv_cache.coverage(
            session, InstrumentRef.parse("nasdaq:AAPL"), "1d", adjusted=True
        )
    assert cov.bars == depth
    assert cov.last == plan.targets[0].end


async def test_second_run_does_not_touch_the_source(maker):
    """하루 1회 수집이 전제다. 두 번 불러도 소스를 두 번 밟지 않는다."""
    ctx = context()
    plan = await worker.plan_targets(spec(), ctx)
    await worker.ingest(plan, ctx, maker)

    again = await worker.ingest(plan, ctx, maker)
    assert (again.fetched, again.skipped_fresh) == (0, 1)

    forced = await worker.ingest(plan, ctx, maker, force=True)
    assert forced.fetched == 1


async def test_one_failure_does_not_stop_the_rest(maker):
    ctx = context()
    plan = await worker.plan_targets(spec({"nasdaq": 2}), ctx)
    original = ctx.providers.fetch_ohlcv

    async def flaky(instrument, *args, **kwargs):
        if instrument.symbol == "AAPL":
            raise RuntimeError("소스가 죽었다")
        return await original(instrument, *args, **kwargs)

    ctx.providers.fetch_ohlcv = flaky  # type: ignore[method-assign]
    report = await worker.ingest(plan, ctx, maker)

    assert report.fetched == 1
    assert [k for k, _ in report.failures] == ["nasdaq:AAPL"]
    async with maker() as session:
        failed = await session.get(IngestionJobRow, ("nasdaq", "AAPL", "1d", True))
    assert failed.failure_count == 1
    assert "죽었다" in failed.last_error


async def test_conflicting_closes_surface_in_the_report(maker):
    """3.8 — 두 소스가 같은 봉을 다르게 주면 수집 리포트에 뜬다."""
    ctx = context()
    plan = await worker.plan_targets(spec(), ctx)
    btc = InstrumentRef.parse("nasdaq:AAPL")
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


# ------------------------------------------------------- 규칙 18 (과거 신호 종목)
async def test_signalled_instruments_keep_being_collected(monkeypatch, maker):
    """★ 유니버스에서 밀리는 종목은 **대개 내린 종목**이다.

    봉이 끊기면 그 신호의 사후 수익률이 결측되고, 하필 손실만 골라서 빠지므로
    성적표가 조용히 낙관 편향된다. 채점의 정직성이 여기에 걸려 있다 (규칙 18).
    """
    from app.storage import db as db_module

    async with maker() as session:
        session.add(
            SignalRow(
                run_id="r1",
                pipeline_id="p1",
                node_id="persist",
                dedup_key="k1",
                instrument="nasdaq:TSLA",  # 유니버스(상위 2)에는 없는 종목
                venue="nasdaq",
                timeframe="1d",
                as_of=NOW,
            )
        )
        await session.commit()

    monkeypatch.setattr(db_module, "database_url", lambda: "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(db_module, "session_scope", _scope(maker))

    plan = await worker.plan_targets(spec({"nasdaq": 2}), context())

    keys = {t.instrument.key for t in plan.targets}
    assert "nasdaq:TSLA" in keys
    assert [t.origin for t in plan.targets if t.instrument.key == "nasdaq:TSLA"] == ["signal"]
    assert any("규칙 18" in note for note in plan.notes)


async def test_a_signalled_instrument_still_in_the_universe_is_not_duplicated(
    monkeypatch, maker
):
    async with maker() as session:
        session.add(
            SignalRow(
                run_id="r1",
                pipeline_id="p1",
                node_id="persist",
                dedup_key="k1",
                instrument="nasdaq:AAPL",  # 유니버스 상위에 이미 있다
                venue="nasdaq",
                timeframe="1d",
                as_of=NOW,
            )
        )
        await session.commit()

    from app.storage import db as db_module

    monkeypatch.setattr(db_module, "database_url", lambda: "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(db_module, "session_scope", _scope(maker))

    plan = await worker.plan_targets(spec({"nasdaq": 2}), context())

    assert [t.instrument.key for t in plan.targets].count("nasdaq:AAPL") == 1
    assert all(t.origin == "universe" for t in plan.targets)


def _scope(maker):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        async with maker() as session:
            yield session

    return scope


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
    plan = await worker.plan_targets(spec({"krx": 1}), ctx, include_delisted=True)

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
    plan = await worker.plan_targets(spec({"krx": 1}), ctx, include_delisted=True)

    assert len(plan.targets) == 1
    assert plan.targets[0].origin == "universe"
    assert plan.targets[0].end == datetime(2026, 3, 10, 6, 30, tzinfo=UTC)
