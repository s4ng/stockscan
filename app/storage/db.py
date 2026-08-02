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
    return _url or get_settings().database_url


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


async def init_db() -> None:
    """테이블을 만든다.

    TODO(Phase 1): Alembic 마이그레이션으로 교체한다. 스키마가 바뀌기 시작하면
                   create_all로는 기존 DB를 따라잡을 수 없다.
    """
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


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
