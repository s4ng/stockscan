"""전략 3단계 실행 — `compute` → `rank` → `select` (ARCHITECTURE.md 4.2).

**이 순서를 아는 곳은 여기 하나뿐이어야 한다.** 부르는 쪽이 둘이기 때문이다.

  - `StrategyRunnerNode` — 파이프라인 실행 (`run`)
  - `app/backtest/replay.py` — 날짜별 리플레이 (`backtest`)

둘이 각자 순서를 적으면 언젠가 한쪽만 바뀌고, 그날부터 **백테스트가 실거래와
다른 코드를 돌면서 같은 것을 재현했다고 말한다.** 규칙 1이 `ctx.now`로 묶어 둔
것을 단계 순서에서 놓치는 셈이라, 같은 함수를 쓰게 만들어 원천 차단한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.strategies.base import Strategy, StrategyError


@dataclass(frozen=True)
class StageResult:
    """단계별 산출물. `ranked`가 있어야 "왜 떨어졌나"를 되짚을 수 있다."""

    computed: Bundle
    ranked: Bundle
    selected: Bundle


def run_stages(
    strategy: Strategy, bundle: Bundle, params: BaseModel, ctx: RunContext
) -> StageResult:
    """`compute`(종목별) → `rank`(횡단면) → `select`(컷)를 순서대로 돌린다."""
    computed = [compute_one(strategy, item, params, ctx) for item in bundle]
    ranked = strategy.rank(Bundle(computed, dict(bundle.context)), params, ctx)
    selected = strategy.select(ranked, params, ctx)
    return StageResult(
        computed=Bundle(computed, dict(bundle.context)), ranked=ranked, selected=selected
    )


def compute_one(strategy: Strategy, item: Item, params: BaseModel, ctx: RunContext) -> Item:
    result = strategy.compute(item, params, ctx)
    if not isinstance(result, Item):
        raise StrategyError(
            f"[{strategy.id}] compute가 Item을 돌려주지 않았습니다 ({type(result).__name__}). "
            f"`return item.with_features(...)` 형태여야 합니다."
        )
    return result


def eligible_items(
    bundle: Bundle, strategy: Strategy, require_warmup: bool, ctx: RunContext
) -> list[Item]:
    """타임프레임과 워밍업 조건을 만족하는 item만 남긴다. 제외는 전부 로그로 남는다."""
    wrong_tf: list[str] = []
    short: list[str] = []
    kept: list[Item] = []

    for item in bundle:
        if item.timeframe != strategy.timeframe:
            wrong_tf.append(f"{item.instrument.key}({item.timeframe})")
            continue
        if require_warmup and len(item.ohlcv) < strategy.startup_candles:
            short.append(f"{item.instrument.key}({len(item.ohlcv)}봉)")
            continue
        kept.append(item)

    if wrong_tf:
        ctx.log.warning(
            f"{strategy.id}의 타임프레임은 {strategy.timeframe}입니다. "
            f"다른 봉이라 제외: {', '.join(wrong_tf[:20])}. "
            f"Market Data 노드의 timeframe을 맞추세요."
        )
    if short:
        ctx.log.warning(
            f"워밍업 봉이 부족해 제외 (필요 {strategy.startup_candles}봉): "
            f"{', '.join(short[:20])}"
            f"{f' 외 {len(short) - 20}건' if len(short) > 20 else ''}. "
            f"Market Data의 lookback을 늘리세요."
        )
    return kept
