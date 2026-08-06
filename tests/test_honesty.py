"""★ 엔진의 정직성 — 백테스트·채점 엔진에는 정답을 알려 줄 오라클이 없다 (4.8).

결과가 그럴듯하면 맞는 줄 안다. 그래서 **전략 성과가 아니라 엔진의 정직성**을 재는
테스트를 여기 박는다. 여기가 빨개지면 전략이 아니라 **구현이 틀린 것**이다.

| 테스트 | 기대 | 깨지면 |
| :--- | :--- | :--- |
| **난수 신호** | hit rate가 기저율과 일치 | ⚠️ **미래 참조가 있다.** 가장 강력한 방어선 |
| **전량 매수** | forward return이 유니버스 평균과 일치 | 수익률 계산·정렬·조인 버그 |
| **신호 1일 밀기** | 성과가 기저율 쪽으로 떨어짐 | 안 떨어지면 엔진이 새고 있다 |
| **상장폐지 포함** | 폐지 종목이 결측으로 드러남 | 서바이버십이 데이터 레이어에 있다 |

★ **난수 신호가 왜 가장 강력한가.** `strategy check`의 AST 검사는 `shift(-1)`처럼
**눈에 보이는** 미래 참조만 잡는다. 그런데 미래는 훨씬 은근한 경로로 샌다 — 조인을
잘못해서 다음 날 종가가 붙거나, 인덱스가 한 칸 밀리거나, 유니버스를 오늘 기준으로
뽑거나. 그 모든 경로를 **결과로** 잡는 것이 이 테스트다: 신호에 정보가 0인데
hit rate가 기저율을 넘으면, 그 정보는 전략이 아니라 **파이프라인 어딘가에서 새어
들어온 미래**밖에 될 수 없다. 논리가 닫혀 있어 우회가 불가능하다.
"""

from __future__ import annotations

import random
import statistics
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.evaluate import evaluate
from app.market.instrument import InstrumentRef
from app.storage import ohlcv_cache
from app.storage.models import Base, SignalRow

pytestmark = pytest.mark.asyncio

START = datetime(2026, 1, 5, 6, 30, tzinfo=UTC)

#: 표본. 이보다 적으면 기저율과의 차이가 잡음에 묻힌다.
SYMBOLS = 40
SESSIONS = 60

#: 난수 hit rate가 기저율에서 벗어나도 되는 폭.
#:
#: ⚠️ **이 값을 키우는 것으로 실패를 넘기지 않는다.** 여유가 필요해 보이면 표본을
#: 늘려야 한다 — 허용 오차를 늘리는 것은 방어선을 무르게 만드는 것이고, 그게
#: 정확히 이 테스트가 막으려는 종류의 타협이다.
TOLERANCE = 0.12


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _walk(seed: int, drift: float) -> list[float]:
    """결정적 난수 보행. `drift`가 그 종목의 기대 수익률을 정한다."""
    rng = random.Random(seed)
    price = 100.0
    out = [price]
    for _ in range(SESSIONS - 1):
        price *= 1 + drift + rng.gauss(0, 0.02)
        out.append(price)
    return out


async def seed_market(maker, *, drift: float = 0.0) -> list[InstrumentRef]:
    """가짜 시장 하나. 종목마다 다른 시드로 걷는다."""
    refs = [InstrumentRef.parse(f"nasdaq:S{i:03d}") for i in range(SYMBOLS)]
    index = pd.DatetimeIndex(
        [START + timedelta(days=i) for i in range(SESSIONS)], tz="UTC", name="time"
    )
    async with maker() as session:
        for i, ref in enumerate(refs):
            closes = _walk(seed=i, drift=drift)
            frame = pd.DataFrame(
                {
                    "open": closes,
                    "high": closes,
                    "low": closes,
                    "close": closes,
                    "volume": [1.0] * SESSIONS,
                },
                index=index,
            )
            await ohlcv_cache.write_bars(
                session, ref, "1d", frame, adjusted=True, source_id="test"
            )
        await session.commit()
    return refs


async def emit(maker, picks: list[tuple[InstrumentRef, datetime]]) -> None:
    async with maker() as session:
        for n, (ref, as_of) in enumerate(picks):
            session.add(
                SignalRow(
                    run_id="r",
                    pipeline_id="p",
                    node_id="persist",
                    dedup_key=f"k{n}",
                    instrument=ref.key,
                    venue=ref.venue,
                    timeframe="1d",
                    as_of=as_of,
                )
            )
        await session.commit()


async def hit_rate(maker, horizon: str = "fwd_20") -> tuple[float, int]:
    async with maker() as session:
        await evaluate(session, limit=5000)
    async with maker() as session:
        rows = list((await session.scalars(select(SignalRow))).all())
    values = [getattr(r, horizon) for r in rows]
    values = [v for v in values if v is not None]
    assert values, "채워진 신호가 없습니다 — 봉이 모자란 것이지 엔진 문제가 아닐 수 있습니다"
    return sum(1 for v in values if v > 0) / len(values), len(values)


