"""성적표 — **알림을 믿을 수 있게 만드는 장치** (ARCHITECTURE.md 4.8).

이 프로그램은 매수 후보를 뽑아 텔레그램으로 보낸다. 그런데 **스크리너는 내버려 두면
자신감 기계가 된다** — 사람은 맞은 종목만 기억하기 때문이다. 그래서 낸 신호를 전부
채점해서, 알림을 받는 순간 "이걸 얼마나 믿어야 하나"가 같이 오게 한다.

★ **숫자 하나하나가 거짓말하지 않도록 짝을 붙인다.**

| 숫자 | 혼자 두면 | 그래서 함께 낸다 |
| :--- | :--- | :--- |
| 승률 | 상승장에선 아무거나 찍어도 60%다 | **기저율** — 같은 기간 유니버스 전체의 승률 |
| 수익률 | 시장이 좋았던 것을 전략의 공으로 돌린다 | **벤치마크 대비 초과수익** |
| 평균 | 한 종목이 5배 가면 거짓말을 한다 | **중앙값과 사분위** |

★ **채점 대상은 "낸 신호 전부"다.** 사용자가 실제로 무엇을 샀는지는 묻지 않고 **전부
샀다고 가정**한다. 한때 `[샀다/안 샀다]` 응답을 받아 산 것과 무시한 것을 갈라 비교했지만
2026-08-07에 걷어냈다 — 응답을 빠짐없이 해야만 성립하는 비교였는데, 산 것만 답하고
무시한 것은 넘기면 **자기가 고른 분할**이 되어 결론이 아첨하는 쪽으로 기울었다.
가정을 고정해 두면 적어도 **분모가 정직하다.**

★ **기저율 비교가 난수 신호 검증의 실전판이다** (4.8 "엔진을 검증하는 법").
회귀 테스트는 난수 신호의 승률이 기저율과 같은지를 보지만, 여기서는 **실제 신호가
기저율을 넘는지**를 본다. 넘지 못하면 그 전략은 종목을 고르지 못하는 것이고,
지나치게 넘으면 미래 참조를 의심할 자리다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluate import HORIZONS
from app.storage.models import OhlcvCacheRow, SignalRow

#: 성적표가 다루는 기본 구간. 한 달이면 일일 알림과 다른 층위가 된다.
DEFAULT_DAYS = 30

#: 이보다 적으면 숫자를 내지 않는다.
#:
#: ★ **표본이 적을 때 승률을 보여 주는 것이 가장 위험하다** — 3건 중 2건이 맞으면
#: "승률 67%"가 되고, 그 숫자는 아무것도 뜻하지 않는데 사람은 그것을 기억한다.
#: 없는 숫자를 지어내지 않는 것과 같은 원칙이다 (12.3).
MIN_SAMPLE = 5


@dataclass
class Horizon:
    """지평선 하나의 성적."""

    bars: int
    count: int = 0
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    hit_rate: float | None = None
    excess_median: float | None = None
    """벤치마크 대비 초과수익의 중앙값. 벤치마크가 없으면 None."""

    base_rate: float | None = None
    """★ 같은 기간 **유니버스 전체**의 승률. 승률을 해석할 유일한 기준선."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bars": self.bars,
            "count": self.count,
            "median": self.median,
            "p25": self.p25,
            "p75": self.p75,
            "hit_rate": self.hit_rate,
            "excess_median": self.excess_median,
            "base_rate": self.base_rate,
        }


@dataclass
class Scorecard:
    strategy: str = ""
    days: int = DEFAULT_DAYS
    since: datetime | None = None
    signals: int = 0
    evaluated: int = 0
    """사후 수익률이 하나라도 채워진 신호 수. `signals`와 다르면 아직 기다리는 중이다."""

    horizons: list[Horizon] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "days": self.days,
            "since": self.since.isoformat() if self.since else None,
            "signals": self.signals,
            "evaluated": self.evaluated,
            "horizons": [h.to_dict() for h in self.horizons],
            "notes": self.notes,
        }


