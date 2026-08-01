"""저장소 모델 (ARCHITECTURE.md 4.7).

핵심 규칙은 **버전 불변성**이다. 파이프라인을 저장하면 기존 행을 덮어쓰지 않고
새 버전 스냅샷을 만든다. 그래야 실행 중인 Run이 캔버스 편집에 영향받지 않고,
"그때 그 신호가 어떤 그래프에서 나왔는지"를 나중에 재현할 수 있다.

주의: 여기서 쓰는 `datetime.now(UTC)`는 저장 메타데이터용이다. 노드 로직의
시각은 반드시 `ctx.now`에서 와야 한다 (CLAUDE.md 규칙 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Dialect,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """항상 tz-aware UTC로 오가는 DateTime.

    SQLite는 `DateTime(timezone=True)`를 줘도 tzinfo를 저장하지 않는다. 그대로 두면
    읽을 때 naive datetime이 나오고, 프론트엔드의 `new Date(...)`가 그걸 **로컬 시각**으로
    해석해 표시 시각이 통째로 어긋난다. 여기서 경계를 막는다 (CLAUDE.md 규칙 4).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime은 저장할 수 없습니다. UTC로 변환해 넘기세요.")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class PipelineRow(Base):
    """파이프라인 메타. 실제 그래프는 pipeline_versions에 있다."""

    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    active_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    """스케줄 트리거 활성화 여부 (Phase 1)."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    versions: Mapped[list[PipelineVersionRow]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineVersionRow.version.desc()",
    )


class PipelineVersionRow(Base):
    """DAG JSON 스냅샷. **한 번 쓰면 수정하지 않는다.**"""

    __tablename__ = "pipeline_versions"
    __table_args__ = (UniqueConstraint("pipeline_id", "version", name="uq_pipeline_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pipelines.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    pipeline: Mapped[PipelineRow] = relationship(back_populates="versions")
