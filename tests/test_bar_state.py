"""Fresh Bar Gate 영속화 테스트 (ARCHITECTURE.md 3.5).

`InMemoryBarState`는 프로세스 메모리에만 남아서 CLI에서는 게이트가 사실상
무동작이었다. 여기서 검증하는 것은 **새 프로세스가 직전 실행의 봉을 기억하는가**다.
`SqlBarState`를 새로 만드는 것이 "새 프로세스"에 해당한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.engine.state import bar_key
from app.storage.bar_state import SqlBarState, sqlite_path

UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 8, 2, tzinfo=UTC)
KEY = bar_key("data", "nasdaq:BTC", "1d")


def open_state(path: Path, pipeline_id: str = "pipe_t", *, readonly: bool = False) -> SqlBarState:
    """새 프로세스가 저장소를 여는 것에 해당한다."""
    return SqlBarState(path, pipeline_id, readonly=readonly)


def test_committed_bar_survives_a_new_process(tmp_path: Path):
    """★ 이것이 Phase 1에서 막은 구멍이다."""
    db = tmp_path / "m.db"
    first = open_state(db)
    first.stage(KEY, AS_OF)
    first.commit()
    first.close()

    second = open_state(db)
    assert second.last_seen(KEY) == AS_OF
    second.close()


def test_staged_but_uncommitted_bar_is_forgotten(tmp_path: Path):
    """실행이 실패하면 봉을 소비하지 않는다 — 안 그러면 그 신호가 영영 사라진다."""
    db = tmp_path / "m.db"
    first = open_state(db)
    first.stage(KEY, AS_OF)
    first.discard()
    first.commit()  # discard 뒤의 commit은 쓸 것이 없다
    first.close()

    second = open_state(db)
    assert second.last_seen(KEY) is None
    second.close()


def test_staged_value_is_invisible_until_commit(tmp_path: Path):
    """같은 실행 안에서 stage한 값은 보이지 않아야 한다 (프로토콜 계약)."""
    state = open_state(tmp_path / "m.db")
    state.stage(KEY, AS_OF)

    assert state.last_seen(KEY) is None
    state.close()


def test_newer_bar_overwrites_the_older_one(tmp_path: Path):
    db = tmp_path / "m.db"
    first = open_state(db)
    first.stage(KEY, AS_OF)
    first.commit()
    first.close()

    second = open_state(db)
    second.stage(KEY, AS_OF + timedelta(days=1))
    second.commit()
    second.close()

    third = open_state(db)
    assert third.last_seen(KEY) == AS_OF + timedelta(days=1)
    third.close()


def test_pipelines_do_not_consume_each_others_bars(tmp_path: Path):
    """bar_key는 (노드, 심볼, 봉)이라 파이프라인이 다른데 노드 id가 같을 수 있다."""
    db = tmp_path / "m.db"
    one = open_state(db, "pipe_a")
    one.stage(KEY, AS_OF)
    one.commit()
    one.close()

    other = open_state(db, "pipe_b")
    assert other.last_seen(KEY) is None
    other.close()


def test_readonly_state_never_writes(tmp_path: Path):
    """dry-run이 봉을 삼키면 다음 실제 실행에서 stale로 걸러져 신호가 사라진다."""
    db = tmp_path / "m.db"
    writer = open_state(db)
    writer.commit()  # 테이블만 만든다
    writer.close()

    reader = open_state(db, readonly=True)
    reader.stage(KEY, AS_OF)
    reader.commit()
    reader.close()

    after = open_state(db)
    assert after.last_seen(KEY) is None
    after.close()


def test_readonly_state_on_a_database_without_the_table(tmp_path: Path):
    """커밋 실행이 한 번도 없었으면 테이블이 없다. dry-run이 만들면 규칙 11이 깨진다."""
    db = tmp_path / "m.db"
    db.touch()

    reader = open_state(db, readonly=True)
    assert reader.last_seen(KEY) is None
    reader.close()

    # 테이블을 만들지 않았는지 확인한다
    import sqlite3

    with sqlite3.connect(db) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "bar_state" not in tables


def test_naive_datetime_is_refused(tmp_path: Path):
    """모든 시각은 tz-aware UTC다 (규칙 5)."""
    state = open_state(tmp_path / "m.db")
    state.stage(KEY, datetime(2026, 8, 2))  # noqa: DTZ001 - 거부되는지 보는 것이 목적

    with pytest.raises(ValueError, match="naive"):
        state.commit()
    state.close()


# ------------------------------------------------------------------------ 경로
def test_sqlite_path_extraction():
    assert sqlite_path("sqlite+aiosqlite:///./data/stockscan.db") == Path("./data/stockscan.db")
    assert sqlite_path("sqlite+aiosqlite:///:memory:") is None
    assert sqlite_path("postgresql+asyncpg://localhost/db") is None
