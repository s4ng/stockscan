"""저장소 계약 테스트 — 버전 불변성이 핵심이다."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.schemas.pipeline import EdgeSpec, NodeSpec, PipelineSpec
from app.storage import repository as repo
from app.storage.models import Base, PipelineVersionRow


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def make_spec(pipeline_id: str = "", name: str = "테스트", nodes: int = 2) -> PipelineSpec:
    return PipelineSpec(
        pipeline_id=pipeline_id,
        name=name,
        nodes=[NodeSpec(id=f"n{i}", type="manualTrigger") for i in range(nodes)],
        edges=[EdgeSpec(id="e1", source="n0", target="n1")] if nodes > 1 else [],
    )


async def test_save_new_pipeline_assigns_id(session):
    pipeline_id, version = await repo.save_pipeline(session, make_spec())
    assert pipeline_id.startswith("pipe_")
    assert version == 1


async def test_save_existing_pipeline_bumps_version(session):
    pipeline_id, _ = await repo.save_pipeline(session, make_spec())
    _, v2 = await repo.save_pipeline(session, make_spec(pipeline_id, nodes=3))
    _, v3 = await repo.save_pipeline(session, make_spec(pipeline_id, nodes=4))
    assert (v2, v3) == (2, 3)


async def test_old_versions_are_immutable(session):
    """새 버전을 저장해도 예전 스냅샷은 그대로 남아야 한다."""
    pipeline_id, _ = await repo.save_pipeline(session, make_spec(nodes=2))
    await repo.save_pipeline(session, make_spec(pipeline_id, name="바뀐 이름", nodes=5))

    old = await repo.load_pipeline(session, pipeline_id, version=1)
    new = await repo.load_pipeline(session, pipeline_id)

    assert len(old.nodes) == 2
    assert old.name == "테스트"
    assert len(new.nodes) == 5
    assert new.name == "바뀐 이름"


async def test_snapshot_records_its_own_id_and_version(session):
    pipeline_id, _ = await repo.save_pipeline(session, make_spec())
    await repo.save_pipeline(session, make_spec(pipeline_id))

    loaded = await repo.load_pipeline(session, pipeline_id, version=2)
    assert loaded.pipeline_id == pipeline_id
    assert loaded.version == 2


async def test_list_pipelines_reports_active_version(session):
    pipeline_id, _ = await repo.save_pipeline(session, make_spec(nodes=2))
    await repo.save_pipeline(session, make_spec(pipeline_id, nodes=7))

    summaries = await repo.list_pipelines(session)
    assert len(summaries) == 1
    assert summaries[0].version == 2
    assert summaries[0].node_count == 7


async def test_list_versions_is_newest_first(session):
    pipeline_id, _ = await repo.save_pipeline(session, make_spec())
    await repo.save_pipeline(session, make_spec(pipeline_id))
    versions = await repo.list_versions(session, pipeline_id)
    assert [v["version"] for v in versions] == [2, 1]


async def test_timestamps_round_trip_as_utc_aware(session):
    """SQLite는 tzinfo를 저장하지 않는다. naive로 새어 나오면 프론트가 로컬 시각으로
    오해해 표시가 통째로 어긋난다 (CLAUDE.md 규칙 4)."""
    pipeline_id, _ = await repo.save_pipeline(session, make_spec())

    summaries = await repo.list_pipelines(session)
    updated_at = summaries[0].updated_at
    assert updated_at.tzinfo is not None
    assert updated_at.utcoffset() == timedelta(0)
    assert summaries[0].to_dict()["updated_at"].endswith("+00:00")

    versions = await repo.list_versions(session, pipeline_id)
    assert str(versions[0]["created_at"]).endswith("+00:00")


async def test_strategy_hash_is_pinned_to_the_version(session):
    """전략을 이름으로만 참조하면 파일을 고치는 순간 과거 버전의 의미가 소급으로
    바뀐다 (CLAUDE.md 규칙 10 / §4.7)."""
    spec = PipelineSpec(
        pipeline_id="pipe_s",
        nodes=[
            NodeSpec(
                id="s", type="strategyRunner", params={"strategy_id": "demo_momentum"}
            )
        ],
    )
    await repo.save_pipeline(session, spec)

    stored = await session.scalar(
        select(PipelineVersionRow.strategy_hashes).where(
            PipelineVersionRow.pipeline_id == "pipe_s"
        )
    )
    assert len(stored["demo_momentum"]) == 64


async def test_missing_strategy_records_the_reason_not_silence(session):
    """해시가 조용히 빠지면 '기록을 안 했다'와 '파일이 없었다'를 구분할 수 없다."""
    spec = PipelineSpec(
        pipeline_id="pipe_missing",
        nodes=[NodeSpec(id="s", type="strategyRunner", params={"strategy_id": "없는전략"})],
    )
    await repo.save_pipeline(session, spec)

    stored = await session.scalar(
        select(PipelineVersionRow.strategy_hashes).where(
            PipelineVersionRow.pipeline_id == "pipe_missing"
        )
    )
    assert stored["없는전략"].startswith("unavailable:")


async def test_load_missing_pipeline_raises(session):
    with pytest.raises(repo.PipelineNotFoundError):
        await repo.load_pipeline(session, "pipe_nope")


async def test_delete_removes_versions_too(session):
    pipeline_id, _ = await repo.save_pipeline(session, make_spec())
    await repo.save_pipeline(session, make_spec(pipeline_id))
    await repo.delete_pipeline(session, pipeline_id)

    assert await repo.list_pipelines(session) == []
    assert await repo.list_versions(session, pipeline_id) == []


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
