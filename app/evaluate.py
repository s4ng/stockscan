"""Forward Return Evaluator — 신호가 난 **뒤에** 무슨 일이 있었나 (ARCHITECTURE.md 4.8).

★ **이 모듈이 이 프로젝트의 제품이다.** 신호가 났다는 사실만 쌓으면 스크리너는
자신감 기계가 된다 — 사람은 맞은 종목만 기억하기 때문이다. 그걸 막는 유일한 숫자가
여기서 나온다.

**청산을 가정하지 않는다.** 총수익률·MDD·샤프를 계산하려면 청산 규칙을 정해야 하고,
그 순간 체결 가정·수수료·세금이 줄줄이 딸려 와 Phase 5를 앞으로 끌어온다. 대신
"신호 이후 N봉 동안 값이 어떻게 됐나"만 잰다 — 가정이 하나도 필요 없다.

★ **외부 호출이 없다.** 필요한 봉은 전부 `ohlcv_cache`에 있다. Phase 2에서 캐시를
"성능 최적화가 아니라 데이터 자산"으로 못박아 둔 것이 여기서 값을 한다 — 반년 전
신호의 사후 성적을 지금 계산할 수 있는 이유다 (3.9 / 규칙 16).

⚠️ **아직 N봉이 안 지난 신호는 `NULL`로 남긴다.** 0으로 채우면 최근 신호가 전부
"수익률 0%"로 잡혀 통계가 조용히 희석된다. 없는 숫자를 지어내지 않는다 (12.3).

⚠️ **봉이 끊긴 종목은 채워지지 않는다.** 그 종목이 유니버스에서 밀려 수집이 멈췄다는
뜻이고, 하필 밀린 종목이 대개 내린 종목이라 **손실만 골라서 결측된다.** 규칙 18이
그것을 막는 장치이고, 이 모듈은 결측을 **세어서 드러낸다** — 조용히 빼지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.market.instrument import InstrumentRef, UnknownVenueError
from app.storage.models import OhlcvCacheRow, SignalRow

#: 몇 봉 뒤를 볼 것인가. **복수로 고정한다** — 하나만 골라 튜닝하기 시작하면 그것이
#: 파라미터 탐색이다 (1.3 / 11장 11번). 컬럼 이름과 짝을 이룬다.
HORIZONS: tuple[int, ...] = (1, 5, 20)

#: 한 번에 훑을 신호 수. 전량을 한 트랜잭션에 담으면 신호가 쌓인 뒤 메모리를 문다.
BATCH = 500


@dataclass
class EvalReport:
    """평가 결과. `--json`이 그대로 직렬화한다."""

    scanned: int = 0
    """이번에 훑은 신호 수."""

    filled: dict[int, int] = field(default_factory=dict)
    """지평선별로 **새로 채운** 건수."""

    pending: int = 0
    """아직 N봉이 안 지나 채우지 못한 신호. 시간이 지나면 채워진다 — 정상이다."""

    missing_bars: list[str] = field(default_factory=list)
    """★ 봉이 끊겨 채우지 못한 종목. **조용히 빼지 않고 드러낸다** (규칙 18)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "filled": {f"fwd_{n}": c for n, c in sorted(self.filled.items())},
            "pending": self.pending,
            "missing_bars": self.missing_bars,
        }


