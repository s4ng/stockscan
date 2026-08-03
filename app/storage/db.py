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
    """실제로 열 DB URL. **SQLite 상대 경로는 프로젝트 루트 기준으로 편다.**

    ⚠️ 편지 않으면 경로가 **현재 디렉터리**를 따라간다. `cd data && marketscan run`이
    `data/data/marketscan.db`를 새로 만들고, 사용자는 캐시와 신호가 통째로 비어 있는
    것을 보게 된다 — 파일이 두 개 생겼다는 사실 자체가 잘 안 보이므로 진단이 어렵다.
    `config.py`가 "어느 디렉터리에서 CLI를 부르든 같은 파일을 보아야 한다"고 정한
    것을 여기서 지킨다 (다른 경로들은 `settings.resolve()`가 이미 하고 있다).
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
    """기존 DB에 모델의 컬럼이 없을 때. 사후에 조용히 망가지는 것을 앞당겨 터뜨린다."""


async def init_db() -> None:
    """테이블을 만들고, **기존 테이블의 컬럼이 모자라지 않은지 확인한다.**

    ⚠️ `create_all`은 **없는 테이블만 만든다.** 기존 테이블에 컬럼을 더하지 않으므로,
    모델에 컬럼을 추가하면 예전 DB는 그대로 남는다. 그리고 그 DB에 INSERT를 하면
    `OperationalError`가 나는데 캐시 쓰기 경로가 그걸 삼켜(`except SQLAlchemyError`)
    **"조금 느린 것"처럼 보인다** — 실제로는 캐시가 영영 안 채워진다.

    실제로 한 번 밟은 사고라 여기서 앞당겨 터뜨린다. Alembic을 들이기 전까지의
    임시방편이지만, 조용한 열화보다는 낫다.
    """
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        drift = await connection.run_sync(_missing_columns)
    if drift:
        detail = " · ".join(f"{t}: {', '.join(cols)}" for t, cols in sorted(drift.items()))
        raise SchemaDriftError(
            f"DB 스키마가 모델보다 낡았습니다 — 없는 컬럼: {detail}. "
            f"`ohlcv_cache`·`signals`는 자산이므로 지우기 전에 백업하세요. "
            f"캐시만 잃어도 된다면 DB 파일을 지우고 다시 실행하면 재생성됩니다."
        )


def _missing_columns(connection: object) -> dict[str, list[str]]:
    """모델에는 있는데 실제 테이블에는 없는 컬럼."""
    from sqlalchemy import inspect

    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    drift: dict[str, list[str]] = {}
    for name, table in Base.metadata.tables.items():
        if name not in existing:
            continue
        actual = {col["name"] for col in inspector.get_columns(name)}
        missing = [c.name for c in table.columns if c.name not in actual]
        if missing:
            drift[name] = missing
    return drift


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
