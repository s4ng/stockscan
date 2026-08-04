"""단일 종목 리플레이 — 날짜별로 전략을 다시 돌린다 (ARCHITECTURE.md 12.7).

하는 일은 한 줄로 적힌다: **`start`부터 하루씩, 그날까지의 봉만 잘라서 전략에
넣는다.** 그날 전략이 이 종목을 골랐으면 그 세션에 표시가 남는다.

**미래 참조를 막는 방식이 이 파일의 핵심이다.** 전략에게 넘기는 DataFrame은
`frame.iloc[:i+1]` — 그날 마감된 봉까지다. 뒤 구간은 존재하지 않으므로
`shift(-1)`도 `bfill`도 볼 것이 없다. 규칙 3이 AST로 잡는 것과 같은 사고를
**데이터 쪽에서** 한 번 더 막는 셈이고, 둘 다 통과해야 신뢰할 수 있다.

⚠️ **이 리플레이가 답하지 못하는 것 하나** — 횡단면 컷이다. 전략은
`compute`(종목별) → `rank`(횡단면) → `select`(컷) 순서인데, 종목이 하나뿐이면
"그날 조건을 통과한 종목들 중 상위 10개"의 후보가 1개라 **항상 통과한다.**
그래서 여기서 나온 표시는 **"조건을 만족한 날"**이지 "그날 실제로 뽑혔을 날"이
아니다. 이 사실은 `cut_applied=False`로 결과에 실려 리포트 상단에 그대로 적힌다 —
적지 않으면 사용자가 이걸 실제 신호로 읽고, 그 순간 백테스트가 거짓말이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import date, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.engine.context import NodeLogger, RunContext
from app.engine.types import Bundle, Item
from app.market.calendar import MarketCalendar, session_date
from app.market.instrument import InstrumentRef
from app.strategies.base import (
    PERCENTILE_FEATURE,
    RANK_FEATURE,
    RANK_POOL_FEATURE,
    UNIVERSE_SIZE_FEATURE,
    Strategy,
)
from app.strategies.stages import eligible_items, run_stages

#: 종목이 하나뿐이라 **뜻이 없어지는** 피처들. 리포트에서 뺀다.
#:
#: "순위 1 / 1 · 상위 100%"는 정보가 아니라 오해다 — 유니버스가 1이라 항상 그렇게
#: 나오는데, 화면에서는 "1등으로 뽑혔다"로 읽힌다.
VACUOUS_FEATURES = (
    RANK_FEATURE,
    PERCENTILE_FEATURE,
    UNIVERSE_SIZE_FEATURE,
    RANK_POOL_FEATURE,
    "universe_scanned",
)


@dataclass(frozen=True)
class ReplayDay:
    """하루치 판정 결과."""

    as_of: datetime
    """판정 기준이 된 **마감된** 봉의 종료 시각 (UTC)."""

    session: date
    """세션 날짜. 차트의 x축과 마커가 같은 값을 쓴다 (`session_date`)."""

    close: float
    signal: bool
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "session": self.session.isoformat(),
            "close": self.close,
            "signal": self.signal,
            "features": {k: _jsonable(v) for k, v in self.features.items()},
        }


@dataclass(frozen=True)
class ReplayResult:
    instrument: InstrumentRef
    timeframe: str
    strategy_id: str
    strategy_sha256: str
    params: dict[str, Any]
    startup_candles: int
    start: date
    end: date
    days: list[ReplayDay]
    bars: list[dict[str, Any]]
    """차트에 그릴 봉. 워밍업 구간도 넣는다 — 돌파의 배경이 안 보이면 마커만 뜬다."""

    skipped_warmup: int = 0
    """봉이 `startup_candles`에 못 미쳐 판정하지 못한 날."""

    cut_applied: bool = False
    """횡단면 컷이 실제로 걸렸는가. **단일 종목 리플레이에서는 항상 False다.**"""

    @property
    def signal_days(self) -> list[ReplayDay]:
        return [d for d in self.days if d.signal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument.key,
            "display_name": self.instrument.display_name,
            "timeframe": self.timeframe,
            "strategy_id": self.strategy_id,
            "strategy_sha256": self.strategy_sha256,
            "params": self.params,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "judged_days": len(self.days),
            "signal_count": len(self.signal_days),
            "skipped_warmup": self.skipped_warmup,
            "cut_applied": self.cut_applied,
            "signals": [d.to_dict() for d in self.signal_days],
        }


def replay(
    *,
    frame: pd.DataFrame,
    instrument: InstrumentRef,
    timeframe: str,
    strategy: Strategy,
    params: BaseModel,
    ctx: RunContext,
    calendar: MarketCalendar,
    start: date,
    end: date,
    adjusted: bool = True,
    strategy_sha256: str = "",
) -> ReplayResult:
    """`start`~`end`의 각 세션에 대해 그날까지의 봉으로 전략을 돌린다."""
    sessions = [session_date(ts, calendar) for ts in frame.index]
    days: list[ReplayDay] = []
    skipped = 0

    for i, session in enumerate(sessions):
        if session < start or session > end:
            continue

        window = frame.iloc[: i + 1]
        as_of = _as_datetime(frame.index[i])

        # ★ 미래 참조 방어선. 슬라이스가 맞는지 여기서 한 번 더 확인한다 —
        #   인덱스가 정렬돼 있지 않으면 조용히 미래 봉이 창에 들어온다.
        last = _as_datetime(window.index[-1])
        if last != as_of or _as_datetime(window.index.max()) > as_of:
            raise LookAheadInReplayError(
                f"{instrument.key} {session}: 판정 창에 미래 봉이 들어 있습니다 "
                f"(창의 마지막 {last.isoformat()} vs 기준 {as_of.isoformat()}). "
                f"봉 인덱스가 시간순으로 정렬돼 있는지 확인하세요."
            )

        # 그날의 실행 컨텍스트. `ctx.now`가 그날로 고정돼야 전략이 오늘을 알 수 없다
        # (규칙 1). 로거는 날마다 새로 만든다 — 250일치를 모으면 로그가 터진다.
        day_ctx = dc_replace(ctx, now=as_of, log=NodeLogger())

        item = Item(
            instrument=instrument,
            timeframe=timeframe,
            as_of=as_of,
            ohlcv=window,
            meta={"adjusted": adjusted, "source": "backtest"},
        )
        bundle = Bundle([item])

        # 워밍업 판정도 `run`과 같은 함수를 쓴다 — 여기서 따로 세면 제외 기준이 갈린다.
        if not eligible_items(bundle, strategy, True, day_ctx):
            skipped += 1
            continue

        stages = run_stages(strategy, bundle, params, day_ctx)
        computed = stages.computed.items[0]
        days.append(
            ReplayDay(
                as_of=as_of,
                session=session,
                close=float(window["close"].iloc[-1]),
                signal=not stages.selected.is_empty,
                features=_display_features(computed.features),
            )
        )

    return ReplayResult(
        instrument=instrument,
        timeframe=timeframe,
        strategy_id=strategy.id,
        strategy_sha256=strategy_sha256,
        params=params.model_dump(mode="json"),
        startup_candles=strategy.startup_candles,
        start=start,
        end=end,
        days=days,
        bars=_chart_bars(frame, sessions),
        skipped_warmup=skipped,
    )


class LookAheadInReplayError(RuntimeError):
    """리플레이 창에 미래 봉이 섞였을 때. 폴백하지 않고 그대로 터뜨린다 (규칙 2)."""


# --------------------------------------------------------------------------- 내부
def _chart_bars(frame: pd.DataFrame, sessions: list[date]) -> list[dict[str, Any]]:
    """lightweight-charts가 먹는 모양으로 봉을 옮긴다.

    `time`은 **세션 날짜**다 (`session_date`). 봉과 마커가 같은 함수를 써야
    마커가 제 봉 위에 앉는다 — 마감 시각을 그대로 쓰면 코인이 하루 밀린다.
    """
    return [
        {
            "time": session.isoformat(),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for session, row in zip(sessions, frame.itertuples(), strict=True)
    ]


def _display_features(features: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in features.items() if k not in VACUOUS_FEATURES}


def _as_datetime(value: Any) -> datetime:
    ts = pd.Timestamp(value)
    return ts.to_pydatetime()


def _jsonable(value: Any) -> Any:
    if isinstance(value, float | int | str | bool) or value is None:
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)
