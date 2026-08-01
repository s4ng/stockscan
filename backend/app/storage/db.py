"""데이터베이스 연결.

SQLite를 쓰되 **WAL 모드 + busy_timeout**을 강제한다. 스케줄러와 API가 동시에
쓰기를 시도하면 `database is locked`가 나기 때문이다 (ARCHITECTURE.md 4.7).

SQLAlchemy를 쓰는 이유는 SQLite 전용 문법을 피해 PostgreSQL 전환 비용을 낮추기
위해서다. 지금은 단일 사용자 전제라 SQLite로 충분하다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from sqlite3 import Connection as SQLite3Connection

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.storage.models import Base


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


_settings = get_settings()

# sqlite+aiosqlite:///./data/tradeflow.db → ./data 디렉터리를 미리 만들어 둔다
if _settings.database_url.startswith("sqlite"):
    _path = _settings.database_url.split("///")[-1]
    if _path and _path != ":memory:":
        Path(_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(_settings.database_url, echo=_settings.debug, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """테이블을 만든다.

    TODO(Phase 1): Alembic 마이그레이션으로 교체한다. 스키마가 바뀌기 시작하면
                   create_all로는 기존 DB를 따라잡을 수 없다.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성."""
    async with SessionLocal() as session:
        yield session
