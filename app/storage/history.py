"""실행 이력·신호 저장 (ARCHITECTURE.md 4.7 / 4.9).

**여기 있는 것은 전부 부작용이다.** `--commit`이 붙은 실행에서만 호출된다
(규칙 11). 읽기 전용 명령(`explain` · `signals` · `stats`)은 아래 조회 함수만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.runner import RunResult
from app.engine.signals import SignalDraft
from app.schemas.pipeline import PipelineSpec
from app.storage.models import (
    NodeRunRow,
    RunRow,
    SignalRow,
    StrategyVersionRow,
)


class SqlSignalSink:
    """신호를 `signals`에 쓰는 배출구. `--commit`에서만 꽂힌다.

    `dedup_key` UNIQUE에 걸리면 **조용히 넘어가지 않고 False를 돌려준다** — 노드가
    "몇 건이 중복이었는지"를 로그에 남겨야 하기 때문이다. 중복 자체는 정상이다
    (3.5가 봉 커밋을 미루므로 겹치는 쪽이 잃는 쪽보다 안전하다).
    """

    persistent = True

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self.written = 0
        self.duplicates = 0
        self.drafts: list[SignalDraft] = []
        """실제로 기록된 신호. CLI가 dry-run과 같은 형태로 결과를 보여 주기 위해 쓴다."""

    async def emit(self, draft: SignalDraft) -> bool:
        async with self._sessionmaker() as session:
            session.add(
                SignalRow(
                    run_id=draft.run_id,
                    pipeline_id=draft.pipeline_id,
                    node_id=draft.node_id,
                    dedup_key=draft.dedup_key,
                    instrument=draft.instrument,
                    venue=draft.venue,
                    timeframe=draft.timeframe,
                    as_of=draft.as_of,
                    kind=draft.kind,
                    strategy_id=draft.strategy_id,
                    strategy_sha256=draft.strategy_sha256,
                    features=draft.features,
                    tags=draft.tags,
                    meta=draft.meta,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                self.duplicates += 1
                return False
        self.written += 1
        self.drafts.append(draft)
        return True


@dataclass
class RunRecord:
    """조회 결과. CLI가 그대로 직렬화한다."""

    run_id: str
    pipeline_id: str
    mode: str
    as_of: datetime
    status: str
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "mode": self.mode,
            "as_of": self.as_of.isoformat(),
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


# --------------------------------------------------------------------- 쓰기 (commit)
async def start_run(
    session: AsyncSession,
    *,
    run_id: str,
    spec: PipelineSpec,
    mode: str,
    as_of: datetime,
) -> None:
    """실행 시작을 먼저 기록한다.

    신호가 `runs`를 참조하므로 순서가 강제된다. 도중에 프로세스가 죽어도
    `status="running"`인 행이 남아 "여기서 끊겼다"를 알 수 있다.
    """
    session.add(
        RunRow(
            id=run_id,
            pipeline_id=spec.pipeline_id,
            pipeline_version=spec.version,
            mode=mode,
            as_of=as_of,
            status="running",
        )
    )
    await session.commit()


async def finish_run(session: AsyncSession, result: RunResult) -> None:
    """실행 결과와 노드별 스냅샷을 기록한다 (4.9)."""
    row = await session.get(RunRow, result.run_id)
    if row is None:  # pragma: no cover - start_run이 항상 선행한다
        return
    row.status = str(result.status)
    row.error = result.error
    row.finished_at = datetime.now(UTC)

    for node in result.nodes:
        session.add(
            NodeRunRow(
                run_id=result.run_id,
                node_id=node.node_id,
                type=node.type,
                status=str(node.status),
                duration_ms=node.duration_ms,
                inputs=node.inputs,
                outputs=node.outputs,
                logs=node.logs,
                error=node.error,
            )
        )
    await session.commit()


async def set_acted(session: AsyncSession, signal_id: int, acted: bool) -> SignalRow | None:
    """이 신호대로 움직였는지 기록한다 (4.8 오버라이드 추적).

    ★ 이 시스템의 정체성이 규율 기계라면, 측정할 것은 전략 성과만이 아니라
    **사용자가 규율을 지켰는지**다. 무시한 신호의 사후 성과가 좋았다면 재량이
    손해라는 뜻이고, 그 비교는 이 값이 채워져야만 가능하다.

    되돌릴 수 있다 — 잘못 눌렀으면 반대로 다시 부르면 된다. 봉 소비와 달리
    이 값은 무엇도 영영 잃게 만들지 않는다.
    """
    row = await session.get(SignalRow, signal_id)
    if row is None:
        return None
    row.acted = acted
    await session.commit()
    return row


async def snapshot_strategy(
    session: AsyncSession, *, strategy_id: str, sha256: str, source: str
) -> None:
    """전략 소스 전문을 해시 기준으로 한 벌 보관한다 (4.7).

    파일을 지우거나 고쳐도 과거 실행이 어떤 코드로 돌았는지 되짚을 수 있어야 한다.
    """
    if await session.get(StrategyVersionRow, sha256) is not None:
        return
    session.add(StrategyVersionRow(sha256=sha256, strategy_id=strategy_id, source=source))
    await session.commit()


# ------------------------------------------------------------------ 읽기 (부작용 없음)
async def list_signals(
    session: AsyncSession,
    *,
    limit: int = 20,
    strategy_id: str | None = None,
    venue: str | None = None,
    acted: bool | None = None,
) -> list[SignalRow]:
    stmt = select(SignalRow).order_by(SignalRow.as_of.desc(), SignalRow.id.desc()).limit(limit)
    if strategy_id:
        stmt = stmt.where(SignalRow.strategy_id == strategy_id)
    if venue:
        stmt = stmt.where(SignalRow.venue == venue)
    if acted is not None:
        stmt = stmt.where(SignalRow.acted.is_(acted))
    return list((await session.scalars(stmt)).all())


async def get_signal(session: AsyncSession, signal_id: int) -> SignalRow | None:
    return await session.get(SignalRow, signal_id)


async def get_run(session: AsyncSession, run_id: str) -> RunRow | None:
    return await session.get(RunRow, run_id)


async def get_node_runs(session: AsyncSession, run_id: str) -> list[NodeRunRow]:
    stmt = select(NodeRunRow).where(NodeRunRow.run_id == run_id).order_by(NodeRunRow.id)
    return list((await session.scalars(stmt)).all())


async def last_run(session: AsyncSession) -> RunRecord | None:
    row = await session.scalar(select(RunRow).order_by(RunRow.started_at.desc()).limit(1))
    if row is None:
        return None
    return RunRecord(
        run_id=row.id,
        pipeline_id=row.pipeline_id,
        mode=row.mode,
        as_of=row.as_of,
        status=row.status,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


async def signal_counts(
    session: AsyncSession, group_by: str = "strategy"
) -> list[dict[str, Any]]:
    """신호 건수 집계.

    ⚠️ 여기서 내보내는 것은 **건수와 분산뿐이다.** forward return·hit rate·IC는
    Forward Return Evaluator가 사후 수익률을 채운 뒤에야 계산할 수 있고, 그건
    Phase 3이다. 없는 숫자를 만들어 내지 않는다 (4.8).
    """
    column = {
        "strategy": SignalRow.strategy_id,
        "venue": SignalRow.venue,
        "instrument": SignalRow.instrument,
        "timeframe": SignalRow.timeframe,
    }.get(group_by)
    if column is None:
        raise ValueError(
            f"알 수 없는 집계 기준: {group_by!r}. "
            f"사용 가능: strategy, venue, instrument, timeframe"
        )

    stmt = (
        select(column, func.count(SignalRow.id), func.max(SignalRow.as_of))
        .group_by(column)
        .order_by(func.count(SignalRow.id).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "group": key if key is not None else "(없음)",
            "signals": int(count),
            "latest_as_of": latest.isoformat() if latest else None,
        }
        for key, count, latest in rows
    ]


async def acted_breakdown(session: AsyncSession) -> dict[str, int]:
    """오버라이드 추적의 원자료 (4.8). 실행/무시/미응답 건수."""
    stmt = select(SignalRow.acted, func.count(SignalRow.id)).group_by(SignalRow.acted)
    rows = (await session.execute(stmt)).all()
    out = {"acted": 0, "ignored": 0, "unanswered": 0}
    for acted, count in rows:
        key = "unanswered" if acted is None else ("acted" if acted else "ignored")
        out[key] = int(count)
    return out
