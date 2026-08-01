"""REST API.

프론트엔드는 `GET /api/nodes`의 JSON Schema를 읽어 팔레트와 파라미터 폼을
자동 생성한다. 따라서 노드를 추가해도 프론트엔드 코드를 고칠 필요가 없다.

⚠️ 라우트 선언 순서가 중요하다. `/pipelines/validate` 같은 고정 경로는 반드시
`/pipelines/{pipeline_id}`보다 **먼저** 선언해야 한다. FastAPI는 선언 순서대로
매칭하므로, 순서가 바뀌면 "validate"가 pipeline_id로 잡힌다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.context import RunContext
from app.engine.graph import PipelineValidationError, execution_levels, validate
from app.engine.runner import execute
from app.nodes.registry import catalog
from app.schemas.pipeline import PipelineSpec, RunRequest
from app.storage import repository as repo
from app.storage.db import get_session

router = APIRouter(prefix="/api")
UTC = ZoneInfo("UTC")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/nodes")
async def list_nodes() -> dict[str, Any]:
    """노드 카탈로그. 프론트엔드 팔레트와 폼의 단일 출처."""
    return {"nodes": catalog()}


# --------------------------------------------------------------------- 고정 경로
@router.post("/pipelines/validate")
async def validate_pipeline(spec: PipelineSpec) -> dict[str, Any]:
    result = validate(spec)
    payload = result.to_dict()
    if result.ok:
        payload["levels"] = execution_levels(spec)
    return payload


@router.post("/pipelines/run")
async def run_pipeline(request: RunRequest) -> dict[str, Any]:
    """파이프라인을 한 번 실행하고 노드별 실행 기록을 돌려준다."""
    ctx = RunContext.create(
        settings=request.pipeline.settings,
        mode=request.mode,
        now=_parse_now(request.now),
    )
    try:
        result = await execute(request.pipeline, ctx)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.result.to_dict()) from exc
    return result.to_dict()


# ------------------------------------------------------------------- 저장·불러오기
@router.get("/pipelines")
async def list_pipelines(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    summaries = await repo.list_pipelines(session)
    return {"pipelines": [s.to_dict() for s in summaries]}


@router.post("/pipelines")
async def save_pipeline(
    spec: PipelineSpec, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """저장한다. 기존 파이프라인이면 **새 버전**을 만든다 (스냅샷은 불변).

    검증에 실패해도 저장은 허용한다 — 작업 중인 그래프를 못 저장하면 쓸 수 없다.
    실행 시점에 `/pipelines/run`이 막는다.
    """
    pipeline_id, version = await repo.save_pipeline(session, spec)
    return {"pipeline_id": pipeline_id, "version": version, "name": spec.name}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(
    pipeline_id: str,
    version: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        spec = await repo.load_pipeline(session, pipeline_id, version)
    except repo.PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return spec.model_dump(mode="json")


@router.get("/pipelines/{pipeline_id}/versions")
async def get_pipeline_versions(
    pipeline_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return {"versions": await repo.list_versions(session, pipeline_id)}


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(
    pipeline_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    try:
        await repo.delete_pipeline(session, pipeline_id)
    except repo.PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}


def _parse_now(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"now는 ISO8601 형식이어야 합니다: {raw!r}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