async def build(
    session: AsyncSession,
    *,
    now: datetime,
    days: int = DEFAULT_DAYS,
    strategy: str | None = None,
) -> Scorecard:
    """구간의 성적표를 만든다. **읽기만 한다.**"""
    since = now - timedelta(days=days)
    card = Scorecard(strategy=strategy or "(전체)", days=days, since=since)

    stmt = select(SignalRow).where(SignalRow.as_of >= since)
    if strategy:
        stmt = stmt.where(SignalRow.strategy_id == strategy)
    rows = list((await session.scalars(stmt)).all())

    card.signals = len(rows)
    card.evaluated = sum(
        1 for r in rows if any(getattr(r, f"fwd_{n}") is not None for n in HORIZONS)
    )
    if not rows:
        card.notes.append("이 구간에 신호가 없습니다.")
        return card

    for bars in HORIZONS:
        card.horizons.append(await _horizon(session, rows, bars, since, now))

    _add_notes(card, rows)
    return card


# --------------------------------------------------------------------------- 내부
async def _horizon(
    session: AsyncSession,
    rows: list[SignalRow],
    bars: int,
    since: datetime,
    now: datetime,
) -> Horizon:
    values = [getattr(r, f"fwd_{bars}") for r in rows]
    values = [v for v in values if v is not None]
    horizon = Horizon(bars=bars, count=len(values))
    if len(values) < MIN_SAMPLE:
        return horizon

    horizon.median = statistics.median(values)
    quartiles = statistics.quantiles(values, n=4) if len(values) >= 4 else None
    if quartiles:
        horizon.p25, horizon.p75 = quartiles[0], quartiles[2]
    horizon.hit_rate = sum(1 for v in values if v > 0) / len(values)

    excess = [
        getattr(r, f"fwd_{bars}") - getattr(r, f"bench_{bars}")
        for r in rows
        if getattr(r, f"fwd_{bars}") is not None and getattr(r, f"bench_{bars}") is not None
    ]
    if excess:
        horizon.excess_median = statistics.median(excess)

    horizon.base_rate = await _base_rate(session, rows, bars, since, now)
    return horizon


async def _base_rate(
    session: AsyncSession,
    rows: list[SignalRow],
    bars: int,
    since: datetime,
    now: datetime,
) -> float | None:
    """★ 같은 기간 **캐시에 있는 전 종목**의 N봉 승률 — 승률을 해석할 기준선.

    이것이 없으면 "승률 64%"가 좋은 건지 나쁜 건지 알 수 없다. 상승장이면 아무거나
    찍어도 60%를 넘기 때문이다. **난수 신호 검증(4.8)의 실전판이다** — 회귀 테스트가
    "난수는 기저율과 같아야 한다"를 보는 반면, 여기서는 "실제 신호가 기저율을
    넘는가"를 본다.

    표본이 커질 수 있으므로 신호가 실제로 난 **세션 날짜**로만 잰다 — 전 기간
    전 종목을 세면 "그 기간의 시장"이 아니라 "캐시의 모양"을 재게 된다.
    """
    sessions = sorted({r.as_of for r in rows})
    if not sessions:
        return None

    wins = total = 0
    for as_of in sessions[:20]:  # 세션이 많아도 20개면 기준선으로 충분하다
        stmt = (
            select(OhlcvCacheRow.venue, OhlcvCacheRow.symbol, OhlcvCacheRow.close)
            .where(OhlcvCacheRow.bar_time == as_of, OhlcvCacheRow.timeframe == "1d")
        )
        base_rows = list((await session.execute(stmt)).all())
        for venue, symbol, base in base_rows:
            if not base or base <= 0:
                continue
            after = await session.scalar(
                select(OhlcvCacheRow.close)
                .where(
                    OhlcvCacheRow.venue == venue,
                    OhlcvCacheRow.symbol == symbol,
                    OhlcvCacheRow.timeframe == "1d",
                    OhlcvCacheRow.bar_time > as_of,
                )
                .order_by(OhlcvCacheRow.bar_time.asc())
                .offset(bars - 1)
                .limit(1)
            )
            if after is None:
                continue
            total += 1
            if after / base - 1 > 0:
                wins += 1
    return wins / total if total >= MIN_SAMPLE else None


def _add_notes(card: Scorecard, rows: list[SignalRow]) -> None:
    pending = card.signals - card.evaluated
    if pending:
        card.notes.append(
            f"{pending}건은 아직 봉이 모자라 채우지 못했습니다 (시간이 지나면 채워집니다)."
        )
    if any(h.count and h.count < MIN_SAMPLE for h in card.horizons):
        card.notes.append(
            f"표본이 {MIN_SAMPLE}건보다 적은 지평선은 숫자를 내지 않습니다 — "
            f"적은 표본의 승률은 아무것도 뜻하지 않는데 사람은 그것을 기억합니다."
        )
    if all(h.excess_median is None for h in card.horizons):
        card.notes.append(
            "벤치마크가 없어 초과수익을 내지 못했습니다. "
            "`stockscan ingest --commit`이 KOSPI·S&P500을 모읍니다."
        )


