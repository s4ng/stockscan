"""Forward Return Evaluator (ARCHITECTURE.md 4.8).

★ **이것이 이 프로젝트의 제품이다.** 신호가 났다는 사실만 쌓으면 스크리너는
자신감 기계가 된다 — 사람은 맞은 종목만 기억하기 때문이다.

여기서 지키는 것은 다섯이다.

  1. ★ **아직 N봉이 안 지난 신호는 `NULL`로 둔다** — 0으로 채우면 최근 신호가 전부
     "수익률 0%"로 잡혀 통계가 조용히 희석된다 (12.3 없는 숫자를 지어내지 않는다)
  2. ★ **날짜가 아니라 봉으로 센다** — 휴장일 때문에 "20일 뒤"와 "20봉 뒤"는 다르고,
     한국과 미국의 휴장일이 또 다르다
  3. ★ **봉이 끊긴 종목을 조용히 빼지 않고 센다** — 밀린 종목은 대개 내린 종목이라
     결측이 손실 쪽에 몰린다 (규칙 18)
  4. **`as_of` 봉은 분모다** — 포함해서 세면 1봉 뒤 수익률이 언제나 0이 된다
  5. **다시 불러도 안전하다** — 이미 채운 값을 건드리지 않는다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.evaluate import HORIZONS, evaluate
from app.market.instrument import InstrumentRef
from app.storage import ohlcv_cache
from app.storage.models import Base, SignalRow

pytestmark = pytest.mark.asyncio

AS_OF = datetime(2026, 3, 10, 6, 30, tzinfo=UTC)
SAMSUNG = InstrumentRef.parse("krx:005930")


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_bars(maker, closes: list[float], *, start: datetime = AS_OF) -> None:
    """`start`부터 하루씩 뒤로 봉을 깐다. 첫 값이 `as_of` 봉이다."""
    index = pd.DatetimeIndex(
        [start + timedelta(days=i) for i in range(len(closes))], tz="UTC", name="time"
    )
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=index,
    )
    async with maker() as session:
        await ohlcv_cache.write_bars(
            session, SAMSUNG, "1d", frame, adjusted=True, source_id="test"
        )
        await session.commit()


async def seed_signal(maker, *, instrument: str = "krx:005930", key: str = "k1") -> int:
    async with maker() as session:
        row = SignalRow(
            run_id="r1",
            pipeline_id="p1",
            node_id="persist",
            dedup_key=key,
            instrument=instrument,
            venue=instrument.split(":")[0],
            timeframe="1d",
            as_of=AS_OF,
        )
        session.add(row)
        await session.commit()
        return row.id


# ------------------------------------------------------------------------ 계산
async def test_forward_returns_are_computed_from_the_cache(maker):
    """★ 외부 호출이 없다. 필요한 봉은 전부 `ohlcv_cache`에 있다 (3.9)."""
    # as_of=100, 이후 21봉: +1%, ... 20봉 뒤 = 120
    closes = [100.0] + [101.0, 102.0, 103.0, 104.0, 105.0] + [110.0] * 14 + [120.0]
    await seed_bars(maker, closes)
    signal_id = await seed_signal(maker)

    async with maker() as session:
        report = await evaluate(session)

    async with maker() as session:
        row = await session.get(SignalRow, signal_id)

    assert row.fwd_base == 100.0
    assert row.fwd_1 == pytest.approx(0.01)  # 1봉 뒤 101
    assert row.fwd_5 == pytest.approx(0.05)  # 5봉 뒤 105
    assert row.fwd_20 == pytest.approx(0.20)  # 20봉 뒤 120
    assert report.filled == {1: 1, 5: 1, 20: 1}


async def test_the_as_of_bar_is_the_denominator_not_the_first_horizon(maker):
    """`as_of`를 포함해서 세면 1봉 뒤 수익률이 언제나 0이 된다."""
    await seed_bars(maker, [100.0, 110.0])
    signal_id = await seed_signal(maker)

    async with maker() as session:
        await evaluate(session)
    async with maker() as session:
        row = await session.get(SignalRow, signal_id)

    assert row.fwd_1 == pytest.approx(0.10)  # 0이 아니다


async def test_losses_are_recorded_as_negative(maker):
    """맞은 것만 기억하는 것을 막는 장치다. 손실이 손실로 남아야 한다."""
    await seed_bars(maker, [100.0, 90.0])
    signal_id = await seed_signal(maker)

    async with maker() as session:
        await evaluate(session)
    async with maker() as session:
        row = await session.get(SignalRow, signal_id)

    assert row.fwd_1 == pytest.approx(-0.10)


# ------------------------------------------------------- ★ 없는 숫자를 지어내지 않는다
async def test_horizons_without_enough_bars_stay_null(maker):
    """★ 0으로 채우면 최근 신호가 전부 "수익률 0%"로 잡혀 통계가 조용히 희석된다."""
    await seed_bars(maker, [100.0, 101.0, 102.0])  # 2봉만 지났다
    signal_id = await seed_signal(maker)

    async with maker() as session:
        report = await evaluate(session)
    async with maker() as session:
        row = await session.get(SignalRow, signal_id)

    assert row.fwd_1 == pytest.approx(0.01)
    assert row.fwd_5 is None  # 아직 모른다
    assert row.fwd_20 is None
    assert report.pending == 1


async def test_a_signal_with_no_bars_at_all_is_counted_not_swallowed(maker):
    """★ 봉이 끊긴 종목을 조용히 빼면 **손실만 골라서 결측된다** (규칙 18).

    유니버스에서 밀리는 종목은 대개 내린 종목이기 때문이다. 그래서 세어서 드러낸다.
    """
    await seed_signal(maker, instrument="krx:999999", key="gone")

    async with maker() as session:
        report = await evaluate(session)

    assert report.missing_bars == ["krx:999999"]
    assert report.filled == {}


# --------------------------------------------------------------------- 재실행
async def test_evaluate_is_idempotent(maker):
    """다시 불러도 이미 채운 값을 건드리지 않는다."""
    await seed_bars(maker, [100.0, 110.0])
    signal_id = await seed_signal(maker)

    async with maker() as session:
        first = await evaluate(session)
    async with maker() as session:
        second = await evaluate(session)
    async with maker() as session:
        row = await session.get(SignalRow, signal_id)

    assert first.filled == {1: 1}
    assert second.filled == {}  # 새로 채운 것이 없다
    assert row.fwd_1 == pytest.approx(0.10)


async def test_later_bars_fill_the_longer_horizons(maker):
    """시간이 지나면 채워진다 — `pending`은 실패가 아니라 '아직'이다."""
    await seed_bars(maker, [100.0, 101.0])
    signal_id = await seed_signal(maker)
    async with maker() as session:
        await evaluate(session)

    # 봉이 더 쌓였다
    await seed_bars(maker, [100.0] + [101.0, 102.0, 103.0, 104.0, 105.0])
    async with maker() as session:
        report = await evaluate(session)
    async with maker() as session:
        row = await session.get(SignalRow, signal_id)

    assert report.filled == {5: 1}
    assert row.fwd_5 == pytest.approx(0.05)
    assert row.fwd_20 is None


async def test_fully_filled_signals_are_not_rescanned(maker):
    """전량을 매번 다시 훑으면 신호가 쌓인 뒤 평가가 느려진다."""
    closes = [100.0] + [101.0] * max(HORIZONS)
    await seed_bars(maker, closes)
    await seed_signal(maker)

    async with maker() as session:
        await evaluate(session)
    async with maker() as session:
        again = await evaluate(session)

    assert again.scanned == 0
