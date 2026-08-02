"""Condition Splitter — 조건에 따라 true/false 두 갈래로 나눈다.

선택되지 않은 브랜치의 하위 노드는 러너가 `skipped`로 전파한다.
식은 안전 평가기를 쓰므로 임포트·함수 정의 같은 표현은 파싱 단계에서 거부된다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.expr import ExpressionError, evaluate_bool
from app.engine.template import item_env
from app.engine.types import Bundle, Item
from app.nodes.base import BaseNode, NodeError
from app.nodes.registry import register
from app.schemas.pipeline import MAIN

TRUE = "true"
FALSE = "false"


class ConditionSplitterParams(BaseModel):
    expression: str = Field(
        default="tags.score >= 8",
        description=(
            "사용 가능한 변수: instrument, features, tags, meta, timeframe, close, bars. "
            "예: tags.ma_signal == 'cross_above' and close > 50000"
        ),
    )
    on_error_value: bool = Field(
        default=False, description="식 평가에 실패한 item을 어느 쪽으로 보낼지"
    )


@register
class ConditionSplitterNode(BaseNode):
    type = "conditionSplitter"
    display_name = "Condition Splitter"
    category = "logic"
    description = "조건식으로 종목을 true/false 두 갈래로 나눕니다."
    ParamsModel = ConditionSplitterParams
    inputs = (MAIN,)
    outputs = (TRUE, FALSE)

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: ConditionSplitterParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        bundle = inputs.get(MAIN, Bundle.empty())
        passed: list[Item] = []
        failed: list[Item] = []

        for item in bundle:
            try:
                result = evaluate_bool(params.expression, item_env(item))
            except ExpressionError as exc:
                # 이름 오타 같은 식 자체의 문제는 전체를 세우는 게 맞다
                if "알 수 없는 이름" in str(exc) or "문법 오류" in str(exc):
                    raise NodeError(f"조건식을 평가할 수 없습니다: {exc}") from exc
                ctx.log.warning(f"{item.instrument.key}: 식 평가 실패 ({exc})")
                result = params.on_error_value
            (passed if result else failed).append(item)

        ctx.log.info(
            f"조건 '{params.expression}': true={len(passed)}개, false={len(failed)}개"
        )
        return {
            TRUE: bundle.replace_items(passed),
            FALSE: bundle.replace_items(failed),
        }
