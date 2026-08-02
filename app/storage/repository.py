"""파이프라인 저장·조회.

저장은 항상 **새 버전을 추가**한다. 기존 스냅샷은 절대 수정하지 않는다.

**전략 소스의 SHA-256도 함께 박는다.** 전략이 파일이 되면서 생긴 구멍이다 —
파이프라인이 전략을 이름으로만 참조하면 그 파일을 고치는 순간 과거 버전이 무엇이었는지가
소급으로 바뀌고, 버전을 불변으로 둔 이유가 그대로 무너진다 (ARCHITECTURE.md 4.7 / 규칙 10).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pipeline import PipelineSpec
from app.storage.models import PipelineRow, PipelineVersionRow


class PipelineNotFoundError(LookupError):
    pass


@dataclass
class PipelineSummary:
    pipeline_id: str
    name: str
    version: int
    node_count: int
    enabled: bool
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "version": self.version,
            "node_count": self.node_count,
            "enabled": self.enabled,
            "updated_at": self.updated_at.isoformat(),
        }


def new_pipeline_id() -> str:
    return f"pipe_{uuid.uuid4().hex[:12]}"


async def save_pipeline(session: AsyncSession, spec: PipelineSpec) -> tuple[str, int]:
    """저장하고 `(pipeline_id, version)`을 돌려준다.

    `spec.pipeline_id`가 비어 있거나 없는 id면 새로 만들고, 이미 있으면 버전을 올린다.
    """
    pipeline_id = spec.pipeline_id.strip() or new_pipeline_id()
    row = await session.get(PipelineRow, pipeline_id)

    if row is None:
        row = PipelineRow(id=pipeline_id, name=spec.name, active_version=1)
        session.add(row)
        version = 1
    else:
        highest = await session.scalar(
            select(func.max(PipelineVersionRow.version)).where(
                PipelineVersionRow.pipeline_id == pipeline_id
            )
        )
        version = int(highest or 0) + 1
        row.name = spec.name
        row.active_version = version

    # 스냅샷에는 확정된 id/version을 박아 넣는다. 나중에 이 JSON만 보고도
    # 어떤 파이프라인의 몇 번째 버전인지 알 수 있어야 한다.
    snapshot = spec.model_copy(update={"pipeline_id": pipeline_id, "version": version})
    session.add(
        PipelineVersionRow(
            pipeline_id=pipeline_id,
            version=version,
            spec=snapshot.model_dump(mode="json"),
            strategy_hashes=_strategy_hashes(spec),
        )
    )
    await session.commit()
    return pipeline_id, version


def _strategy_hashes(spec: PipelineSpec) -> dict[str, str]:
    """이 버전이 참조하는 전략의 현재 소스 해시 (규칙 10).

    전략 파일을 못 읽으면 **비워 두지 않고 사유를 남긴다** — 해시가 조용히 빠지면
    "그때는 해시를 안 남겼구나"와 "그때 파일이 없었다"를 구분할 수 없다.
    """
    from app.strategies.base import StrategyError
    from app.strategies.registry import load_strategy

    hashes: dict[str, str] = {}
    for node in spec.nodes:
        strategy_id = node.params.get("strategy_id")
        if node.type != "strategyRunner" or not isinstance(strategy_id, str) or not strategy_id:
            continue
        try:
            hashes[strategy_id] = load_strategy(strategy_id).sha256
        except StrategyError as exc:
            hashes[strategy_id] = f"unavailable: {exc}"
    return hashes


async def list_pipelines(session: AsyncSession) -> list[PipelineSummary]:
    stmt = select(PipelineRow).order_by(PipelineRow.updated_at.desc())
    rows = (await session.scalars(stmt)).all()
    summaries: list[PipelineSummary] = []
    for row in rows:
        spec = await session.scalar(
            select(PipelineVersionRow.spec).where(
                PipelineVersionRow.pipeline_id == row.id,
                PipelineVersionRow.version == row.active_version,
            )
        )
        nodes = spec.get("nodes", []) if isinstance(spec, dict) else []
        summaries.append(
            PipelineSummary(
                pipeline_id=row.id,
                name=row.name,
                version=row.active_version,
                node_count=len(nodes),
                enabled=row.enabled,
                updated_at=row.updated_at,
            )
        )
    return summaries


async def load_pipeline(
    session: AsyncSession, pipeline_id: str, version: int | None = None
) -> PipelineSpec:
    """저장된 스냅샷을 PipelineSpec으로 되돌린다. 기본은 활성 버전."""
    row = await session.get(PipelineRow, pipeline_id)
    if row is None:
        raise PipelineNotFoundError(f"파이프라인을 찾을 수 없습니다: {pipeline_id}")

    target = version if version is not None else row.active_version
    spec = await session.scalar(
        select(PipelineVersionRow.spec).where(
            PipelineVersionRow.pipeline_id == pipeline_id,
            PipelineVersionRow.version == target,
        )
    )
    if spec is None:
        raise PipelineNotFoundError(f"버전을 찾을 수 없습니다: {pipeline_id} v{target}")
    return PipelineSpec.model_validate(spec)


async def list_versions(session: AsyncSession, pipeline_id: str) -> list[dict[str, object]]:
    rows = (
        await session.scalars(
            select(PipelineVersionRow)
            .where(PipelineVersionRow.pipeline_id == pipeline_id)
            .order_by(PipelineVersionRow.version.desc())
        )
    ).all()
    return [
        {"version": r.version, "created_at": r.created_at.isoformat()} for r in rows
    ]


async def delete_pipeline(session: AsyncSession, pipeline_id: str) -> None:
    row = await session.get(PipelineRow, pipeline_id)
    if row is None:
        raise PipelineNotFoundError(f"파이프라인을 찾을 수 없습니다: {pipeline_id}")
    await session.delete(row)
    await session.commit()
