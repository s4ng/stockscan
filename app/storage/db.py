"""데이터베이스 연결.

SQLite를 쓰되 **WAL 모드 + busy_timeout**을 강제한다. CLI로 전환하면서 쓰는
프로세스가 한 번에 하나가 되어 잠금 경합 위험은 크게 줄었지만, 자동 실행이
겹칠 수 있으므로 그대로 유지한다 (ARCHITECTURE.md 4.7).

SQLAlchemy를 쓰는 이유는 SQLite 전용 문법을 피하기 위해서다. `ohlcv_cache`가
커지면 뒤를 갈아 끼울 수 있어야 한다 (3.9).

엔진은 **지연 생성**한다. 임포트 시점에 만들면 테스트가 다른 DB를 가리킬 수 없고,
부작용 없는 읽기 전용 명령이 DB 파일을 만들어 버린다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from sqlite3 import Connection as SQLite3Connection

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.storage.models import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_url: str | None = None


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
    """SQLite 연결마다 PRAGMA를 건다. 다른 DB로 바꾸면 자동으로 무시된다."""
    if not isinstance(dbapi_connection, SQLite3Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def database_url() -> str:
    """실제로 열 DB URL. **SQLite 상대 경로는 `~/.marketscan` 기준으로 편다.**

    ⚠️ 편지 않으면 경로가 **현재 디렉터리**를 따라간다. `cd data && marketscan run`이
    `data/data/marketscan.db`를 새로 만들고, 사용자는 캐시와 신호가 통째로 비어 있는
    것을 보게 된다 — 파일이 두 개 생겼다는 사실 자체가 잘 안 보이므로 진단이 어렵다.
    `config.py`가 "어느 디렉터리에서 CLI를 부르든 같은 파일을 보아야 한다"고 정한
    것을 여기서 지킨다 (다른 경로들은 `settings.resolve()`가 이미 하고 있다).

    저장소 안이 아니라 홈인 이유는 수명이다 — `ohlcv_cache`는 무료 소스가 막혀도
    남는 유일한 자산이라(3.9), 저장소를 지웠다고 함께 사라지면 안 된다.
    """
    return _resolve_sqlite(_url or get_settings().database_url)


def _resolve_sqlite(url: str) -> str:
    if not url.startswith("sqlite"):
        return url
    prefix, sep, raw = url.partition("///")
    if not sep or not raw or raw == ":memory:" or Path(raw).is_absolute():
        return url
    return f"{prefix}///{get_settings().resolve(raw).as_posix()}"


def configure(url: str) -> None:
    """DB URL을 바꾼다. 테스트에서 임시 파일 DB를 가리킬 때 쓴다."""
    global _url, _engine, _sessionmaker
    _url = url
    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = database_url()
        _ensure_parent_dir(url)
        _engine = create_async_engine(url, echo=get_settings().debug, future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


class SchemaDriftError(RuntimeError):
    """기존 DB의 컬럼이 모자란데 **안전하게 더할 수 없을** 때.

    사후에 조용히 망가지는 것을 앞당겨 터뜨린다 — 실제로 한 번 밟은 사고다.
    모델에 컬럼을 추가하면 예전 DB는 그대로 남고, 그 DB에 INSERT하면
    `OperationalError`가 나는데 캐시 쓰기 경로가 그걸 삼켜(`except SQLAlchemyError`)
    **"조금 느린 것"처럼 보인다.** 실제로는 캐시가 영영 안 채워진다.
    """


async def init_db() -> None:
    """테이블을 만들고, **모자란 컬럼을 더한다.**

    ⚠️ `create_all`은 **없는 테이블만 만든다.** 기존 테이블에 컬럼을 더하지 않으므로
    모델을 넓히면 예전 DB가 뒤처진다.

    ★ **nullable 컬럼은 `ADD COLUMN`으로 자동으로 붙인다** (2026-08-06).
    예전에는 무조건 터뜨리고 "DB 파일을 지우면 재생성된다"고 안내했는데, 그 안내를
    따르면 **`ohlcv_cache`가 통째로 날아간다** — 무료 소스가 막혀도 남는 유일한
    자산이고(규칙 16) 사후 수익률 계산이 통째로 여기 얹혀 있다. 채점을 붙이려면
    `signals`를 넓혀야 하는데, 그때마다 자산을 버리게 두면 안 된다.

    **더할 수 없는 것만 터뜨린다** — SQLite는 `ADD COLUMN`으로 PK·UNIQUE를 만들 수
    없고, NOT NULL은 기본값이 있어야 한다. 그런 변경은 사람이 판단할 일이다.
    """
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        added, blocked = await connection.run_sync(_reconcile_columns)

    if blocked:
        detail = " · ".join(f"{t}: {', '.join(cols)}" for t, cols in sorted(blocked.items()))
        raise SchemaDriftError(
            f"DB 스키마가 모델보다 낡았고 자동으로 더할 수 없는 컬럼이 있습니다 — {detail}. "
            f"PK·UNIQUE·NOT NULL(기본값 없음)은 SQLite가 ADD COLUMN으로 못 만듭니다. "
            f"⚠️ `ohlcv_cache`·`signals`는 자산이므로 **지우기 전에 반드시 백업하세요**."
        )
    if added:
        for table, columns in sorted(added.items()):
            log.info("스키마를 넓혔습니다 — %s에 %s 추가", table, ", ".join(columns))


def _reconcile_columns(connection: object) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """모자란 컬럼을 더하고 `(더한 것, 못 더한 것)`을 돌려준다."""
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    added: dict[str, list[str]] = {}
    blocked: dict[str, list[str]] = {}

    for name, table in Base.metadata.tables.items():
        if name not in existing:
            continue
        actual = {col["name"] for col in inspector.get_columns(name)}
        for column in table.columns:
            if column.name in actual:
                continue
            clause = _add_column_clause(column, connection)
            if clause is None:
                blocked.setdefault(name, []).append(column.name)
                continue
            connection.execute(text(f"ALTER TABLE {name} ADD COLUMN {clause}"))  # type: ignore[attr-defined]
            added.setdefault(name, []).append(column.name)
    return added, blocked


def _add_column_clause(column: object, connection: object) -> str | None:
    """`ADD COLUMN`에 넣을 조각. 안전하게 만들 수 없으면 None.

    SQLite가 거부하는 것 셋: PRIMARY KEY · UNIQUE · **기본값 없는 NOT NULL**.

    ⚠️ 마지막 것이 함정이다 — 모델의 `default=0`은 **파이썬 쪽** 기본값이라 DDL에
    실리지 않는다. 그대로 `ADD COLUMN ... NOT NULL`을 보내면 SQLite가
    "Cannot add a NOT NULL column with default value NULL"로 거부한다. 그래서
    스칼라 기본값이 있으면 `DEFAULT`를 함께 실어 준다 — 기존 행이 받는 값이
    새 행이 받을 값과 같아진다.
    """
    if column.primary_key or column.unique:  # type: ignore[attr-defined]
        return None

    # ⚠️ **테이블 수준 UNIQUE에 걸린 컬럼도 막는다.** `ADD COLUMN`은 컬럼만 만들고
    #    제약은 만들지 않으므로, 그냥 붙이면 `signals.dedup_key`가 생겼는데
    #    **중복 방지가 꺼진 채로 도는** 상태가 된다 — 같은 봉의 신호가 여러 번
    #    쌓이고, 그러면 성적표의 분모가 조용히 부풀어 오른다 (4.5).
    if any(
        column.name in constraint.columns  # type: ignore[attr-defined]
        for constraint in column.table.constraints  # type: ignore[attr-defined]
        if constraint.__class__.__name__ == "UniqueConstraint"
    ):
        return None

    dialect = connection.engine.dialect  # type: ignore[attr-defined]
    spec = f"{column.name} {column.type.compile(dialect)}"  # type: ignore[attr-defined]

    literal = _default_literal(column)
    if literal is not None:
        spec += f" DEFAULT {literal}"
    if not column.nullable and literal is not None:  # type: ignore[attr-defined]
        spec += " NOT NULL"
    # ⚠️ **NOT NULL인데 DDL에 실을 기본값이 없으면 제약을 빼고 붙인다.**
    #
    # `default=utcnow` 같은 콜러블은 행마다 값이 달라 DDL에 넣을 수 없다. 여기서
    # 포기하면 사용자에게 남는 선택지가 "DB를 지운다"뿐인데, 그러면 `ohlcv_cache`가
    # 통째로 날아간다(규칙 16). **제약이 조금 느슨한 것보다 자산을 잃는 쪽이 훨씬 나쁘다.**
    #
    # 실무상 안전한 이유: 값을 채우는 것은 ORM이고(파이썬 쪽 default가 매 INSERT마다
    # 적용된다) 이 저장소에는 raw INSERT 경로가 없다. 새로 만드는 DB는 `create_all`이
    # 원래의 엄격한 스키마로 만든다 — 느슨해지는 것은 **넓혀 온 DB뿐**이다.
    return spec


def _default_literal(column: object) -> str | None:
    """DDL에 실을 수 있는 기본값. 콜러블·시퀀스면 None (그건 행마다 달라진다)."""
    server_default = column.server_default  # type: ignore[attr-defined]
    if server_default is not None and hasattr(server_default, "arg"):
        return str(server_default.arg)

    default = column.default  # type: ignore[attr-defined]
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """세션 하나를 열고 닫는다. 커밋은 호출자가 명시적으로 한다."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose() -> None:
    """엔진을 닫는다. CLI가 끝날 때 호출해 커넥션을 남기지 않는다."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _ensure_parent_dir(url: str) -> None:
    """`sqlite+aiosqlite:///./data/marketscan.db` → ./data 를 미리 만든다."""
    if not url.startswith("sqlite"):
        return
    path = url.split("///")[-1]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
