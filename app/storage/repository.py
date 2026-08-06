"""설정 버전 저장·조회 (ARCHITECTURE.md 4.7 / 규칙 10).

저장은 항상 **새 버전을 추가**한다. 기존 스냅샷은 절대 수정하지 않는다 —
"그날 무엇으로 돌았는가"가 소급으로 바뀌면 `explain`이 거짓말을 하게 된다.

**전략 소스의 SHA-256도 함께 박는다.** 전략이 파일이 되면서 생긴 구멍이다 —
설정이 전략을 이름으로만 참조하면 그 파일을 고치는 순간 과거 버전이 무엇이었는지가
소급으로 바뀌고, 버전을 불변으로 둔 이유가 그대로 무너진다.

⚠️ **2026-08-06 전까지 이 모듈은 테스트에서만 불렸다.** DAG를 걷어내면서
`execute_run`의 커밋 경로에 물렸다 — 규칙 10이 문서에만 있고 코드에는 없었던 것이다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AppConfig
from app.storage.models import PipelineRow, PipelineVersionRow


class PipelineNotFoundError(LookupError):
    pass


@dataclass
class ConfigSummary:
    pipeline_id: str
    name: str
    version: int
    enabled: bool
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "version": self.version,
            "enabled": self.enabled,
            "updated_at": self.updated_at.isoformat(),
        }


def new_pipeline_id() -> str:
    return f"pipe_{uuid.uuid4().hex[:12]}"


async def save_config(session: AsyncSession, config: AppConfig) -> tuple[str, int]:
    """설정을 저장하고 `(pipeline_id, version)`을 돌려준다.

    **내용이 직전 버전과 같으면 새 버전을 만들지 않는다.** 매 실행마다 같은 설정을
    한 줄씩 쌓으면 버전 번호가 실행 횟수가 되어 "언제 설정이 바뀌었나"를 잃는다.
    """
    pipeline_id = config.pipeline_id
    snapshot = config.snapshot()
    hashes = _strategy_hashes(config)
    row = await session.get(PipelineRow, pipeline_id)

    if row is None:
        session.add(PipelineRow(id=pipeline_id, name=_name_of(config), active_version=1))
        version = 1
    else:
        latest = await session.scalar(
            select(PipelineVersionRow)
            .where(PipelineVersionRow.pipeline_id == pipeline_id)
            .order_by(PipelineVersionRow.version.desc())
            .limit(1)
        )
        if latest is not None and latest.spec == snapshot and latest.strategy_hashes == hashes:
            return pipeline_id, latest.version  # 바뀐 것이 없다

        highest = await session.scalar(
            select(func.max(PipelineVersionRow.version)).where(
                PipelineVersionRow.pipeline_id == pipeline_id
            )
        )
        version = int(highest or 0) + 1
        row.name = _name_of(config)
        row.active_version = version

    session.add(
        PipelineVersionRow(
            pipeline_id=pipeline_id,
            version=version,
            spec=snapshot,
            strategy_hashes=hashes,
        )
    )
    await session.commit()
    return pipeline_id, version


def _name_of(config: AppConfig) -> str:
    return config.strategy or "(이름 없음)"


def _strategy_hashes(config: AppConfig) -> dict[str, str]:
    """이 버전이 참조하는 전략의 현재 소스 해시 (규칙 10).

    전략 파일을 못 읽으면 **비워 두지 않고 사유를 남긴다** — 해시가 조용히 빠지면
    "그때는 해시를 안 남겼구나"와 "그때 파일이 없었다"를 구분할 수 없다.
    """
    from app.strategies.base import StrategyError
    from app.strategies.registry import load_strategy

    if not config.strategy:
        return {}
    try:
        return {config.strategy: load_strategy(config.strategy).sha256}
    except StrategyError as exc:
        return {config.strategy: f"unavailable: {exc}"}


async def list_configs(session: AsyncSession) -> list[ConfigSummary]:
    stmt = select(PipelineRow).order_by(PipelineRow.updated_at.desc())
    rows = (await session.scalars(stmt)).all()
    return [
        ConfigSummary(
            pipeline_id=row.id,
            name=row.name,
            version=row.active_version,
            enabled=row.enabled,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


async def load_config(
    session: AsyncSession, pipeline_id: str, version: int | None = None
) -> AppConfig:
    """저장된 스냅샷을 `AppConfig`로 되돌린다. 기본은 활성 버전.

    ⚠️ 스냅샷에는 토큰이 없다(`AppConfig.snapshot()`이 뺀다). 되돌린 설정으로
    알림을 보내려 하면 채널이 열리지 않는데, **그게 맞는 동작이다** — 실행 이력은
    백업·공유 대상이라 비밀이 거기까지 따라가면 안 된다.
    """
    row = await session.get(PipelineRow, pipeline_id)
    if row is None:
        raise PipelineNotFoundError(f"설정을 찾을 수 없습니다: {pipeline_id}")

    target = version if version is not None else row.active_version
    spec = await session.scalar(
        select(PipelineVersionRow.spec).where(
            PipelineVersionRow.pipeline_id == pipeline_id,
            PipelineVersionRow.version == target,
        )
    )
    if spec is None:
        raise PipelineNotFoundError(f"버전을 찾을 수 없습니다: {pipeline_id} v{target}")
    return AppConfig.model_validate(spec)


async def list_versions(session: AsyncSession, pipeline_id: str) -> list[dict[str, object]]:
    rows = (
        await session.scalars(
            select(PipelineVersionRow)
            .where(PipelineVersionRow.pipeline_id == pipeline_id)
            .order_by(PipelineVersionRow.version.desc())
        )
    ).all()
    return [{"version": r.version, "created_at": r.created_at.isoformat()} for r in rows]
