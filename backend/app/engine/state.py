"""Fresh Bar Gate가 참조하는 봉 상태 저장소 (ARCHITECTURE.md 3.5).

코인과 주식을 한 파이프라인에 섞으면, 미국장이 닫힌 시간에 파이프라인이 돌 때
주식 쪽은 어제와 똑같은 캔들을 다시 읽고 같은 신호를 매번 재발생시킨다.
직전 실행의 as_of를 기억했다가 변하지 않았으면 해당 item을 조용히 제외한다.

TODO(Phase 2): SQLite 구현으로 교체한다. 프로세스를 재시작하면 상태가 사라진다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class BarStateStore(Protocol):
    """(노드, 심볼, 타임프레임)별로 마지막으로 처리한 봉을 기억한다."""

    def last_seen(self, key: str) -> datetime | None: ...

    def mark(self, key: str, as_of: datetime) -> None: ...


class InMemoryBarState:
    """프로세스 메모리에만 남는 기본 구현."""

    def __init__(self) -> None:
        self._seen: dict[str, datetime] = {}

    def last_seen(self, key: str) -> datetime | None:
        return self._seen.get(key)

    def mark(self, key: str, as_of: datetime) -> None:
        self._seen[key] = as_of

    def clear(self) -> None:
        self._seen.clear()


def bar_key(node_id: str, instrument_key: str, timeframe: str) -> str:
    return f"{node_id}|{instrument_key}|{timeframe}"
