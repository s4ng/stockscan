"""Log Alert — 알림을 로그로 출력하는 개발용 액션 노드.

`shadow`/`backtest` 모드에서는 외부로 내보내지 않고 기록만 남긴다.
Telegram Alert 노드도 같은 규칙(`ctx.sends_alerts`)을 따르게 된다.

TODO(Phase 1): TelegramAlertNode 추가 — credential_id + chat_id + dedup_key.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.template import render_item
from app.engine.types import Bundle
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.schemas.pipeline import MAIN

DEFAULT_TEMPLATE = "[{{instrument.venue}}] {{instrument.display_name}} · {{close}} {{instrument.quote_currency}} ({{as_of}})"


class LogAlertParams(BaseModel):
    template: str = Field(default=DEFAULT_TEMPLATE, description="{{식}} 자리표시자를 지원합니다")
    max_alerts: int = Field(
        default=20, ge=1, le=500, description="한 실행에서 보낼 최대 알림 수 (폭주 방지)"
    )


@register
class LogAlertNode(BaseNode):
    type = "logAlert"
    display_name = "Log Alert"
    category = "action"
    description = "신호를 로그로 출력합니다. 알림 채널 연동 전 개발용입니다."
    ParamsModel = LogAlertParams
    inputs = (MAIN,)
    outputs = (MAIN,)

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: LogAlertParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        bundle = inputs.get(MAIN, Bundle.empty())
        if bundle.is_empty:
            ctx.log.info("보낼 신호가 없습니다")
            return {MAIN: bundle}

        targets = bundle.items[: params.max_alerts]
        dropped = len(bundle) - len(targets)
        prefix = "ALERT" if ctx.sends_alerts else f"{ctx.mode.upper()}"

        for item in targets:
            ctx.log.info(f"[{prefix}] {render_item(params.template, item)}")

        if dropped:
            # 조용한 절삭은 "전부 보냈다"로 오해되므로 반드시 남긴다
            ctx.log.warning(f"max_alerts({params.max_alerts}) 초과로 {dropped}건을 보내지 않았습니다")

        return {MAIN: bundle}
