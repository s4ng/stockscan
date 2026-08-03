"""심볼 마스터 캐시 계약 테스트 (ARCHITECTURE.md 4.7).

★ **여기서 지키는 것 하나가 나머지를 다 정한다** — 거래대금은 캐시하지 않는다.

목록 응답에는 성격이 다른 둘이 섞여 있다. 마스터(심볼·이름·순서)는 하루 이틀
낡아도 무해하지만, 거래대금을 캐시하면 **어제의 상위 60종목을 오늘 훑게 된다.**
성능 문제가 아니라 그날의 판단이 달라지는 문제다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.market.instrument import InstrumentRef
from app.providers.base import (
    MarketDataProvider,
    ProviderCapabilities,
    UniverseEntry,
)
from app.providers.registry import ProviderRegistry
from app.providers.universe_source import CachedUniverse, DirectUniverse
from app.storage import instruments as master
from app.storage.models import Base

NOW = datetime(2026, 8, 3, 21, tzinfo=UTC)


class CountingProvider(MarketDataProvider):
    """몇 번 불렸는지 세는 소스. 캐시가 실제로 호출을 아꼈는지가 요지다."""

    id = "counting"
    display_name = "카운터"
    venues = ("nasdaq",)
    credential_schema = None
    capabilities = ProviderCapabilities(timeframes=("1d",), provides_universe=True)

    def __init__(self, turnover: float | None = None) -> None:
        self.calls = 0
        self._turnover = turnover

    async def fetch_ohlcv(self, instrument, timeframe, end, limit):  # pragma: no cover
        raise NotImplementedError

    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        self.calls += 1
        return [
            UniverseEntry(
                instrument=replace(
                    InstrumentRef.parse(f"nasdaq:{sym}"), display_name=name
                ),
                quote_volume_24h=self._turnover,
            )
            for sym, name in (("AAPL", "Apple Inc"), ("MSFT", "Microsoft Corp"))
        ]


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def registry(provider: CountingProvider) -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register(provider)
    reg.set_route("nasdaq", "*", ["counting"])
    return reg


# --------------------------------------------------------------------- 캐시 적중
async def test_master_is_fetched_once_then_served_from_cache(maker):
    provider = CountingProvider()
    source = CachedUniverse(registry(provider), maker, now=NOW)

    first = await source.list_instruments("nasdaq")
    second = await source.list_instruments("nasdaq")

    assert provider.calls == 1  # 두 번째는 소스를 부르지 않았다
    assert first.from_cache is False
    assert second.from_cache is True
    assert [e.instrument.display_name for e in second.entries] == ["Apple Inc", "Microsoft Corp"]


async def test_cache_expires_after_the_ttl(maker):
    """상장·폐지는 하루 단위로 일어난다. 영원히 캐시하면 신규 상장이 안 들어온다."""
    provider = CountingProvider()
    reg = registry(provider)
    await CachedUniverse(reg, maker, now=NOW).list_instruments("nasdaq")

    later = CachedUniverse(reg, maker, now=NOW + timedelta(days=2))
    result = await later.list_instruments("nasdaq")

    assert provider.calls == 2
    assert result.from_cache is False


# ------------------------------------------------------- ★ 거래대금은 캐시하지 않는다
async def test_turnover_always_goes_to_the_source(maker):
    """★ 캐시된 마스터로 유동성 컷을 걸면 **어제의 상위 N종목**을 오늘 훑게 된다."""
    provider = CountingProvider(turnover=1_000.0)
    source = CachedUniverse(registry(provider), maker, now=NOW)

    for _ in range(3):
        result = await source.list_instruments("nasdaq", needs_turnover=True)

    assert provider.calls == 3  # 매번 다시 받았다
    assert result.from_cache is False
    assert result.entries[0].quote_volume_24h == 1_000.0


async def test_cached_entries_never_carry_turnover(maker):
    """저장하지 않으므로 None이어야 한다. 0으로 채우면 유동성 컷이 조용히 무의미해진다."""
    provider = CountingProvider(turnover=1_000.0)
    source = CachedUniverse(registry(provider), maker, now=NOW)

    await source.list_instruments("nasdaq")  # 캐시를 채운다
    cached = await source.list_instruments("nasdaq")

    assert cached.from_cache is True
    assert all(e.quote_volume_24h is None for e in cached.entries)


# ------------------------------------------------------------------------- 저장
async def test_duplicate_symbols_in_the_listing_are_collapsed(maker):
    """⚠️ FDR의 나스닥 목록에는 같은 심볼이 두 번 나온다. 먼저 나온 것이 대표 표기다."""
    entries = [
        UniverseEntry(InstrumentRef.parse("nasdaq:AAPL"), None),
        UniverseEntry(InstrumentRef.parse("nasdaq:MSFT"), None),
        UniverseEntry(InstrumentRef.parse("nasdaq:AAPL"), None),  # 중복
    ]
    async with maker() as session:
        saved = await master.save(session, "nasdaq", entries, source_id="fdr", now=NOW)
        await session.commit()
        snapshot = await master.load(session, "nasdaq", now=NOW)

    assert saved == 2
    assert [e.instrument.symbol for e in snapshot.entries] == ["AAPL", "MSFT"]


async def test_saving_replaces_the_whole_venue(maker):
    """폐지된 종목이 남으면 유니버스에 없는 종목을 계속 훑게 된다."""
    async with maker() as session:
        await master.save(
            session,
            "nasdaq",
            [UniverseEntry(InstrumentRef.parse("nasdaq:GONE"), None)],
            source_id="fdr",
            now=NOW,
        )
        await master.save(
            session,
            "nasdaq",
            [UniverseEntry(InstrumentRef.parse("nasdaq:AAPL"), None)],
            source_id="fdr",
            now=NOW,
        )
        await session.commit()
        snapshot = await master.load(session, "nasdaq", now=NOW)

    assert [e.instrument.symbol for e in snapshot.entries] == ["AAPL"]


async def test_direct_source_never_touches_the_cache(maker):
    provider = CountingProvider()
    source = DirectUniverse(registry(provider))

    await source.list_instruments("nasdaq")
    await source.list_instruments("nasdaq")

    assert provider.calls == 2


# -------------------------------------------------------------------- 스키마 드리프트
async def test_schema_drift_is_raised_not_swallowed(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """★ 조용히 넘어가면 캐시가 영영 안 채워지는데 "좀 느리네"로만 보인다."""
    import sqlite3

    from app.storage import db

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    # 컬럼이 모자란 옛 테이블을 흉내 낸다
    conn.execute(
        "CREATE TABLE ingestion_jobs (venue TEXT, symbol TEXT, timeframe TEXT, "
        "adjusted BOOLEAN, PRIMARY KEY (venue, symbol, timeframe, adjusted))"
    )
    conn.commit()
    conn.close()

    db.configure(f"sqlite+aiosqlite:///{path.as_posix()}")
    try:
        with pytest.raises(db.SchemaDriftError, match="lookback"):
            await db.init_db()
    finally:
        await db.dispose()
        db.configure("sqlite+aiosqlite:///:memory:")
