"""Manual Trigger — 캔버스에서 [실행] 버튼을 눌러 시작하는 진입 노드.

Schedule Trigger(APScheduler 연동)는 Phase 1에서 추가한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.schemas.pipeline import MAIN


class ManualTriggerParams(BaseModel):
    note: str = Field(default="", description="메모 (실행에 영향 없음)")


@register
class ManualTriggerNode(BaseNode):
    type = "manualTrigger"
    display_name = "Manual Trigger"
    category = "trigger"
    description = "수동 실행 진입점입니다. 테스트와 디버깅에 씁니다."
    ParamsModel = ManualTriggerParams
    inputs = ()  # 트리거 앞에는 아무것도 올 수 없다
    outputs = (MAIN,)
    requires_input = False

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: ManualTriggerParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        ctx.log.info(f"수동 실행 시작 (mode={ctx.mode}, now={ctx.now.isoformat()})")
        return {MAIN: Bundle.empty()}
