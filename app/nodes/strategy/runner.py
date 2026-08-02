"""Strategy Runner — 전략 클래스를 파이프라인에 꽂는 노드 (ARCHITECTURE.md 5장).

v0.4에서 지표 노드 체인이 하던 일이 **이 노드 하나**로 접힌다. 노드는 배선일 뿐,
판단은 전부 전략 클래스 안에 있다. 이 노드가 하는 일은 셋이다.

  1. 전략을 불러오고 **소스 해시를 대조**한다 (4.7) — 파일이 바뀌었으면 경고한다
  2. `compute`(시계열) → `rank`(횡단면) → `select`(컷) 순서를 지킨다 (4.2)
  3. `rank` 결과 상위 N개를 **node_runs에 남긴다** (4.9) — 전략이 한 덩어리가 되면서
     중간 판단이 노드 경계에 드러나지 않으므로, 이 스냅샷이 없으면
     "왜 이 종목이 뽑혔는가"를 잃는다
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.nodes.base import BaseNode, NodeError
from app.nodes.registry import register
from app.schemas.pipeline import MAIN
from app.strategies.base import RANK_FEATURE, Strategy, StrategyError
from app.strategies.registry import LoadedStrategy, load_strategy

#: node_runs에 남길 랭킹 스냅샷의 크기. 전부 남기면 유니버스 500종목에서 로그가 터진다.
RANK_SNAPSHOT_SIZE = 20


class StrategyRunnerParams(BaseModel):
    strategy_id: str = Field(
        description="strategies/<id>.py 의 파일 이름. 예: cross_momentum_12_1",
        min_length=1,
    )
    strategy_sha256: str = Field(
        default="",
        description="저장 시점의 전략 소스 해시. 비어 있지 않은데 현재 파일과 다르면 경고합니다.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="전략의 Params 모델에 넘길 값"
    )
    require_startup_candles: bool = Field(
        default=True,
        description="봉이 startup_candles보다 적은 종목을 제외합니다 (지표 워밍업 부족)",
    )


@register
class StrategyRunnerNode(BaseNode):
    type = "strategyRunner"
    display_name = "Strategy Runner"
    category = "strategy"
    description = "전략 클래스의 compute → rank → select를 순서대로 실행합니다."
    ParamsModel = StrategyRunnerParams
    inputs = (MAIN,)
    outputs = (MAIN,)

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: StrategyRunnerParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        bundle = inputs.get(MAIN, Bundle.empty())
        loaded = _load(params.strategy_id)
        strategy = loaded.strategy
        _warn_on_hash_drift(loaded, params.strategy_sha256, ctx)
        strategy_params = _parse_params(strategy, params.params)

        eligible = _eligible_items(bundle, strategy, params.require_startup_candles, ctx)
        if not eligible:
            # 빈 Bundle도 정상 출력이다 (4.1). 실패로 만들지 않는다.
            ctx.log.info("판정할 종목이 없습니다")
            return {MAIN: bundle.replace_items([])}

        computed = [_compute_one(strategy, item, strategy_params, ctx) for item in eligible]
        ranked = strategy.rank(Bundle(computed, dict(bundle.context)), strategy_params, ctx)
        selected = strategy.select(ranked, strategy_params, ctx)

        ctx.log.info(
            f"{strategy.id}: {len(bundle)}개 입력 → {len(ranked)}개 랭킹 → {len(selected)}개 선정"
        )

        # 전략의 신원과 랭킹 근거를 실행 이력에 박아 둔다. explain이 이 값을 읽는다.
        selected.context["strategy"] = {
            "id": strategy.id,
            "sha256": loaded.sha256,
            "timeframe": strategy.timeframe,
            "params": strategy_params.model_dump(mode="json"),
            "universe_size": len(ranked),
            "ranked_top": _rank_snapshot(ranked),
        }
        return {MAIN: selected}


# --------------------------------------------------------------------------- 내부
def _load(strategy_id: str) -> LoadedStrategy:
    try:
        return load_strategy(strategy_id)
    except StrategyError as exc:
        raise NodeError(str(exc)) from exc


def _warn_on_hash_drift(loaded: LoadedStrategy, expected: str, ctx: RunContext) -> None:
    """파이프라인에 박힌 해시와 실제 파일이 다르면 알린다.

    막지는 않는다 — 전략을 고치는 것은 정상적인 작업이다. 다만 **말없이 지나가면**
    과거 실행의 근거가 소급으로 바뀌므로, 이 실행이 어느 코드로 돌았는지가
    실행 이력에 남아야 한다 (4.7).
    """
    if expected and expected != loaded.sha256:
        ctx.log.warning(
            f"전략 소스가 파이프라인에 기록된 버전과 다릅니다 — "
            f"기록 {expected[:12]}… / 현재 {loaded.sha256[:12]}…. "
            f"이 실행의 결과는 과거 버전과 직접 비교할 수 없습니다. "
            f"의도한 변경이라면 파이프라인의 strategy_sha256을 갱신하세요."
        )


def _parse_params(strategy: Strategy, raw: dict[str, Any]) -> BaseModel:
    try:
        return strategy.Params.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
        )
        raise NodeError(
            f"[{strategy.id}] 전략 파라미터 오류 — {details}. "
            f"사용 가능한 파라미터는 `marketscan describe --strategy {strategy.id}`로 확인하세요."
        ) from exc


def _eligible_items(
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


def _compute_one(strategy: Strategy, item: Item, params: BaseModel, ctx: RunContext) -> Item:
    result = strategy.compute(item, params, ctx)
    if not isinstance(result, Item):
        raise NodeError(
            f"[{strategy.id}] compute가 Item을 돌려주지 않았습니다 ({type(result).__name__}). "
            f"`return item.with_features(...)` 형태여야 합니다."
        )
    return result


def _rank_snapshot(ranked: Bundle) -> list[dict[str, Any]]:
    return [
        {
            "instrument": item.instrument.key,
            "rank": item.features.get(RANK_FEATURE),
            "score": item.features.get("score"),
        }
        for item in ranked.items[:RANK_SNAPSHOT_SIZE]
    ]