def base_rate(closes_by_symbol: list[list[float]], sessions: list[int], horizon: int) -> float:
    """유니버스 전체의 N봉 승률 — 비교 기준선."""
    wins = total = 0
    for closes in closes_by_symbol:
        for s in sessions:
            if s + horizon >= len(closes):
                continue
            total += 1
            if closes[s + horizon] / closes[s] - 1 > 0:
                wins += 1
    return wins / total


# --------------------------------------------------------------- ★ 난수 신호
async def test_random_signals_match_the_base_rate(maker):
    """★ **가장 값싸고 강력한 방어선.**

    난수로 고른 신호는 정보가 0이므로 hit rate가 유니버스 기저율과 같아야 한다.
    **넘으면 전략 문제가 아니라 미래 참조가 있는 것이다** — 정보가 없는데 맞을
    수는 없으므로, 그 정보는 파이프라인 어딘가에서 새어 들어온 것이다.
    """
    refs = await seed_market(maker, drift=0.001)
    rng = random.Random(20260806)
    sessions = list(range(0, SESSIONS - 21))

    picks = [
        (rng.choice(refs), START + timedelta(days=rng.choice(sessions)))
        for _ in range(200)
    ]
    await emit(maker, picks)

    measured, count = await hit_rate(maker)
    expected = base_rate([_walk(i, 0.001) for i in range(SYMBOLS)], sessions, 20)

    assert count >= 100, "표본이 적으면 이 비교가 잡음에 묻힌다"
    assert abs(measured - expected) < TOLERANCE, (
        f"난수 신호의 승률({measured:.1%})이 기저율({expected:.1%})에서 "
        f"{abs(measured - expected):.1%} 벗어났습니다. "
        f"⚠️ 전략 문제가 아니라 **미래 참조**를 의심하세요 (4.8)."
    )


# ------------------------------------------------------------- ★ 전량 매수
async def test_buying_everything_matches_the_universe_average(maker):
    """전량을 신호로 내면 평균 수익률이 유니버스 평균과 같아야 한다.

    어긋나면 수익률 계산·정렬·조인 어딘가가 틀린 것이다.
    """
    refs = await seed_market(maker, drift=0.0)
    as_of = START + timedelta(days=10)
    await emit(maker, [(ref, as_of) for ref in refs])

    async with maker() as session:
        await evaluate(session, limit=5000)
    async with maker() as session:
        rows = list((await session.scalars(select(SignalRow))).all())

    measured = statistics.mean([r.fwd_20 for r in rows if r.fwd_20 is not None])
    expected = statistics.mean(
        [_walk(i, 0.0)[30] / _walk(i, 0.0)[10] - 1 for i in range(SYMBOLS)]
    )
    assert measured == pytest.approx(expected, abs=1e-9)


# --------------------------------------------------------- ★ as_of는 분모다
async def test_shifting_the_signal_changes_the_result(maker):
    """신호를 하루 밀면 결과가 달라져야 한다.

    안 달라지면 `as_of`가 실제로 쓰이지 않는다는 뜻이고, 그러면 사후 수익률이
    "그 신호의" 성적이 아니게 된다.
    """
    refs = await seed_market(maker, drift=0.0)
    ref = refs[0]

    await emit(maker, [(ref, START + timedelta(days=10))])
    async with maker() as session:
        await evaluate(session, limit=10)
    async with maker() as session:
        first = (await session.get(SignalRow, 1)).fwd_20

    async with maker() as session:
        session.add(
            SignalRow(
                run_id="r",
                pipeline_id="p",
                node_id="persist",
                dedup_key="shifted",
                instrument=ref.key,
                venue=ref.venue,
                timeframe="1d",
                as_of=START + timedelta(days=11),
            )
        )
        await session.commit()
    async with maker() as session:
        await evaluate(session, limit=10)
    async with maker() as session:
        shifted = (await session.get(SignalRow, 2)).fwd_20

    assert first != shifted


# ------------------------------------------------- ★ 서바이버십 (봉이 끊긴 종목)
async def test_a_delisted_instrument_surfaces_as_missing_not_as_success(maker):
    """★ 봉이 끊긴 종목을 조용히 빼면 **손실만 골라서 결측된다** (규칙 18 / 4.8).

    유니버스에서 밀리는 종목은 대개 내린 종목이기 때문이다. 결측은 성공이 아니라
    결측으로 드러나야 한다.
    """
    refs = await seed_market(maker, drift=0.0)
    gone = InstrumentRef.parse("nasdaq:DEAD")  # 봉이 하나도 없다
    await emit(maker, [(refs[0], START + timedelta(days=10)), (gone, START + timedelta(days=10))])

    async with maker() as session:
        report = await evaluate(session, limit=10)

    assert report.missing_bars == ["nasdaq:DEAD"]
    assert report.filled  # 나머지는 정상적으로 채워졌다
