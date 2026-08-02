"""Persist Signal — 신호를 `signals`에 기록하는 액션 노드 (ARCHITECTURE.md 5장).

**이 노드는 부작용의 유무를 스스로 판단하지 않는다.** `ctx.signals` 배출구가
dry-run이면 메모리에만 담기고, `--commit`이 붙었을 때만 CLI가 DB 배출구를 꽂는다
(규칙 11). 노드마다 분기를 심으면 언젠가 하나를 빠뜨린다.

`signals`가 쌓여야 `explain` · `stats` · 4.8의 오버라이드 추적이 성립한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.signals import draft_from_item
from app.engine.types import Bundle
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.schemas.pipeline import MAIN


class PersistSignalParams(BaseModel):
    kind: str = Field(default="entry", description="신호 종류. dedup_key에 포함됩니다.")


@register
class PersistSignalNode(BaseNode):
    type = "persistSignal"
    display_name = "Persist Signal"
    category = "action"
    description = "통과한 종목을 signals 테이블에 기록합니다. --commit이 있을 때만 실제로 씁니다."
    ParamsModel = PersistSignalParams
    inputs = (MAIN,)
    outputs = (MAIN,)

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: PersistSignalParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        bundle = inputs.get(MAIN, Bundle.empty())
        if bundle.is_empty:
            ctx.log.info("기록할 신호가 없습니다")
            return {MAIN: bundle}

        # 전략 노드가 남긴 신원을 신호에 그대로 박는다. explain이 이걸 읽어
        # "어떤 전략의 어떤 버전이었는가"를 한 번에 돌려준다 (12.5).
        strategy = bundle.context.get("strategy") if isinstance(bundle.context, dict) else None

        written = 0
        duplicates = 0
        for item in bundle:
            draft = draft_from_item(
                item,
                run_id=ctx.run_id,
                pipeline_id=ctx.pipeline_id,
                node_id=ctx.node_id or self.type,
                kind=params.kind,
                strategy=strategy if isinstance(strategy, dict) else None,
            )
            if await ctx.signals.emit(draft):
                written += 1
            else:
                duplicates += 1

        if ctx.signals.persistent:
            ctx.log.info(f"신호 {written}건 기록 (중복 제외 {duplicates}건)")
        else:
            ctx.log.info(
                f"dry-run — 신호 {written}건을 기록하지 **않았습니다**. "
                f"실제로 남기려면 --commit을 붙이세요."
            )
        return {MAIN: bundle}
