"""실행 서비스 — 명령의 **본체**. CLI도 웹 UI도 여기를 통해서만 실행한다.

CLAUDE.md가 정한 것: **"`run --commit`을 무엇이 부르든 동작이 같아야 한다."**
부르는 창구가 둘(터미널·브라우저)이 되면서 이 문장이 실제로 시험대에 올랐다.
오케스트레이션을 양쪽에 각각 적으면 언젠가 한쪽만 바뀌고, 그날 **화면에서 누른
실행과 터미널에서 친 실행이 다른 일을 한다.** `strategies/stages.py`가 전략 단계
순서에 대해 하는 일을 이 모듈이 명령 전체에 대해 한다.

여기 있는 것은 **부작용의 순서와 조건**이고, 없는 것은 **표현**이다 — 표·종료 코드·
HTML은 부르는 쪽의 몫이다. 그래서 이 모듈은 `Out`도 `typer`도 모른다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.backtest import ReplayResult, replay
from app.cli import pipeline_file
from app.engine.context import RunContext
from app.engine.runner import RunResult, execute
from app.engine.signals import CollectingSink
from app.engine.state import InMemoryBarState
from app.ingest import worker
from app.market.instrument import InstrumentRef
from app.providers.ohlcv_source import CachedSource, DirectSource
from app.providers.universe_source import CachedUniverse, DirectUniverse
from app.report.backtest_report import report_path as backtest_report_path
from app.report.backtest_report import write_backtest_report
from app.report.run_report import ReportInput, report_path, write_run_report
from app.schemas.pipeline import ExecutionMode, PipelineSpec
from app.storage import db, history, instruments
from app.storage.bar_state import SqlBarState, sqlite_path
from app.storage.history import SqlSignalSink
from app.strategies.registry import StrategyError, load_strategy

UTC = ZoneInfo("UTC")

#: 진행 상황·경고를 받는 창구. CLI는 stderr로, 웹은 화면 배너로 보낸다.
Notify = Callable[[str], None]

ErrorKind = Literal["validation", "data"]


def _silent(_: str) -> None:
    """알림을 받지 않겠다는 선택. 기본값이다."""


class ServiceError(RuntimeError):
    """실행을 계속할 수 없을 때. `kind`가 CLI 종료 코드·HTTP 상태로 번역된다.

    부르는 쪽이 종료 코드를 알아야 하므로 예외에 종류를 싣는다 — 메시지 문자열을
    보고 분기하게 두면 문구를 고치는 순간 자동 실행의 판단이 바뀐다 (12.3).
    """

    def __init__(self, message: str, *, kind: ErrorKind = "validation") -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind


# ===================================================================== 실행 자원
def ohlcv_source(providers: Any, spec: PipelineSpec) -> Any:
    """봉을 어디서 얻을지 고른다 (3.9).

    ★ **dry-run도 캐시에 쓴다.** 규칙 11이 막는 것은 **되돌릴 수 없는 것** 셋이다 —
    알림 발송, `signals` 기록, 봉 소비. 봉을 캐시에 넣는 것은 그중 어느 것도 아니다:
    판단이 아니라 **자료 축적**이고, 어차피 `ingest`가 쓸 것을 미리 쓰는 것뿐이다.
    받아 놓고 버리면 무료 API를 두 번 두드리게 되는데, 그 호출을 아끼는 것이 3.9의
    존재 이유다.
    """
    if sqlite_path(db.database_url()) is None:
        return DirectSource(providers, default_adjusted=spec.settings.adjusted)
    return CachedSource(
        providers,
        db.get_sessionmaker(),
        writable=True,
        default_adjusted=spec.settings.adjusted,
    )


def universe_source(providers: Any, now: datetime) -> Any:
    """종목 목록을 어디서 얻을지 고른다 (4.7).

    캐시가 아끼는 것은 **거래대금이 없는 목록**뿐이다 — `top_by_turnover`를 거는
    venue는 노드가 `needs_turnover=True`로 불러 캐시를 건너뛴다.
    """
    if sqlite_path(db.database_url()) is None:
        return DirectUniverse(providers)
    return CachedUniverse(providers, db.get_sessionmaker(), now=now)


def bar_state(pipeline_id: str, commit: bool, warn: Notify = _silent) -> Any:
    """실행에 맞는 봉 상태 저장소를 고른다 (3.5).

    dry-run에서도 **읽기는 한다.** 읽지 않으면 `run`과 `run --commit`이 서로 다른
    종목 집합을 보게 되어, dry-run이 실제 실행을 예측하지 못한다.
    """
    path = sqlite_path(db.database_url())
    if path is None:
        warn(
            "SQLite가 아니어서 Fresh Bar Gate가 프로세스 메모리에만 남습니다 — "
            "같은 봉이 반복 판정될 수 있습니다 (중복 신호는 dedup_key가 막습니다)."
        )
        return InMemoryBarState()
    if not commit and not path.exists():
        # 읽기 전용 실행이 DB 파일을 만들면 규칙 11이 깨진다 (12.1).
        return InMemoryBarState()
    return SqlBarState(path, pipeline_id, readonly=not commit)


def database_exists() -> bool:
    """읽기 전용 명령이 DB 파일을 **만들지 않도록** 먼저 확인한다 (12.1)."""
    url = db.database_url()
    if not url.startswith("sqlite"):
        return True
    path = url.split("///")[-1]
    return path == ":memory:" or Path(path).exists()


# ============================================================================ run
@dataclass
class RunOutcome:
    result: RunResult
    ctx: RunContext
    signals: list[dict[str, Any]]
    """이 실행이 낸 신호 **전부**. 잘라내는 것은 부르는 쪽의 몫이다."""

    committed: bool
    written: int
    report: Path | None = None


async def execute_run(
    spec: PipelineSpec,
    *,
    mode: ExecutionMode | None = None,
    now: datetime | None = None,
    commit: bool = False,
    allow_alerts: bool = False,
    warn: Notify = _silent,
) -> RunOutcome:
    """파이프라인을 실행한다. **부작용은 `commit`이 있을 때만** 열린다 (규칙 11).

    ⚠️ `allow_alerts`는 **스케줄 실행만** 켠다. 사람이 손으로(터미널이든 화면의
    버튼이든) 부른 실행에서 채널로 메시지가 나가면 알림 자체를 믿지 않게 된다
    (12.2). 기본값이 False인 이유이고, 웹 UI도 이 기본값을 그대로 쓴다.
    """
    resolved_mode = mode or spec.settings.default_mode

    sink: Any = CollectingSink()
    # 캐시 쓰기는 dry-run에서도 열려 있으므로(`ohlcv_source`) 테이블을 먼저 만든다.
    await db.init_db()
    if commit and resolved_mode is not ExecutionMode.BACKTEST:
        sink = SqlSignalSink(db.get_sessionmaker())

    ctx = RunContext.create(
        settings=spec.settings,
        mode=resolved_mode,
        now=now,
        pipeline_id=spec.pipeline_id,
        bar_state=bar_state(spec.pipeline_id, commit, warn),
        signals=sink,
        commit=commit,
        allow_alerts=allow_alerts,
    )
    # 레지스트리는 RunContext가 만든 것을 그대로 쓴다 — 소스 구성의 단일 출처가
    # 한 곳이어야 테스트가 네트워크를 막을 수 있다 (tests/conftest.py).
    ctx.ohlcv = ohlcv_source(ctx.providers, spec)
    ctx.universe = universe_source(ctx.providers, ctx.now)

    if commit:
        async with db.session_scope() as session:
            await history.start_run(
                session, run_id=ctx.run_id, spec=spec, mode=str(resolved_mode), as_of=ctx.now
            )
            await snapshot_strategies(session, spec)

    try:
        result = await execute(spec, ctx)
        # 노드별 스냅샷이 있어야 `explain`이 "왜 이 신호가 났는가"를 돌려준다 (4.9).
        if commit:
            async with db.session_scope() as session:
                await history.finish_run(session, result)
    finally:
        # CCXT가 연 aiohttp 세션을 닫는다. 남기면 종료 시 경고가 뜬다.
        await ctx.providers.close()
        closer = getattr(ctx.bar_state, "close", None)
        if closer is not None:
            closer()
        # dry-run도 캐시를 읽느라 엔진을 열었을 수 있다. 엔진이 없으면 무해하다.
        await db.dispose()

    drafts = getattr(sink, "drafts", [])
    return RunOutcome(
        result=result,
        ctx=ctx,
        signals=[d.to_dict() for d in drafts],
        committed=commit,
        written=getattr(sink, "written", len(drafts)),
    )


async def snapshot_strategies(session: Any, spec: PipelineSpec) -> None:
    """전략 소스 전문을 해시 기준으로 보관한다 (4.7).

    파일을 고치면 과거 실행의 근거가 소급으로 바뀌므로, 커밋 실행마다 그 시점의
    코드를 남긴다. 이미 있는 해시면 아무 일도 하지 않는다.
    """
    for strategy_id in pipeline_file.strategy_ids(spec):
        try:
            loaded = load_strategy(strategy_id)
        except StrategyError:
            continue  # 실행 단계에서 노드가 제대로 된 오류를 낸다
        await history.snapshot_strategy(
            session,
            strategy_id=strategy_id,
            sha256=loaded.sha256,
            source=loaded.source.path.read_text(encoding="utf-8"),
        )


def write_report(outcome: RunOutcome, spec: PipelineSpec, warn: Notify = _silent) -> Path | None:
    """정적 HTML 리포트를 남긴다.

    파일 쓰기지만 `--commit` 뒤에 두지 않았다. 재생성 가능하고 무엇도 되돌릴 수
    없게 만들지 않기 때문이다. 실패해도 실행을 실패로 만들지 않는다 — 리포트는
    산출물이지 판단이 아니다.
    """
    try:
        return write_run_report(
            ReportInput(
                result=outcome.result,
                signals=outcome.signals,
                committed=outcome.committed,
                pipeline_name=spec.name,
                user_timezone=spec.settings.user_timezone,
            ),
            report_path(outcome.result.run_id, committed=outcome.committed),
        )
    except OSError as exc:
        warn(f"리포트를 쓰지 못했습니다 ({exc}). 실행 결과 자체는 유효합니다.")
        return None


# ======================================================================= backtest
async def execute_backtest(
    spec: PipelineSpec,
    *,
    instrument: str,
    start: date,
    end: date,
    strategy_id: str | None = None,
    warn: Notify = _silent,
    progress: Notify = _silent,
) -> ReplayResult:
    """한 종목을 날짜별로 되감아 전략을 다시 돌린다 (12.7). **부작용 없음.**"""
    if end < start:
        raise ServiceError(f"종료일({end})이 시작일({start})보다 빠릅니다.")

    chosen, raw_params = backtest_strategy(spec, strategy_id, warn)
    try:
        loaded = load_strategy(chosen)
        params = loaded.strategy.Params.model_validate(raw_params)
    except StrategyError as exc:
        raise ServiceError(str(exc)) from exc
    except ValueError as exc:
        raise ServiceError(f"[{chosen}] 전략 파라미터 오류 — {exc}") from exc

    strategy = loaded.strategy
    await db.init_db()

    # 기준 시각은 종료일의 끝이되 미래로는 가지 않는다. `ctx.now`가 미래면
    # "지금까지 마감된 봉"의 뜻이 흐려진다 (규칙 1).
    end_of_day = datetime.combine(end, time(23, 59, 59), tzinfo=UTC)
    now = min(end_of_day, datetime.now(UTC))

    ctx = RunContext.create(
        settings=spec.settings,
        mode=ExecutionMode.BACKTEST,  # signals·알림을 구조적으로 막는다 (4.2)
        now=now,
        pipeline_id=spec.pipeline_id,
    )
    ctx.ohlcv = ohlcv_source(ctx.providers, spec)
    ctx.universe = universe_source(ctx.providers, ctx.now)

    try:
        ref = await resolve_instrument(instrument, ctx, spec, warn=warn, progress=progress)
        # 워밍업 + 리플레이 구간. 달력일이 거래일보다 많으므로 넉넉한 쪽으로 잡힌다.
        bars = strategy.startup_candles + (end - start).days + 10
        progress(f"{ref.key} · {strategy.timeframe} · 최대 {bars}봉을 읽습니다…")
        loaded_bars = await ctx.ohlcv.load(ref, strategy.timeframe, end=now, limit=bars)

        if loaded_bars.df.empty:
            raise ServiceError(
                f"{ref.key}의 봉을 하나도 받지 못했습니다. "
                f"`marketscan ingest --commit`으로 먼저 봉을 쌓거나 종목을 확인하세요.",
                kind="data",
            )
        return replay(
            frame=loaded_bars.df,
            instrument=ref,
            timeframe=strategy.timeframe,
            strategy=strategy,
            strategy_sha256=loaded.sha256,
            params=params,
            ctx=ctx,
            calendar=ctx.calendar_for(ref),
            start=start,
            end=end,
            adjusted=loaded_bars.adjusted,
        )
    finally:
        await ctx.providers.close()
        await db.dispose()


def write_backtest(result: ReplayResult, spec: PipelineSpec, warn: Notify = _silent) -> Path | None:
    try:
        return write_backtest_report(
            result,
            backtest_report_path(result),
            user_timezone=spec.settings.user_timezone,
        )
    except OSError as exc:
        warn(f"리포트를 쓰지 못했습니다 ({exc}). 결과 자체는 유효합니다.")
        return None


def backtest_strategy(
    spec: PipelineSpec, override: str | None, warn: Notify = _silent
) -> tuple[str, dict[str, Any]]:
    """어떤 전략을 어떤 파라미터로 돌릴 것인가.

    기본은 **설정 파일의 전략과 그 파라미터**다. 파이프라인에 적어 둔 값으로
    돌아야 백테스트가 "지금 돌고 있는 것"을 재현한다 — 전략 기본값으로 돌리면
    실제로 도는 설정과 다른 것을 검증하게 된다.
    """
    nodes = [n for n in spec.nodes if n.type == "strategyRunner"]
    if override:
        for node in nodes:
            if node.params.get("strategy_id") == override:
                return override, dict(node.params.get("params") or {})
        warn(
            f"{override}는 이 설정 파일에 없어 전략의 기본 파라미터로 돌립니다 "
            f"(설정의 전략: {', '.join(pipeline_file.strategy_ids(spec)) or '(없음)'})."
        )
        return override, {}

    if not nodes:
        raise ServiceError(
            "설정 파일에 strategyRunner 노드가 없습니다. 전략을 직접 지정하세요."
        )
    first = nodes[0]
    if len(nodes) > 1:
        warn(
            f"전략이 여럿이라 첫 번째({first.params.get('strategy_id')})로 돌립니다. "
            f"전략을 직접 고를 수 있습니다."
        )
    return str(first.params.get("strategy_id") or ""), dict(first.params.get("params") or {})


async def resolve_instrument(
    raw: str,
    ctx: RunContext,
    spec: PipelineSpec,
    *,
    warn: Notify = _silent,
    progress: Notify = _silent,
) -> InstrumentRef:
    """`venue:symbol`이면 그대로, 아니면 이름·심볼로 찾는다.

    찾는 순서는 싼 것부터다: 심볼 마스터(DB) → 소스에 직접 조회.

    ★ **소스 조회까지 가는 이유** — 마스터에는 `top_by_turnover`를 쓰는 venue가
    없다. 거래대금은 캐시하면 그날의 유니버스가 바뀌므로 통째로 건너뛰기 때문이고
    (`instruments.py`), 그 결과 국내·코인은 마스터에 한 줄도 없다. 여기서 멈추면
    `backtest 삼성전자`가 **"그런 종목이 없다"로 끝난다** — 있는데도.
    """
    text = raw.strip()
    if ":" in text:
        try:
            return InstrumentRef.parse(text)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    found: list[InstrumentRef] = []
    if database_exists():
        async with db.session_scope() as session:
            found = await instruments.find(session, text)

    if not found:
        found = await _search_venues(text, ctx, spec, warn=warn, progress=progress)

    if not found:
        raise ServiceError(
            f"종목을 찾지 못했습니다: {text!r}. "
            f"'venue:symbol' 형식으로 적으면 목록 조회 없이 바로 씁니다 (예: krx:005930)."
        )
    if len(found) > 1 and not _exact_match(found[0], text):
        candidates = ", ".join(f"{i.key}({i.display_name})" for i in found[:8])
        raise ServiceError(
            f"{text!r}에 맞는 종목이 여럿입니다: {candidates}. 하나를 골라 적으세요."
        )
    return found[0]


async def _search_venues(
    text: str, ctx: RunContext, spec: PipelineSpec, *, warn: Notify, progress: Notify
) -> list[InstrumentRef]:
    """설정 파일이 훑는 venue의 종목 목록에서 이름·심볼을 찾는다.

    **훑는 venue만 본다.** 안 보는 시장까지 뒤지면 이름 하나 찾자고 네트워크
    호출이 셋으로 늘고, 사용자가 관심 없는 시장의 동명이의 종목이 후보로 끼어든다.
    """
    venues = [
        str(q["venue"])
        for q in pipeline_file.universe_summary(spec)["dynamic"]  # type: ignore[union-attr]
        if q.get("venue")
    ]
    matches: list[InstrumentRef] = []
    for venue in dict.fromkeys(venues):
        progress(f"{venue}의 종목 목록에서 {text!r}를 찾는 중…")
        try:
            result = await ctx.universe.list_instruments(venue)
        except Exception as exc:  # noqa: BLE001 - 한 시장이 막혀도 나머지는 찾는다
            warn(f"{venue} 목록을 받지 못했습니다 ({exc}).")
            continue
        matches.extend(
            entry.instrument
            for entry in result.entries
            if _exact_match(entry.instrument, text)
            or text.lower() in entry.instrument.display_name.lower()
        )
    matches.sort(key=lambda ref: (not _exact_match(ref, text), ref.key))
    return matches


def _exact_match(ref: InstrumentRef, text: str) -> bool:
    return ref.symbol.lower() == text.lower() or ref.display_name == text


# ========================================================================= ingest
@dataclass
class IngestOutcome:
    plan: Any
    report: Any | None = None
    coverage: list[dict[str, Any]] = field(default_factory=list)
    committed: bool = False


async def execute_ingest(
    spec: PipelineSpec,
    *,
    venue: str | None = None,
    lookback: int | None = None,
    include_delisted: bool = False,
    force: bool = False,
    now: datetime | None = None,
    commit: bool = False,
) -> IngestOutcome:
    """수집 계획을 세우고, `commit`이면 실제로 받아 캐시에 쌓는다 (3.9)."""
    ctx = RunContext.create(
        settings=spec.settings,
        mode=ExecutionMode.NOTIFY,
        now=now,
        pipeline_id=spec.pipeline_id,
        commit=commit,
    )
    try:
        plan = await worker.plan_targets(
            spec, ctx, lookback=lookback, include_delisted=include_delisted
        )
        plan = plan.filtered(venue)

        if not commit:
            coverage: list[dict[str, Any]]
            if sqlite_path(db.database_url()) is not None and database_exists():
                coverage = await worker.coverage_of(plan, ctx, db.get_sessionmaker())
            else:
                coverage = [
                    {**t.to_dict(), "bars": 0, "first": None, "last": None} for t in plan.targets
                ]
            return IngestOutcome(plan=plan, coverage=coverage)

        await db.init_db()
        report = await worker.ingest(plan, ctx, db.get_sessionmaker(), force=force)
        return IngestOutcome(plan=plan, report=report, committed=True)
    finally:
        await ctx.providers.close()
        await db.dispose()


# ========================================================================== 동시성
#: ★ 실행은 **한 번에 하나만.** 두 `run --commit`이 겹치면 같은 봉을 두 번 소비하고
#: (3.5), 그 사이에 낀 신호가 stale로 걸러져 영영 사라진다. 터미널에서는 사람이
#: 한 번에 하나씩 치니 문제가 없었는데, **화면에 버튼이 생기면 연타가 기본**이다.
_lock = asyncio.Lock()


def run_lock() -> asyncio.Lock:
    return _lock