async def signal_count(session: AsyncSession, strategy: str, limit: int = 20) -> Horizon | None:
    """★ 일일 알림에 붙일 한 줄 — "이 전략 최근 N건의 성적".

    알림을 받는 순간 **"이걸 얼마나 믿어야 하나"가 같이 와야** 한다. 근거 없는
    명령만 오면 알림을 보지 않게 된다.
    """
    stmt = (
        select(SignalRow)
        .where(SignalRow.strategy_id == strategy, SignalRow.fwd_20.is_not(None))
        .order_by(SignalRow.as_of.desc())
        .limit(limit)
    )
    rows = list((await session.scalars(stmt)).all())
    values = [r.fwd_20 for r in rows if r.fwd_20 is not None]
    if len(values) < MIN_SAMPLE:
        return None

    horizon = Horizon(bars=20, count=len(values))
    horizon.median = statistics.median(values)
    horizon.hit_rate = sum(1 for v in values if v > 0) / len(values)
    excess = [
        r.fwd_20 - r.bench_20 for r in rows if r.fwd_20 is not None and r.bench_20 is not None
    ]
    if excess:
        horizon.excess_median = statistics.median(excess)
    return horizon


async def latest_as_of(session: AsyncSession) -> datetime | None:
    return await session.scalar(select(func.max(SignalRow.as_of)))


# ------------------------------------------------------------------- 텔레그램 문구
def _pct(value: float | None, sign: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%" if sign else f"{value * 100:.0f}%"


def render(card: Scorecard) -> str:
    """한 달에 한 번 오는 메시지. 일일 알림이 답하지 못하는 것에 답한다.

    숫자를 혼자 두지 않는다 — 승률 옆에는 기저율, 수익률 옆에는 초과수익.
    """
    lines = [f"📊 stockscan 성적표 — 최근 {card.days}일", ""]
    lines.append(f"{card.strategy} · 신호 {card.signals}건 (채점 완료 {card.evaluated}건)")

    if not card.signals:
        lines.append("")
        lines.append("이 구간에 신호가 없습니다.")
        return "\n".join(lines)

    lines.append("")
    for h in card.horizons:
        if h.count < MIN_SAMPLE:
            lines.append(f"{h.bars:>2}봉  표본 {h.count}건 — 숫자를 내기엔 적습니다")
            continue

        row = f"{h.bars:>2}봉  중앙값 {_pct(h.median)}"
        if h.p25 is not None and h.p75 is not None:
            row += f" (25~75% {_pct(h.p25)}~{_pct(h.p75)})"
        lines.append(row)

        # ★ 승률은 **반드시 기저율과 함께** 낸다. 혼자 두면 상승장에서 거짓말을 한다.
        hit = f"      승률 {_pct(h.hit_rate, sign=False)} ({h.count}건)"
        if h.base_rate is not None:
            delta = (h.hit_rate or 0) - h.base_rate
            hit += f" · 같은 기간 기저율 {_pct(h.base_rate, sign=False)}"
            hit += f" → {'+' if delta >= 0 else ''}{delta * 100:.0f}%p"
        else:
            hit += " · 기저율 — (캐시가 모자랍니다)"
        lines.append(hit)

        if h.excess_median is not None:
            lines.append(f"      벤치마크 대비 {_pct(h.excess_median)}")

    # 가정을 매번 적어 둔다. 안 적으면 이 숫자를 "내 계좌 수익률"로 읽게 되는데,
    # 실제로는 **낸 신호를 전부 샀다고 쳤을 때**의 값이다.
    lines.append("")
    lines.append(f"※ 신호 {card.signals}건을 전부 샀다고 가정한 값입니다.")

    for note in card.notes:
        lines.append(f"※ {note}")
    return "\n".join(lines)


def render_inline(horizon: Horizon | None) -> str:
    """일일 알림에 붙일 한 줄. 없으면 빈 문자열 (지어내지 않는다)."""
    if horizon is None:
        return ""
    text = (
        f"이 전략 최근 {horizon.count}건: 승률 {_pct(horizon.hit_rate, sign=False)}"
        f" · 20봉 중앙값 {_pct(horizon.median)}"
    )
    if horizon.excess_median is not None:
        text += f" (벤치마크 대비 {_pct(horizon.excess_median)})"
    return text
