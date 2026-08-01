"""저장소 계약 테스트 — 버전 불변성이 핵심이다."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.schemas.pipeline import EdgeSpec, NodeSpec, PipelineSpec
from app.storage import repository as repo
from app.storage.models import Base


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


async def test_load_missing_pipeline_raises(session):
    with pytest.raises(repo.PipelineNotFoundError):
        await repo.load_pipeline(session, "pipe_nope")


async def test_delete_removes_versions_too(session):
    pipeline_id, _ = await repo.save_pipeline(session, make_spec())
    await repo.save_pipeline(session, make_spec(pipeline_id))
    await repo.delete_pipeline(session, pipeline_id)

    assert await repo.list_pipelines(session) == []
    assert await repo.list_versions(session, pipeline_id) == []
