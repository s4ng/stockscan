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
    """⚠️ **더 이상 쓰지 않는다** (2026-08-07). 읽는 코드도 쓰는 코드도 없다.

    한때 알림의 `[샀다/안 샀다]` 버튼이 채우던 값이다. 그 비교는 응답을 빠짐없이
    해야만 성립했는데 실제로는 산 것만 답하게 되어 **자기가 고른 분할**이 됐고,
    걷어냈다 (`app/alerts.py`의 "왜 지웠나").

    ★ **컬럼은 지우지 않는다.** `signals`는 자산이고 `create_all`은 기존 테이블의
    컬럼을 못 지운다 — 모델에서만 빼면 스키마와 모델이 어긋나 `DB 스키마가 모델보다
    낡았습니다`로 나온다. 기존 DB에 이미 들어 있는 응답도 남겨 둔다.
    """

    # ------------------------------------------------------------ 사후 수익률 (4.8)
    #
    # ★ 신호가 났다는 사실만 쌓으면 스크리너는 자신감 기계가 된다 — 사람은 맞은
    #   종목만 기억하기 때문이다. 그걸 막는 숫자가 여기 있고, 성적표와 알림의
    #   "최근 N건 승률"이 그 위에 선다.
    #
    # ⚠️ **아직 N봉이 안 지난 신호는 `NULL`로 둔다.** 0으로 채우면 최근 신호가 전부
    #   "수익률 0%"로 잡혀 통계가 조용히 희석된다 — 없는 숫자를 지어내지 않는다(12.3).
    #   집계는 `IS NOT NULL`로 거른다.
    fwd_1: Mapped[float | None] = mapped_column(Float, default=None)
    fwd_5: Mapped[float | None] = mapped_column(Float, default=None)
    fwd_20: Mapped[float | None] = mapped_column(Float, default=None)
    """`as_of`로부터 1·5·20 **봉** 뒤의 수익률 (비율, 0.021 = +2.1%).

    ⚠️ **날짜가 아니라 봉으로 센다.** 휴장일 때문에 "20일 뒤"와 "20봉 뒤"는 다르고,
    한국과 미국의 휴장일이 또 다르다. 날짜로 세면 시장마다 다른 것을 재게 된다.
    """

    fwd_base: Mapped[float | None] = mapped_column(Float, default=None)
    """수익률의 분모 — `as_of` 봉의 종가.

    **신호 당시 `meta`에 적힌 값이 아니라 캐시에서 다시 읽은 값이다.** 분자와 분모가
    같은 소스에서 나와야 수정주가 정책이 바뀌어도 비율이 어긋나지 않는다 (3.8).
    """

    bench_1: Mapped[float | None] = mapped_column(Float, default=None)
    bench_5: Mapped[float | None] = mapped_column(Float, default=None)
    bench_20: Mapped[float | None] = mapped_column(Float, default=None)
    """같은 구간 **그 시장 벤치마크**의 수익률 (KOSPI / S&P500).

    ★ **이것이 없으면 hit rate가 거짓말을 한다** — 상승장에서는 아무거나 찍어도
    승률이 60%를 넘는다. 빼고 봐야 "시장이 좋았던 것"과 "전략이 좋았던 것"이 갈린다.

    ⚠️ 신호와 **같은 봉 수**로 잰다. 날짜로 맞추면 휴장일이 다른 시장에서 어긋난다.
    """

    fwd_evaluated_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    """마지막으로 평가기가 훑은 시각. 아직 안 본 신호와 "보았지만 봉이 모자란"
    신호를 구분한다 — 구분하지 못하면 매번 전량을 다시 훑게 된다."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class OhlcvCacheRow(Base):
    """수집한 일봉. ★ **이 테이블은 지우지 않는다** (ARCHITECTURE.md 3.9).

    성능 최적화가 아니라 **영구 보관하는 데이터 자산**이다. 무료 소스는 언제든
    막히고, 막힌 뒤에 남는 것은 여기 쌓인 것뿐이다. 백업 대상에 포함한다.

    PK에 `adjusted`가 들어가는 것이 핵심이다 (3.8 / 규칙 8). 조정가와 비조정가가
    한 계열에 섞이면 지표가 조용히 어긋나고, 어긋난 지점을 사후에 찾을 수 없다.
    같은 이유로 `source_id`를 남긴다 — 폴백으로 소스가 바뀐 구간을 되짚는 유일한 단서다.
    """

    __tablename__ = "ohlcv_cache"

    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    adjusted: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    bar_time: Mapped[datetime] = mapped_column(UtcDateTime, primary_key=True)
    """봉의 **마감 시각** (UTC). 시가 시각이 아니다 (규칙 15)."""

    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)

    source_id: Mapped[str] = mapped_column(String(64))
    ingested_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class InstrumentRow(Base):
    """심볼 마스터 캐시 (ARCHITECTURE.md 4.7).

    ★ **거래대금은 여기 넣지 않는다.** 목록 응답에는 성격이 다른 두 가지가 섞여
    있는데, 마스터(심볼·이름·상장 여부)는 하루 이틀 낡아도 무해한 반면 **거래대금은
    캐시하는 순간 그날의 유니버스가 바뀐다** — 어제의 상위 60종목을 오늘 훑게 된다.
    그건 성능 문제가 아니라 판단이 달라지는 문제다.

    그래서 이 표가 아끼는 것은 **거래대금이 없는 목록**뿐이다. 미국 목록(FDR)이
    정확히 그 경우고, 매 실행마다 6,700행을 다시 받던 것이 여기서 멈춘다.
    """

    __tablename__ = "instruments"

    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)

    display_name: Mapped[str] = mapped_column(String(200), default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    """소스가 준 목록에서의 순서. `limit` 컷이 이 순서에 기댄다."""

    source_id: Mapped[str] = mapped_column(String(64), default="")
    refreshed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class IngestionJobRow(Base):
    """수집 대상 하나의 상태 (ARCHITECTURE.md 4.7 표).

    "언제 마지막으로 성공했는가"가 남아야 **소스가 조용히 죽은 것**을 안다.
    캐시에 봉이 있으면 파이프라인은 계속 도는데, 그 봉이 3주 전 것이라는 사실은
    여기를 보지 않으면 드러나지 않는다.
    """

    __tablename__ = "ingestion_jobs"

    venue: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    adjusted: Mapped[bool] = mapped_column(Boolean, primary_key=True)

    last_success_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    last_bar_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    last_source_id: Mapped[str | None] = mapped_column(String(64), default=None)
    bars: Mapped[int] = mapped_column(Integer, default=0)
    """마지막 수집에서 캐시에 새로 들어간 봉 수 (신규 + 갱신)."""

    lookback: Mapped[int] = mapped_column(Integer, default=0)
    """마지막 성공에서 **요청한** 봉 수.

    캐시에 든 봉이 이보다 적다면 **소스가 그 이상 주지 못한다**는 뜻이다 (신규 상장).
    이 값이 없으면 "아직 덜 모았다"와 "원래 이것뿐이다"를 구분할 수 없어서, 짧은
    이력 종목이 매 실행마다 소스를 다시 부른다.
    """

    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    """연속 실패 횟수. 성공하면 0으로 되돌린다."""

    last_error: Mapped[str | None] = mapped_column(String, default=None)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
