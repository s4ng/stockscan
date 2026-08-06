"""DAG JSON 스키마 (ARCHITECTURE.md 6).

실행 엔진이 받는 유일한 입력. `pipeline_versions`에 스냅샷으로 저장되며 실행
중에는 불변이다 — 실행 도중 파일을 고쳐도 진행 중인 Run은 영향받지 않는다.

⚠️ **정의 형식 자체가 미결정이다** (11장 4번). 그래프가 거의 직선이라 손으로 JSON을
적는 것이 고통스러우므로 TOML이 나을 수 있는데, **엔진은 그대로 두고 입력 형식만
바꾸는 것**이므로 이 모듈과 `runner.py`는 그대로 남는다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAIN = "main"
ERROR = "error"


class ExecutionMode(StrEnum):
    """실행 모드. 기본은 notify이며 실주문은 명시적으로 켜야 한다."""

    BACKTEST = "backtest"
    SHADOW = "shadow"
    """실시간으로 돌리되 알림을 보내지 않고 signals에만 기록 (분봉 전략 검증용)."""
    NOTIFY = "notify"
    PAPER = "paper"
    LIVE = "live"


class ErrorPolicy(StrEnum):
    FAIL = "fail"
    """실행 중단."""
    SKIP = "skip"
    """해당 노드를 skipped 처리하고 하위로 전파."""
    ROUTE = "route"
    """error 핸들로 오류를 내보내 별도 브랜치를 실행."""
    RETRY = "retry"
    """지수 백오프 재시도 후 fallback 정책으로."""


class OnError(BaseModel):
    policy: ErrorPolicy = ErrorPolicy.FAIL
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0)
    fallback: ErrorPolicy = ErrorPolicy.FAIL
    """retry가 모두 실패했을 때 적용할 정책."""

    @field_validator("fallback")
    @classmethod
    def _no_retry_fallback(cls, v: ErrorPolicy) -> ErrorPolicy:
        if v is ErrorPolicy.RETRY:
            raise ValueError("fallback은 retry일 수 없습니다 (무한 재시도)")
        return v


class NodeSpec(BaseModel):
    # v0.4의 `position`(캔버스 좌표)은 사라졌다. 캔버스가 없으므로 좌표를 저장할
    # 이유가 없다 (6장). 예전 정의에 남아 있어도 무시된다.
    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    on_error: OnError = Field(default_factory=OnError)


class EdgeSpec(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str = MAIN
    target_handle: str = MAIN


class PipelineSettings(BaseModel):
    user_timezone: str = "Asia/Seoul"
    """표시용 타임존. 저장은 항상 UTC."""

    default_mode: ExecutionMode = ExecutionMode.NOTIFY

    adjusted: bool = True
    """수정주가 사용 여부. 파이프라인 전역으로 고정하며 캐시 키에 포함된다."""

    max_concurrency: int = Field(default=4, ge=1, le=32)


class PipelineSpec(BaseModel):
    pipeline_id: str = ""
    """비어 있으면 저장 시 새 id가 발급된다."""

    name: str = ""
    version: int = 1
    settings: PipelineSettings = Field(default_factory=PipelineSettings)
    nodes: list[NodeSpec] = Field(default_factory=list)
    edges: list[EdgeSpec] = Field(default_factory=list)

    def node_by_id(self, node_id: str) -> NodeSpec | None:
        return next((n for n in self.nodes if n.id == node_id), None)
