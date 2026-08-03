"""신호 배출구 (ARCHITECTURE.md 4.5 / 12.2).

노드는 "신호를 하나 냈다"고 말할 뿐, 그것이 DB에 들어가는지 메모리에만 남는지
알지 못한다. **그 결정은 `--commit` 하나로 CLI가 내린다** — 노드마다 dry-run 분기를
심으면 언젠가 하나를 빠뜨리고, 그날 에이전트가 실수로 봉을 삼킨다 (규칙 11).

`dedup_key`가 여기 있는 이유는 3.5와 짝을 이루기 때문이다. 봉 커밋을 실행 성공
시점까지 미루면 최악의 경우 **알림이 겹치는데**, 그건 이 키가 막는다. 반대로
유실을 막을 장치는 없으므로 겹치는 쪽이 안전하다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.engine.types import Item


@dataclass(frozen=True)
class SignalDraft:
    """저장 직전의 신호. 저장소 스키마를 노드에 노출하지 않기 위한 중간 표현."""

    run_id: str
    pipeline_id: str
    node_id: str
    instrument: str
    venue: str
    timeframe: str
    as_of: datetime
    kind: str = "entry"
    strategy_id: str | None = None
    strategy_sha256: str | None = None
    features: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """같은 캔들 기준 신호는 한 번만. Phase 5에서 `| side`를 붙이면 주문 멱등키가 된다."""
        raw = "|".join(
            [self.pipeline_id, self.node_id, self.instrument, self.as_of.isoformat(), self.kind]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "node_id": self.node_id,
            "dedup_key": self.dedup_key,
            "instrument": self.instrument,
            "display_name": self.meta.get("display_name"),
            "venue": self.venue,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "kind": self.kind,
            "strategy_id": self.strategy_id,
            "strategy_sha256": self.strategy_sha256,
            "features": self.features,
            "tags": self.tags,
        }


class SignalSink(Protocol):
    """신호를 받아 어딘가에 남긴다."""

    persistent: bool
    """True면 실제 부작용이 있다. 로그 문구와 종료 요약이 이 값으로 갈린다."""

    async def emit(self, draft: SignalDraft) -> bool:
        """새로 기록했으면 True, 이미 있던 신호(dedup)면 False."""
        ...


class CollectingSink:
    """메모리에만 담는 기본 배출구. **dry-run의 기본값이다.**

    부작용이 없으므로 `--commit` 없이 몇 번을 돌려도 안전하고, "이번에 무엇이
    나왔을 것인가"를 그대로 보여줄 수 있다.
    """

    persistent = False

    def __init__(self) -> None:
        self.drafts: list[SignalDraft] = []
        self._seen: set[str] = set()

    async def emit(self, draft: SignalDraft) -> bool:
        if draft.dedup_key in self._seen:
            return False
        self._seen.add(draft.dedup_key)
        self.drafts.append(draft)
        return True


def draft_from_item(
    item: Item,
    *,
    run_id: str,
    pipeline_id: str,
    node_id: str,
    kind: str = "entry",
    strategy: dict[str, Any] | None = None,
) -> SignalDraft:
    """Item 하나를 신호로 옮긴다. OHLCV 원본은 옮기지 않는다 (12.4)."""
    meta = dict(item.meta)
    # `005930`보다 `삼성전자`가 읽힌다. 신호에 박아 두지 않으면 나중에 `explain`·
    # `review`가 이름을 얻으려고 종목 목록을 다시 조회해야 한다.
    if item.instrument.display_name and item.instrument.display_name != item.instrument.symbol:
        meta["display_name"] = item.instrument.display_name

    return SignalDraft(
        run_id=run_id,
        pipeline_id=pipeline_id,
        node_id=node_id,
        instrument=item.instrument.key,
        venue=item.instrument.venue,
        timeframe=item.timeframe,
        as_of=item.as_of,
        kind=kind,
        strategy_id=(strategy or {}).get("id"),
        strategy_sha256=(strategy or {}).get("sha256"),
        features=dict(item.features),
        tags=dict(item.tags),
        meta=meta,
    )
