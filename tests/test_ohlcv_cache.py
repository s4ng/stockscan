"""`ohlcv_cache` 계약 테스트 (ARCHITECTURE.md 3.9 / 3.8).

여기서 지키는 것은 넷이다.

  1. **`adjusted`가 키에 들어간다** — 조정가와 비조정가가 섞이면 지표가 조용히
     어긋나고 원인 추적이 불가능해진다 (규칙 8)
  2. **`end` 이후 봉을 주지 않는다** — Provider와 같은 계약이다. 캐시가 예외가
     되면 백테스트 리플레이가 미래를 본다 (규칙 2)
  3. **두 소스가 같은 봉을 다르게 주면 드러난다** (3.8 정합성 검증)
  4. **`cache: only`는 커버리지가 모자라면 거부한다** — 조용히 소스로 물러서면
     "외부 호출을 하지 않는다"는 전제가 깨진다 (4.8)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.market.instrument import InstrumentRef
from app.providers.ccxt_base import CcxtProvider
from app.providers.ohlcv_source import CachedSource, CacheMissError, predicted_adjusted
from app.providers.registry import ProviderRegistry
from app.providers.synthetic import SyntheticProvider
from app.storage import ohlcv_cache
from app.storage.models import Base

BTC = InstrumentRef.parse("upbit:KRW-BTC")
START = datetime(2026, 3, 1, tzinfo=UTC)


def frame(bars: int, *, close: float = 100.0, start: datetime = START) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [start + timedelta(days=i) for i in range(bars)], tz="UTC", name="time"
    )
    return pd.DataFrame(
        {
            "open": [close] * bars,
            "high": [close + 1] * bars,
            "low": [close - 1] * bars,
            "close": [close + i for i in range(bars)],
            "volume": [10.0] * bars,
        },
        index=index,
    )


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ------------------------------------------------------------------- 저장·조회
async def test_roundtrip_preserves_order_and_values(maker):
    async with maker() as session:
        report = await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(5), adjusted=False, source_id="synthetic"
        )
        await session.commit()
        assert report.inserted == 5

        df, sources = await ohlcv_cache.read_bars(
            session, BTC, "1d", adjusted=False, end=START + timedelta(days=10), limit=10
        )
    assert len(df) == 5
    assert df.index.is_monotonic_increasing
    assert sources == ("synthetic",)
    assert df["close"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]


async def test_rewriting_the_same_bars_is_idempotent(maker):
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(5), adjusted=False, source_id="synthetic"
        )
        await session.commit()
        again = await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(5), adjusted=False, source_id="synthetic"
        )
        await session.commit()
    assert (again.inserted, again.updated) == (0, 0)


async def test_end_never_leaks_future_bars(maker):
    """규칙 2 — 캐시도 `end` 이후를 주지 않는다."""
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(10), adjusted=False, source_id="synthetic"
        )
        await session.commit()
        df, _ = await ohlcv_cache.read_bars(
            session, BTC, "1d", adjusted=False, end=START + timedelta(days=3), limit=100
        )
    assert df.index[-1].to_pydatetime() == START + timedelta(days=3)
    assert len(df) == 4


async def test_limit_takes_the_most_recent_bars(maker):
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(10), adjusted=False, source_id="synthetic"
        )
        await session.commit()
        df, _ = await ohlcv_cache.read_bars(
            session, BTC, "1d", adjusted=False, end=START + timedelta(days=9), limit=3
        )
    assert len(df) == 3
    assert df.index[0].to_pydatetime() == START + timedelta(days=7)


# ------------------------------------------------------------------- 규칙 8
async def test_adjusted_is_part_of_the_key(maker):
    """조정가와 비조정가는 **다른 계열**이다. 한 키에 섞이면 안 된다."""
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(3, close=100.0), adjusted=True, source_id="a"
        )
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(3, close=200.0), adjusted=False, source_id="b"
        )
        await session.commit()

        adj, _ = await ohlcv_cache.read_bars(
            session, BTC, "1d", adjusted=True, end=START + timedelta(days=9), limit=99
        )
        raw, _ = await ohlcv_cache.read_bars(
            session, BTC, "1d", adjusted=False, end=START + timedelta(days=9), limit=99
        )
    assert len(adj) == len(raw) == 3
    assert adj["close"].iloc[0] == 100.0
    assert raw["close"].iloc[0] == 200.0


# ------------------------------------------------------------------- 3.8 정합성
async def test_two_sources_disagreeing_on_a_close_is_reported(maker):
    """같은 날 종가가 다르면 경고한다. 덮어쓰되 **조용히** 덮어쓰지 않는다."""
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(3, close=100.0), adjusted=True, source_id="pykrx"
        )
        await session.commit()
        report = await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(3, close=150.0), adjusted=True, source_id="fdr"
        )
        await session.commit()

    assert len(report.conflicts) == 3
    assert report.updated == 3
    described = report.conflicts[0].describe()
    assert "pykrx" in described and "fdr" in described


async def test_same_source_recalculation_is_not_a_conflict(maker):
    """분할·배당으로 수정주가가 다시 계산된 것은 정상 갱신이지 불일치가 아니다."""
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(3, close=100.0), adjusted=True, source_id="pykrx"
        )
        await session.commit()
        report = await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(3, close=50.0), adjusted=True, source_id="pykrx"
        )
        await session.commit()
    assert report.conflicts == []
    assert report.updated == 3


# ------------------------------------------------------------------- 수집 상태
async def test_failures_accumulate_and_success_resets(maker):
    """연속 실패 횟수가 '소스가 조용히 죽었다'는 유일한 신호다."""
    async with maker() as session:
        for _ in range(3):
            await ohlcv_cache.record_job(
                session, BTC, "1d", adjusted=False, success=False, error="timeout"
            )
        await session.commit()
        stale = await ohlcv_cache.stale_jobs(session)
        assert stale[0].failure_count == 3

        await ohlcv_cache.record_job(
            session, BTC, "1d", adjusted=False, success=True, bars=5, source_id="synthetic"
        )
        await session.commit()
        assert await ohlcv_cache.stale_jobs(session) == []


# -------------------------------------------------------------- OhlcvSource 정책
class CacheableSynthetic(SyntheticProvider):
    """실제 소스인 척하는 synthetic. 캐시 **쓰기** 경로를 검증하기 위한 것이다.

    진짜 `SyntheticProvider`는 `cacheable=False`라 캐시에 들어가지 않는다 —
    합성 봉이 영구 자산에 섞이면 이후 실제 실행이 가짜 시세로 돌기 때문이다.
    """

    id = "cacheable_synthetic"
    capabilities = replace(SyntheticProvider.capabilities, cacheable=True)


def registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register(CacheableSynthetic())
    reg.set_route("upbit", "*", ["cacheable_synthetic"])
    return reg


async def test_cache_only_refuses_instead_of_calling_the_source(maker):
    """`only`가 몰래 네트워크를 타면 그것을 고른 이유가 사라진다."""
    source = CachedSource(registry(), maker, writable=False)
    with pytest.raises(CacheMissError, match="ingest"):
        await source.load(BTC, "1d", START, 30, policy="only")


async def test_cache_serves_the_run_without_touching_the_source(maker):
    """★ 3.9의 존재 이유 — 소스가 죽어도 쌓인 봉으로 계속 돈다."""
    reg = registry()
    end = START + timedelta(days=29)
    async with maker() as session:
        # synthetic은 `adjusted="always"`이므로 캐시 키도 True다. 이 값을 손으로
        # 정하면 규칙 8이 깨지므로 실제 소스가 선언한 것을 따라간다.
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(30), adjusted=True, source_id="synthetic"
        )
        await ohlcv_cache.record_job(
            session, BTC, "1d", adjusted=True, success=True, last_bar_time=end
        )
        await session.commit()

    async def explode(*args, **kwargs):
        raise AssertionError("캐시가 적중했다면 소스를 부르면 안 된다")

    reg.fetch_ohlcv = explode  # type: ignore[method-assign]
    result = await CachedSource(reg, maker, writable=False).load(BTC, "1d", end, 30)

    assert result.from_cache is True
    assert result.provider_id == "cache"
    assert result.cached_sources == ("synthetic",)
    assert len(result.df) == 30


async def test_shallow_cache_falls_through_to_the_source(maker):
    """캐시가 요청보다 짧으면 소스로 간다.

    조용히 짧은 계열을 주면 전략이 `startup_candles`에 못 미쳐 종목이 통째로
    사라지고, 그 이유가 어디에도 남지 않는다.
    """
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(5), adjusted=True, source_id="synthetic"
        )
        await session.commit()
    result = await CachedSource(registry(), maker, writable=False).load(
        BTC, "1d", START + timedelta(days=29), 30
    )
    assert result.from_cache is False
    assert result.provider_id == "cacheable_synthetic"


async def test_writable_flag_controls_the_cache_write(maker):
    """`writable=False`는 읽기만 한다 — 백테스트 리플레이가 캐시를 덮지 않게."""
    end = START + timedelta(days=29)
    await CachedSource(registry(), maker, writable=False).load(BTC, "1d", end, 30)
    async with maker() as session:
        assert (await ohlcv_cache.coverage(session, BTC, "1d", adjusted=True)).bars == 0

    await CachedSource(registry(), maker, writable=True).load(BTC, "1d", end, 30)
    async with maker() as session:
        assert (await ohlcv_cache.coverage(session, BTC, "1d", adjusted=True)).bars > 0


async def test_uncacheable_sources_never_reach_the_permanent_cache(maker):
    """★ 캐시는 소스를 구분해 **읽지** 않는다. 한 번 섞인 합성 봉은 되돌릴 수 없다.

    캐시에 삭제 경로가 없으므로(규칙 16) 쓰기 단계에서 막는 것이 유일한 방어선이다.
    dry-run도 캐시에 쓰게 되면서 이 경로가 실제로 밟히기 쉬워졌다.
    """
    reg = ProviderRegistry()
    reg.register(SyntheticProvider())  # cacheable=False
    reg.set_route("upbit", "*", ["synthetic"])

    result = await CachedSource(reg, maker, writable=True).load(
        BTC, "1d", START + timedelta(days=29), 30
    )

    assert not result.df.empty  # 봉은 정상적으로 받았고
    async with maker() as session:
        cov = await ohlcv_cache.coverage(session, BTC, "1d", adjusted=True)
    assert cov.bars == 0  # 캐시에는 남지 않았다


async def test_crypto_is_never_marked_adjusted():
    """코인에는 액면분할·배당이 없다. 설정을 그대로 베끼면 안 된다 (3.8).

    이 값이 캐시 키에 들어가므로(규칙 8), 설정을 베끼면 조정가/비조정가가 섞인다.
    """
    reg = ProviderRegistry()
    reg.register(CcxtProvider("upbit"))  # 생성은 네트워크를 타지 않는다
    reg.set_route("upbit", "*", ["ccxt.upbit"])
    assert predicted_adjusted(reg, BTC, "1d", True) is False


async def test_short_history_hits_the_cache_once_the_source_is_exhausted(maker):
    """★ 신규 상장 종목은 소스도 더 줄 수 없다. 매번 다시 부르면 순수한 낭비다.

    `lookback`(요청한 깊이)이 없으면 "아직 덜 모았다"와 "원래 이것뿐이다"를
    구분할 수 없어, 이력이 짧은 종목이 **영원히** 캐시를 못 맞힌다.
    """
    end = START + timedelta(days=29)
    async with maker() as session:
        await ohlcv_cache.write_bars(  # 5봉뿐 — 상장이 늦었다
            session, BTC, "1d", frame(5), adjusted=True, source_id="synthetic"
        )
        await ohlcv_cache.record_job(
            session,
            BTC,
            "1d",
            adjusted=True,
            success=True,
            lookback=30,  # 30봉을 요청했는데 5봉만 왔다 = 소스에 더 없다
            last_bar_time=START + timedelta(days=4),
            now=end,
        )
        await session.commit()

    reg = registry()

    async def explode(*args, **kwargs):
        raise AssertionError("소스가 줄 수 있는 만큼 다 받았으면 다시 부르면 안 된다")

    reg.fetch_ohlcv = explode  # type: ignore[method-assign]
    result = await CachedSource(reg, maker, writable=False).load(BTC, "1d", end, 30)

    assert result.from_cache is True
    assert len(result.df) == 5  # 짧다는 사실은 그대로 드러난다
    assert any("5봉" in n for n in result.notes)


async def test_shallow_cache_without_a_job_still_goes_to_the_source(maker):
    """수집 이력이 없으면 '덜 모은 것'으로 본다 — 조용히 짧은 계열을 주면 안 된다."""
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, BTC, "1d", frame(5), adjusted=True, source_id="synthetic"
        )
        await session.commit()

    result = await CachedSource(registry(), maker, writable=False).load(
        BTC, "1d", START + timedelta(days=29), 30
    )
    assert result.from_cache is False


async def test_a_run_records_the_ingestion_job(maker):
    """`run`만 쓰는 사용자에게도 수집 이력이 남아야 위 판정이 성립한다."""
    from app.storage.models import IngestionJobRow

    end = START + timedelta(days=29)
    await CachedSource(registry(), maker, writable=True).load(BTC, "1d", end, 30)

    async with maker() as session:
        job = await session.get(IngestionJobRow, ("upbit", "KRW-BTC", "1d", True))
    assert job is not None
    assert job.lookback == 30
    assert job.last_success_at == end
