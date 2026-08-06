"""저장소 계약 테스트 — 버전 불변성이 핵심이다."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.storage import repository as repo
from app.storage.models import Base, PipelineVersionRow
from tests.conftest import make_config


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def make_spec(strategy: str = "demo_momentum", size: int = 2):
    """설정 한 벌. `pipeline_id`는 전략에서 유도되므로 따로 주지 않는다."""
    return make_config(strategy=strategy, universe={"nasdaq": size})


async def test_save_new_pipeline_assigns_id(session):
    pipeline_id, version = await repo.save_config(session, make_spec())
    assert pipeline_id.startswith("pipe_")
    assert version == 1


async def test_save_existing_pipeline_bumps_version(session):
    pipeline_id, _ = await repo.save_config(session, make_spec())
    _, v2 = await repo.save_config(session, make_spec(size=3))
    _, v3 = await repo.save_config(session, make_spec(size=4))
    assert (v2, v3) == (2, 3)


async def test_old_versions_are_immutable(session):
    """새 버전을 저장해도 예전 스냅샷은 그대로 남아야 한다."""
    pipeline_id, _ = await repo.save_config(session, make_spec(size=2))
    await repo.save_config(session, make_spec(size=5))

    old = await repo.load_config(session, pipeline_id, version=1)
    new = await repo.load_config(session, pipeline_id)

    assert old.universe == {"nasdaq": 2}
    assert new.universe == {"nasdaq": 5}


async def test_an_unchanged_config_does_not_bump_the_version(session):
    """★ 매 실행마다 같은 설정을 쌓으면 버전 번호가 실행 횟수가 되어
    '언제 설정이 바뀌었나'를 잃는다."""
    pipeline_id, first = await repo.save_config(session, make_spec())
    _, again = await repo.save_config(session, make_spec())

    assert (first, again) == (1, 1)
    assert len(await repo.list_versions(session, pipeline_id)) == 1


async def test_the_snapshot_never_carries_the_token(session):
    """★ 실행 이력은 백업·공유 대상이다. 비밀이 거기까지 따라가면 설정 파일
    하나만 조심해서는 막을 수 없다."""
    from app.config import TelegramConfig

    config = make_spec().model_copy(
        update={"telegram": TelegramConfig(token="secret", chat_id="42")}
    )
    await repo.save_config(session, config)

    stored = await session.scalar(select(PipelineVersionRow.spec))
    assert "telegram" not in stored
    assert "secret" not in str(stored)


async def test_list_pipelines_reports_active_version(session):
    pipeline_id, _ = await repo.save_config(session, make_spec(size=2))
    await repo.save_config(session, make_spec(size=7))

    summaries = await repo.list_configs(session)
    assert len(summaries) == 1
    assert summaries[0].version == 2


async def test_list_versions_is_newest_first(session):
    pipeline_id, _ = await repo.save_config(session, make_spec())
    await repo.save_config(session, make_spec(size=9))
    versions = await repo.list_versions(session, pipeline_id)
    assert [v["version"] for v in versions] == [2, 1]


async def test_timestamps_round_trip_as_utc_aware(session):
    """SQLite는 tzinfo를 저장하지 않는다. naive로 새어 나오면 프론트가 로컬 시각으로
    오해해 표시가 통째로 어긋난다 (CLAUDE.md 규칙 4)."""
    pipeline_id, _ = await repo.save_config(session, make_spec())

    summaries = await repo.list_configs(session)
    updated_at = summaries[0].updated_at
    assert updated_at.tzinfo is not None
    assert updated_at.utcoffset() == timedelta(0)
    assert summaries[0].to_dict()["updated_at"].endswith("+00:00")

    versions = await repo.list_versions(session, pipeline_id)
    assert str(versions[0]["created_at"]).endswith("+00:00")


async def test_strategy_hash_is_pinned_to_the_version(session):
    """전략을 이름으로만 참조하면 파일을 고치는 순간 과거 버전의 의미가 소급으로
    바뀐다 (CLAUDE.md 규칙 10 / §4.7)."""
    await repo.save_config(session, make_spec(strategy="demo_momentum"))

    stored = await session.scalar(
        select(PipelineVersionRow.strategy_hashes).where(
            PipelineVersionRow.pipeline_id == "pipe_demo_momentum"
        )
    )
    assert len(stored["demo_momentum"]) == 64


async def test_missing_strategy_records_the_reason_not_silence(session):
    """해시가 조용히 빠지면 '기록을 안 했다'와 '파일이 없었다'를 구분할 수 없다."""
    await repo.save_config(session, make_spec(strategy="없는전략"))

    stored = await session.scalar(
        select(PipelineVersionRow.strategy_hashes).where(
            PipelineVersionRow.pipeline_id == "pipe_없는전략"
        )
    )
    assert stored["없는전략"].startswith("unavailable:")


async def test_load_missing_pipeline_raises(session):
    with pytest.raises(repo.PipelineNotFoundError):
        await repo.load_config(session, "pipe_nope")


async def test_versions_are_never_overwritten(session):
    """규칙 10 — 저장은 항상 새 버전을 **추가**한다. 기존 스냅샷은 불변이다."""
    pipeline_id, _ = await repo.save_config(session, make_spec(size=2))
    await repo.save_config(session, make_spec(size=9))

    versions = await repo.list_versions(session, pipeline_id)
    assert [v["version"] for v in versions] == [2, 1]
    assert (await repo.load_config(session, pipeline_id, version=1)).universe == {"nasdaq": 2}


def test_sqlite_path_is_anchored_to_the_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """★ 상대 경로를 그대로 두면 DB가 **현재 디렉터리**를 따라간다.

    `cd data && marketscan run`이 `data/data/marketscan.db`를 새로 만들고, 사용자는
    캐시와 신호가 통째로 비어 있는 것을 본다. 파일이 두 개 생겼다는 사실 자체가
    잘 안 보여서 진단이 어렵다.

    기준점은 `config_dir`이다 — DB도 사용자 자산이라 저장소와 수명을 같이하면
    안 된다 (`ohlcv_cache`는 무료 소스가 막혀도 남는 유일한 자산이다, §3.9).
    """
    from app.storage import db

    settings = get_settings()
    monkeypatch.setattr(settings, "config_dir", tmp_path / "home")
    db.configure("sqlite+aiosqlite:///./data/marketscan.db")
    try:
        monkeypatch.chdir(tmp_path)
        resolved = db.database_url()
    finally:
        db.configure(settings.database_url)

    assert resolved.endswith((tmp_path / "home/data/marketscan.db").as_posix())
    assert not resolved.endswith("./data/marketscan.db")  # cwd를 따라가지 않는다


def test_memory_and_absolute_urls_are_left_alone():
    from app.storage import db

    for url in ("sqlite+aiosqlite:///:memory:", "postgresql+asyncpg://h/db"):
        db.configure(url)
        try:
            assert db.database_url() == url
        finally:
            db.configure(get_settings().database_url)