async def evaluate(session: AsyncSession, *, limit: int = BATCH) -> EvalReport:
    """채울 수 있는 신호의 사후 수익률을 채운다. **외부 호출 없음.**

    다시 불러도 안전하다 — 이미 채운 값은 건드리지 않고, 못 채운 것만 다시 본다.
    """
    report = EvalReport()
    rows = await _unfilled(session, limit)
    report.scanned = len(rows)
    if not rows:
        return report

    missing: set[str] = set()
    for row in rows:
        try:
            instrument = InstrumentRef.parse(row.instrument)
        except (ValueError, UnknownVenueError):
            continue  # 옛 표기. 지금 스키마로는 봉을 찾을 수 없다

        base = await _close_at(session, instrument, row.timeframe, row.as_of)
        if base is None or base <= 0:
            missing.add(row.instrument)
            continue

        forward = await _closes_after(session, instrument, row.timeframe, row.as_of)
        row.fwd_base = base
        for horizon in HORIZONS:
            column = f"fwd_{horizon}"
            if getattr(row, column) is not None:
                continue
            if len(forward) < horizon:
                continue
            setattr(row, column, forward[horizon - 1] / base - 1)
            report.filled[horizon] = report.filled.get(horizon, 0) + 1

        row.fwd_evaluated_at = datetime.now(UTC)
        # **아직 비어 있는 지평선이 하나라도 있으면 기다리는 중이다.** 일부만 채운
        # 신호도 여기 들어간다 — "다 됐다"와 "1봉만 됐다"를 같게 세면 성적표가
        # 몇 건 위에 서 있는지 알 수 없다.
        if any(getattr(row, f"fwd_{n}") is None for n in HORIZONS):
            report.pending += 1

    await session.commit()
    report.missing_bars = sorted(missing)
    return report


# --------------------------------------------------------------------------- 내부
async def _unfilled(session: AsyncSession, limit: int) -> list[SignalRow]:
    """아직 다 채우지 못한 신호. 오래된 것부터 — 그쪽이 채워질 가능성이 높다."""
    conditions = [getattr(SignalRow, f"fwd_{n}").is_(None) for n in HORIZONS]
    stmt = (
        select(SignalRow)
        .where(or_(*conditions))
        .order_by(SignalRow.as_of.asc(), SignalRow.id.asc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def _close_at(
    session: AsyncSession, instrument: InstrumentRef, timeframe: str, as_of: datetime
) -> float | None:
    """`as_of` 봉의 종가 — 수익률의 분모.

    **신호의 `meta`에 적힌 값을 쓰지 않는다.** 분자(뒤의 종가)와 분모가 같은 소스에서
    나와야 수정주가 정책이 바뀌어도 비율이 어긋나지 않는다 (3.8).
    """
    stmt = _bars(instrument, timeframe).where(OhlcvCacheRow.bar_time == as_of)
    return await session.scalar(stmt.with_only_columns(OhlcvCacheRow.close))


async def _closes_after(
    session: AsyncSession, instrument: InstrumentRef, timeframe: str, as_of: datetime
) -> list[float]:
    """`as_of` **뒤**의 종가를 오름차순으로. 최대 지평선까지만 읽는다.

    ⚠️ `as_of`를 포함하지 않는다 — 포함하면 1봉 뒤가 신호 당일이 되어 수익률이
    언제나 0이 된다.
    """
    stmt = (
        _bars(instrument, timeframe)
        .where(OhlcvCacheRow.bar_time > as_of)
        .order_by(OhlcvCacheRow.bar_time.asc())
        .limit(max(HORIZONS))
        .with_only_columns(OhlcvCacheRow.close)
    )
    return [float(c) for c in (await session.scalars(stmt)).all()]


def _bars(instrument: InstrumentRef, timeframe: str):
    """★ `adjusted`를 조건에 넣지 않는다.

    캐시 키에는 들어가지만(규칙 8) 여기서는 **신호 당시 어느 쪽으로 받았는지 모른다.**
    조건으로 걸면 정책이 바뀐 뒤의 신호가 통째로 결측되고, 그 결측이 조용하다.
    분모와 분자를 같은 조회로 뽑으므로 섞여도 비율은 같은 계열 안에서 나온다.
    """
    return select(OhlcvCacheRow).where(
        OhlcvCacheRow.venue == instrument.venue,
        OhlcvCacheRow.symbol == instrument.symbol,
        OhlcvCacheRow.timeframe == timeframe,
    )
