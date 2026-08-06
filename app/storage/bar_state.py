"""Fresh Bar Gate의 영속 저장소 (ARCHITECTURE.md 3.5).

`InMemoryBarState`는 프로세스 메모리에만 남아서, **CLI에서는 Fresh Bar Gate가
사실상 동작하지 않았다** — `stockscan run`은 매번 새 프로세스라 `last_seen`이
항상 비어 있다. v0.4(상주 서버)에서는 프로세스가 살아 있어 그럭저럭 돌았지만
v0.5의 CLI 전환이 이 구멍을 드러냈다. 여기가 그 구멍을 막는다.

**왜 SQLAlchemy가 아니라 `sqlite3`인가**

`BarStateStore` 프로토콜의 메서드가 동기다. 실행 엔진이 `execute()` 한복판에서
`ctx.bar_state.commit()`을 부르는데, 이걸 async로 바꾸면 프로토콜·노드·러너가
줄줄이 딸려 온다. 반대로 이 테이블은 **키 하나에 시각 하나**뿐이라 ORM이 줄
것이 없다. 그래서 프로토콜을 지키고 저장소만 바꿨다.

같은 이유로 이 테이블의 DDL은 `models.py`가 아니라 여기에 있다. 컬럼 표현이 두
곳에 생기면(SQLAlchemy의 `DateTime`과 여기의 ISO 문자열) 언젠가 어긋나고,
어긋난 날 Fresh Bar Gate가 **조용히** 무동작이 된다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")

TABLE = "bar_state"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    pipeline_id TEXT NOT NULL,
    bar_key     TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (pipeline_id, bar_key)
)
"""


class SqlBarState:
    """SQLite에 남는 봉 상태. 파이프라인 단위로 격리된다.

    `bar_key`는 `(노드, 심볼, 타임프레임)`이라 파이프라인이 다른데 노드 id가 같으면
    서로의 봉을 소비한 것으로 읽힌다. 그래서 `pipeline_id`로 한 겹 더 나눈다.
    """

    def __init__(self, path: Path, pipeline_id: str, *, readonly: bool = False) -> None:
        self.pipeline_id = pipeline_id
        self.readonly = readonly
        self._staged: dict[str, datetime] = {}
        self._conn = sqlite3.connect(path)
        # 자동 실행이 겹칠 수 있다. 비동기 엔진이 쓰기 잠금을 쥔 순간과 만나면
        # 기다렸다 진행해야지, 즉시 실패하면 그 실행의 봉이 통째로 재처리된다.
        self._conn.execute("PRAGMA busy_timeout=5000")
        if not readonly:
            self._conn.execute(_DDL)
            self._conn.commit()
        self._seen = self._load()

    def _load(self) -> dict[str, datetime]:
        try:
            rows = self._conn.execute(
                f"SELECT bar_key, as_of FROM {TABLE} WHERE pipeline_id = ?",  # noqa: S608
                (self.pipeline_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # 읽기 전용인데 테이블이 아직 없다 = 커밋 실행이 한 번도 없었다.
            # dry-run이 테이블을 만들면 규칙 11(부작용 금지)이 깨진다.
            return {}
        return {key: datetime.fromisoformat(value).astimezone(UTC) for key, value in rows}

    # ------------------------------------------------------------ BarStateStore
    def last_seen(self, key: str) -> datetime | None:
        return self._seen.get(key)

    def stage(self, key: str, as_of: datetime) -> None:
        self._staged[key] = as_of

    def commit(self) -> None:
        """실행이 온전히 성공했을 때만 러너가 부른다.

        읽기 전용(dry-run)이면 아무것도 쓰지 않는다. 러너가 이미 `ctx.commit`으로
        막고 있지만, 봉 유실은 되돌릴 수 없어서 방어선을 하나 더 둔다 — 잘못
        소비된 봉의 신호는 **영영 사라진다**.
        """
        if self.readonly or not self._staged:
            self._staged.clear()
            return
        now = datetime.now(UTC).isoformat()
        self._conn.executemany(
            f"INSERT INTO {TABLE} (pipeline_id, bar_key, as_of, updated_at) "  # noqa: S608
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(pipeline_id, bar_key) DO UPDATE SET "
            "as_of = excluded.as_of, updated_at = excluded.updated_at",
            [
                (self.pipeline_id, key, _as_utc(as_of).isoformat(), now)
                for key, as_of in self._staged.items()
            ],
        )
        self._conn.commit()
        self._seen.update(self._staged)
        self._staged.clear()

    def discard(self) -> None:
        self._staged.clear()

    def close(self) -> None:
        self._conn.close()


def sqlite_path(url: str) -> Path | None:
    """`sqlite+aiosqlite:///./data/stockscan.db` → `./data/stockscan.db`.

    SQLite가 아니거나 인메모리면 None. 그 경우 호출자가 인메모리 저장소로 물러선다.
    """
    if not url.startswith("sqlite"):
        return None
    raw = url.split("///")[-1]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 저장할 수 없습니다. UTC로 변환해 넘기세요.")
    return value.astimezone(UTC)
