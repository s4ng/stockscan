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
    Float,
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

    #: 이 버전이 참조한 전략 소스의 SHA-256 (strategy_id → sha256).
    #: 전략이 파일이 되면서 생긴 구멍을 막는다 — 파일을 고치면 과거 버전의 의미가
    #: 소급으로 바뀌기 때문이다 (ARCHITECTURE.md 4.7 / 규칙 10).
    strategy_hashes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    pipeline: Mapped[PipelineRow] = relationship(back_populates="versions")


class StrategyVersionRow(Base):
    """전략 소스 스냅샷. 해시가 같으면 같은 행을 재사용한다 (ARCHITECTURE.md 4.7).

    파이프라인 버전은 해시만 들고 있고, 전문은 여기 한 벌만 둔다. 파일을 지우거나
    고쳐도 과거 실행이 어떤 코드로 돌았는지 되짚을 수 있어야 한다.
    """

    __tablename__ = "strategy_versions"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class RunRow(Base):
    """실행 1회. `--commit`이 붙은 실행만 기록된다 (규칙 11)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String(64), index=True)
    pipeline_version: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(16))

    as_of: Mapped[datetime] = mapped_column(UtcDateTime)
    """이 실행이 기준으로 삼은 시각(`ctx.now`). 재현할 때 이 값을 그대로 넣는다."""

    status: Mapped[str] = mapped_column(String(16), default="running")
    error: Mapped[str | None] = mapped_column(String, default=None)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    node_runs: Mapped[list[NodeRunRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class NodeRunRow(Base):
    """노드별 입·출력 요약. "왜 이 신호가 나왔는가"를 사후 재현하는 근거 (4.9)."""

    __tablename__ = "node_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    logs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(String, default=None)

    run: Mapped[RunRow] = relationship(back_populates="node_runs")


class SignalRow(Base):
    """생성된 신호. `explain` · `signals list` · `stats`가 읽는 테이블."""

    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_signal_dedup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    pipeline_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(String(64))

    dedup_key: Mapped[str] = mapped_column(String(64))
    """sha256(pipeline_id | node_id | instrument | as_of | kind).

    같은 캔들 기준 신호는 한 번만 남는다. 재시도·수동 재실행·자동 실행 중복이
    겹쳐도 이력이 부풀지 않는다 (4.5).
    """

    instrument: Mapped[str] = mapped_column(String(64), index=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="entry")

    strategy_id: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    strategy_sha256: Mapped[str | None] = mapped_column(String(64), default=None)

    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    acted: Mapped[bool | None] = mapped_column(Boolean, default=None)
    """사용자가 이 신호대로 움직였는가.

    ★ 이 시스템의 정체성이 규율 기계라면 측정할 것은 전략 성과만이 아니라 **사용자가
    규율을 지켰는지**다. 무시한 신호의 사후 성과가 좋았다면 재량이 손해라는 뜻이다
    (4.8 오버라이드 추적). null은 아직 응답하지 않음.
    """

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
