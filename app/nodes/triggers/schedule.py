"""Schedule Trigger — 파이프라인이 **자기 실행 시각을 갖는다** (ARCHITECTURE.md 8장).

`serve`의 설정이 아니라 파이프라인의 성질로 둔 이유는 이동성이다 — 설정 파일을
복사하면 스케줄도 함께 간다. `serve`는 이 노드를 읽어 루프를 돌 뿐이다.

실행 시점에 이 노드가 하는 일은 `manualTrigger`와 같다. 스케줄은 **실행 전에**
읽히므로, 여기서 다시 판정할 것이 없다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.schedule import ScheduleError, _parse_entry, _parse_time
from app.schemas.pipeline import MAIN


class ScheduleTriggerParams(BaseModel):
    at: list[Any] = Field(
        default_factory=list,
        description='실행 시각 목록. 예: [{ time: "15:40", market: krx, note: "KRX 마감 뒤" }]',
    )
    timezone: str = Field(default="", description="시각의 기준 시간대. 비우면 파이프라인 설정")
    heartbeat: str = Field(
        default="",
        description="하루 1회 생존 신고 시각 'HH:MM'. 신호가 0건이어도 보냅니다.",
    )

    @field_validator("at")
    @classmethod
    def _check_entries(cls, value: list[Any]) -> list[Any]:
        """**여기서 터뜨린다.** 스케줄이 잘못된 것을 실행 시점에 알면 이미 하루를
        날린 뒤다 — 검증(`marketscan describe`)에서 걸려야 한다."""
        if not value:
            raise ValueError('at이 비어 있습니다. 예: [{ time: "15:40", market: krx }]')
        for item in value:
            _parse_entry(item)  # 형식 오류를 여기서 드러낸다
        return value

    @field_validator("heartbeat")
    @classmethod
    def _check_heartbeat(cls, value: str) -> str:
        if value:
            _parse_time(value, "heartbeat")
        return value


@register
class ScheduleTriggerNode(BaseNode):
    type = "scheduleTrigger"
    display_name = "Schedule Trigger"
    category = "trigger"
    description = "정해진 시각에 실행합니다. `serve`가 이 선언을 읽습니다."
    ParamsModel = ScheduleTriggerParams
    inputs = ()
    outputs = (MAIN,)
    requires_input = False

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: ScheduleTriggerParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        ctx.log.info(
            f"스케줄 실행 (mode={ctx.mode}, now={ctx.now.isoformat()}, "
            f"슬롯 {len(params.at)}개)"
        )
        return {MAIN: Bundle.empty()}


__all__ = ["ScheduleError", "ScheduleTriggerNode", "ScheduleTriggerParams"]
