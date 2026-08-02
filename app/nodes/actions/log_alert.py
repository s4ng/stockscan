"""Log Alert — 신호를 실행 로그로 남기는 노드.

**이 노드는 바깥으로 아무것도 내보내지 않는다** (`sends_external_messages = False`).
단일 실행의 산출물은 stdout과 HTML 리포트이고, 텔레그램 같은 실제 전송은 상주
실행(`serve`)이 생길 때 별도 노드로 붙는다 (ARCHITECTURE.md 11장 4b).

그 노드는 `sends_external_messages = True`를 선언하기만 하면 되고, 그러면 실행
엔진이 `run`에서 자동으로 건너뛴다 — 노드가 스스로 판단할 일이 아니다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.template import render_item
from app.engine.types import Bundle
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.schemas.pipeline import MAIN

DEFAULT_TEMPLATE = (
    "[{{instrument.venue}}] {{instrument.display_name}} · "
    "{{close}} {{instrument.quote_currency}} ({{as_of}})"
)


class LogAlertParams(BaseModel):
    template: str = Field(default=DEFAULT_TEMPLATE, description="{{식}} 자리표시자를 지원합니다")
    max_lines: int = Field(
        default=20, ge=1, le=500, description="한 실행에서 남길 최대 줄 수 (로그 폭주 방지)"
    )


@register
class LogAlertNode(BaseNode):
    type = "logAlert"
    display_name = "Log Alert"
    category = "action"
    description = "신호를 사람이 읽는 한 줄로 실행 로그에 남깁니다. 외부로 나가지 않습니다."
    ParamsModel = LogAlertParams
    inputs = (MAIN,)
    outputs = (MAIN,)
    sends_external_messages = False  # 로그일 뿐이다. 바깥으로 나가는 것이 없다

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: LogAlertParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        bundle = inputs.get(MAIN, Bundle.empty())
        if bundle.is_empty:
            ctx.log.info("남길 신호가 없습니다")
            return {MAIN: bundle}

        targets = bundle.items[: params.max_lines]
        dropped = len(bundle) - len(targets)

        for item in targets:
            ctx.log.info(f"[{ctx.mode.upper()}] {render_item(params.template, item)}")

        if dropped:
            # 조용한 절삭은 "전부 봤다"로 오해되므로 반드시 남긴다
            ctx.log.warning(
                f"max_lines({params.max_lines}) 초과로 {dropped}건을 로그에 남기지 "
                f"않았습니다. 전체는 HTML 리포트에 있습니다."
            )

        return {MAIN: bundle}
