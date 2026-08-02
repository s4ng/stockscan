"""Fresh Bar Gate가 참조하는 봉 상태 저장소 (ARCHITECTURE.md 3.5).

코인과 주식을 한 파이프라인에 섞으면, 미국장이 닫힌 시간에 파이프라인이 돌 때
주식 쪽은 어제와 똑같은 캔들을 다시 읽고 같은 신호를 매번 재발생시킨다.
직전 실행의 as_of를 기억했다가 변하지 않았으면 해당 item을 조용히 제외한다.

**봉의 소비는 실행이 끝까지 성공한 뒤에만 확정한다.** 수집 시점에 바로 기록하면
하류 노드(알림 등)가 실패했을 때도 봉이 소비된 것으로 남아, 재실행하면 stale로
걸러지고 **그 신호는 영원히 사라진다.** 반대로 커밋을 미뤄서 생기는 최악은 알림
중복인데, 그건 `alerts_sent.dedup_key` UNIQUE가 이미 막는다(4.5). 잃는 쪽보다
겹치는 쪽이 안전하다.

**영속 구현은 `app/storage/bar_state.py`의 `SqlBarState`다** (Phase 1에서 추가).
CLI가 실행마다 어느 것을 꽂을지 정한다.

| 실행 | 저장소 |
| :--- | :--- |
| `--commit` | `SqlBarState` — 봉을 실제로 소비한다 |
| dry-run (DB 있음) | `SqlBarState(readonly)` — **읽기만** 한다 |
| dry-run (DB 없음) | `InMemoryBarState` — DB 파일조차 안 만든다 (12.1) |

dry-run도 **읽기는 한다.** 읽지 않으면 `run`과 `run --commit`이 서로 다른 종목
집합을 보게 되어, dry-run이 실제 실행을 예측하지 못한다.

아래 `InMemoryBarState`는 **테스트와 백테스트용**으로 남는다. 프로세스 메모리에만
남으므로 CLI에 꽂으면 Fresh Bar Gate가 무동작이 된다는 점을 잊지 말 것.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class BarStateStore(Protocol):
    """(노드, 심볼, 타임프레임)별로 마지막으로 처리한 봉을 기억한다."""

    def last_seen(self, key: str) -> datetime | None:
        """확정된(commit된) 마지막 봉. 같은 실행 안에서 stage한 값은 보이지 않는다."""
        ...

    def stage(self, key: str, as_of: datetime) -> None:
        """이 실행에서 이 봉을 처리했다고 예약한다. 아직 확정은 아니다."""
        ...

    def commit(self) -> None:
        """실행이 성공했을 때만 호출한다. 예약분을 확정한다."""
        ...

    def discard(self) -> None:
        """실행이 실패했을 때 호출한다. 예약분을 버려 다음 실행이 재시도하게 한다."""
        ...


class InMemoryBarState:
    """프로세스 메모리에만 남는 기본 구현."""

    def __init__(self) -> None:
        self._seen: dict[str, datetime] = {}
        self._staged: dict[str, datetime] = {}

    def last_seen(self, key: str) -> datetime | None:
        return self._seen.get(key)

    def stage(self, key: str, as_of: datetime) -> None:
        self._staged[key] = as_of

    def commit(self) -> None:
        self._seen.update(self._staged)
        self._staged.clear()

    def discard(self) -> None:
        self._staged.clear()

    def clear(self) -> None:
        self._seen.clear()
        self._staged.clear()


def bar_key(node_id: str, instrument_key: str, timeframe: str) -> str:
    return f"{node_id}|{instrument_key}|{timeframe}"
