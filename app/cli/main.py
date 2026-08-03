"""marketscan CLI (ARCHITECTURE.md 12장).

상주 프로세스도 웹 서버도 없다. 무언가가 하루 몇 번 `marketscan run --commit`을
부르고, 사람과 LLM은 같은 CLI로 그 결과에 질문한다.

⚠️ **그 "무언가"가 무엇인지는 아직 정하지 않았다** — OS 스케줄러에 맡길지,
스케줄과 알림을 함께 갖는 `serve` 명령을 둘지는 미결정이다 (ARCHITECTURE.md 11장).
어느 쪽이든 이 CLI의 표면은 바뀌지 않으므로 지금 정하지 않는다.

**읽기 전용이 기본이다.** `explain` · `signals` · `stats` · `describe` ·
`strategy check`는 부작용이 없고, `run`은 `--commit` 없이는 알림도 기록도 하지
않으며 봉도 소비하지 않는다 (규칙 11).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer

import app.nodes  # noqa: F401  — @register 실행 (노드 레지스트리 채우기)
from app.cli import pipeline_file
from app.cli.output import ExitCode, Out, table
from app.cli.templates import STRATEGY_TEMPLATE
from app.core.config import get_settings
from app.engine.context import RunContext
from app.engine.graph import PipelineValidationError, validate
from app.engine.runner import NodeStatus, RunResult, RunStatus, execute
from app.engine.signals import CollectingSink
from app.engine.state import InMemoryBarState
from app.ingest import worker
from app.market.timeframe import JUDGEMENT
from app.nodes.registry import catalog
from app.providers.ohlcv_source import CachedSource, DirectSource
from app.report.run_report import ReportInput, report_path, write_run_report
from app.schemas.pipeline import ExecutionMode, PipelineSpec
from app.storage import db, history
from app.storage.bar_state import SqlBarState, sqlite_path
from app.storage.history import SqlSignalSink
from app.strategies.check import check_file
from app.strategies.registry import (
    StrategyError,
    discover,
    load_strategy,
    strategies_dir,
)

UTC = ZoneInfo("UTC")

cli = typer.Typer(
    name="marketscan",
    help="멀티마켓 횡단면 스크리너 · 신호 알림 CLI",
    no_args_is_help=True,
    add_completion=False,
)
strategy_app = typer.Typer(help="전략 파일 관리", no_args_is_help=True)
signals_app = typer.Typer(help="신호 이력 조회", no_args_is_help=True)
cli.add_typer(strategy_app, name="strategy")
cli.add_typer(signals_app, name="signals")

JsonOpt = Annotated[bool, typer.Option("--json", help="JSON으로 출력합니다 (진행 로그는 stderr).")]
LimitOpt = Annotated[int, typer.Option("--limit", min=1, max=1000, help="최대 출력 건수")]


# =============================================================================== run
@cli.command()
def run(
    pipeline: Annotated[
        Path | None, typer.Option("--pipeline", "-p", help="파이프라인 정의 JSON 경로")
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", help="crypto | krx | us — 해당 시장만 실행")
    ] = None,
    mode: Annotated[
        ExecutionMode | None, typer.Option("--mode", help="실행 모드 (기본: 파이프라인 설정)")
    ] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="기준 시각 ISO8601 UTC. 재현 실행에 씁니다.")
    ] = None,
    commit: Annotated[
        bool,
        typer.Option(
            "--commit",
            help="부작용을 허용합니다 — signals 기록·봉 소비. 자동 실행에만 붙이세요.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="기본값입니다. --commit과 함께 쓸 수 없습니다.")
    ] = False,
    report: Annotated[
        bool, typer.Option("--report/--no-report", help="정적 HTML 리포트를 reports/에 씁니다")
    ] = True,
    as_json: JsonOpt = False,
    limit: LimitOpt = 20,
) -> None:
    """파이프라인을 실행합니다. **기본은 dry-run입니다.**

    산출물은 **stdout과 HTML 리포트뿐**입니다. 텔레그램 같은 외부 전송은 상주
    실행(`serve`)의 몫으로 미뤄져 있습니다 — 손으로 돌릴 때마다 채널로 메시지가
    나가면 알림 자체를 신뢰하지 않게 되기 때문입니다.
    """
    out = Out(as_json)
    if commit and dry_run:
        out.fail(ExitCode.VALIDATION, "--commit과 --dry-run은 함께 쓸 수 없습니다.")

    spec = _load_spec(out, pipeline)
    dropped: list[str] = []
    if market:
        try:
            spec, dropped = pipeline_file.filter_by_market(spec, market)
        except pipeline_file.PipelineFileError as exc:
            out.fail(ExitCode.VALIDATION, str(exc))
        if pipeline_file.has_empty_universe(spec):
            out.progress(f"'{market}' 시장에 해당하는 종목이 없어 실행하지 않습니다.")
            out.emit({"ok": True, "status": "skipped", "market": market, "signals": []})
            raise typer.Exit(int(ExitCode.OK))

    result, sink, ctx = asyncio.run(_execute(out, spec, mode, now, commit))
    _report_run(out, result, sink, ctx, spec, dropped, limit, report)


async def _execute(
    out: Out,
    spec: PipelineSpec,
    mode: ExecutionMode | None,
    now_raw: str | None,
    commit: bool,
) -> tuple[RunResult, Any, RunContext]:
    resolved_mode = mode or spec.settings.default_mode
    now = _parse_now(out, now_raw)

    sink: Any = CollectingSink()
    if commit and resolved_mode is not ExecutionMode.BACKTEST:
        await db.init_db()
        sink = SqlSignalSink(db.get_sessionmaker())

    ctx = RunContext.create(
        settings=spec.settings,
        mode=resolved_mode,
        now=now,
        pipeline_id=spec.pipeline_id,
        bar_state=_bar_state(out, spec.pipeline_id, commit),
        signals=sink,
        commit=commit,
    )
    # 레지스트리는 RunContext가 만든 것을 그대로 쓴다 — 소스 구성의 단일 출처가
    # 한 곳이어야 테스트가 네트워크를 막을 수 있다 (tests/conftest.py).
    ctx.ohlcv = _ohlcv_source(ctx.providers, spec, commit)

    if commit:
        async with db.session_scope() as session:
            await history.start_run(
                session, run_id=ctx.run_id, spec=spec, mode=str(resolved_mode), as_of=ctx.now
            )
            await _snapshot_strategies(session, spec)

    try:
        result = await execute(spec, ctx)
    except PipelineValidationError as exc:
        out.fail(
            ExitCode.VALIDATION,
            str(exc),
            {"issues": exc.result.to_dict()["issues"]},
        )
        raise  # pragma: no cover - fail이 이미 Exit를 던진다
    else:
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
    return result, sink, ctx


def _ohlcv_source(providers: Any, spec: PipelineSpec, commit: bool) -> Any:
    """봉을 어디서 얻을지 고른다 (3.9).

    **쓰기는 `--commit`에서만, 읽기는 언제나.** dry-run이 캐시를 채우면 "읽기 전용
    실행은 DB 파일조차 만들지 않는다"(규칙 11 / 12.1)가 깨지고, 반대로 dry-run이
    캐시를 읽지 않으면 `run`과 `run --commit`이 서로 다른 데이터를 보게 되어
    dry-run이 실제 실행을 예측하지 못한다 — `_bar_state`와 같은 판단이다.
    """
    path = sqlite_path(db.database_url())
    if path is None or (not commit and not path.exists()):
        return DirectSource(providers, default_adjusted=spec.settings.adjusted)
    return CachedSource(
        providers,
        db.get_sessionmaker(),
        writable=commit,
        default_adjusted=spec.settings.adjusted,
    )


def _bar_state(out: Out, pipeline_id: str, commit: bool) -> Any:
    """실행에 맞는 봉 상태 저장소를 고른다 (3.5 / `app/engine/state.py` 표 참조).

    dry-run에서도 **읽기는 한다.** 읽지 않으면 `run`과 `run --commit`이 서로 다른
    종목 집합을 보게 되어, dry-run이 실제 실행을 예측하지 못한다.
    """
    path = sqlite_path(db.database_url())
    if path is None:
        out.warn(
            "SQLite가 아니어서 Fresh Bar Gate가 프로세스 메모리에만 남습니다 — "
            "같은 봉이 반복 판정될 수 있습니다 (중복 신호는 dedup_key가 막습니다)."
        )
        return InMemoryBarState()
    if not commit and not path.exists():
        # 읽기 전용 실행이 DB 파일을 만들면 규칙 11이 깨진다 (12.1).
        return InMemoryBarState()
    return SqlBarState(path, pipeline_id, readonly=not commit)


async def _snapshot_strategies(session: Any, spec: PipelineSpec) -> None:
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


def _write_report(
    out: Out,
    result: RunResult,
    signals: list[dict[str, Any]],
    ctx: RunContext,
    spec: PipelineSpec,
) -> Path | None:
    """정적 HTML 리포트를 남긴다.

    파일 쓰기지만 `--commit` 뒤에 두지 않았다. `reports/`는 재생성 가능하고
    gitignore 대상이며, 무엇도 되돌릴 수 없게 만들지 않기 때문이다 — 위험한
    부작용은 알림 발송·`signals` 기록·봉 소비 셋이고 그건 그대로 `--commit`이 막는다.
    실패해도 실행 자체를 실패로 만들지 않는다. 리포트는 산출물이지 판단이 아니다.
    """
    try:
        path = report_path(result.run_id, committed=ctx.commit)
        return write_run_report(
            ReportInput(
                result=result,
                signals=signals,
                committed=ctx.commit,
                pipeline_name=spec.name,
            ),
            path,
        )
    except OSError as exc:
        out.warn(f"리포트를 쓰지 못했습니다 ({exc}). 실행 결과 자체는 유효합니다.")
        return None


def _report_run(
    out: Out,
    result: RunResult,
    sink: Any,
    ctx: RunContext,
    spec: PipelineSpec,
    dropped: list[str],
    limit: int,
    report: bool,
) -> None:
    drafts = getattr(sink, "drafts", [])
    # 리포트에는 --limit과 무관하게 전부 싣는다. stdout은 좁게, 파일은 넓게가
    # 맞는 배분이다 — 좁혀야 하는 쪽은 사람과 LLM이 읽는 화면이다 (12.4).
    all_signals = [d.to_dict() for d in drafts]
    signals = all_signals[:limit]
    failed = [n for n in result.nodes if n.status is NodeStatus.ERROR]
    written = _write_report(out, result, all_signals, ctx, spec) if report else None

    payload = {
        "ok": result.status is not RunStatus.FAILED and not failed,
        "run_id": result.run_id,
        "pipeline_id": result.pipeline_id,
        "mode": result.mode,
        "now": result.now,
        "status": str(result.status),
        "committed": ctx.commit,
        "signals": signals,
        "signal_count": getattr(sink, "written", len(drafts)),
        "truncated": max(0, len(drafts) - limit),
        "report": str(written) if written else None,
        "alerts_sent": False,  # 단일 실행은 외부로 아무것도 내보내지 않는다
        "nodes": [
            {
                "node_id": n.node_id,
                "type": n.type,
                "status": str(n.status),
                # 노드가 몇 건을 내보냈는가. "왜 신호가 0건인가"에 답하려면 어느
                # 노드에서 0이 됐는지가 보여야 한다 — 상태만으로는 구분되지
                # 않는다(0종목을 수집한 노드도 success다).
                "items": _output_count(n),
                "error": n.error,
            }
            for n in result.nodes
        ],
    }

    human = [
        f"실행 {result.run_id} · {result.pipeline_id} · mode={result.mode} · {result.status}",
        f"기준 시각 {result.now}",
        "",
    ]
    rows = [
        [s["instrument"], s["timeframe"], s["as_of"], s["features"].get("rank"), s["strategy_id"]]
        for s in signals
    ]
    signal_table = table(rows, ["종목", "봉", "as_of", "순위", "전략"])
    human.extend(signal_table or ["신호 0건 — 정상입니다 (빈 결과와 실패는 다릅니다)."])

    if written:
        human += ["", f"리포트 {written}"]
    if not ctx.commit:
        human += ["※ dry-run입니다. signals 미기록 · 봉 미소비 (--commit으로 실행)."]
    if dropped:
        out.warn(f"--market 필터로 {len(dropped)}개 종목을 제외했습니다.")
    for node in failed:
        out.warn(f"[{node.node_id}] {node.error}")

    out.emit(payload, human)
    if result.status is RunStatus.FAILED or failed:
        # 4.1이 "빈 Bundle도 정상"이라고 정했으므로 신호 0건은 여기 오지 않는다.
        # 여기 오는 것은 소스나 노드가 실제로 터진 경우뿐이다 (12.3).
        raise typer.Exit(int(ExitCode.DATA))


# ============================================================================ ingest
@cli.command()
def ingest(
    venue: Annotated[
        str | None, typer.Option("--venue", help="수집할 venue (예: upbit, krx)")
    ] = None,
    pipeline: Annotated[Path | None, typer.Option("--pipeline", "-p")] = None,
    lookback: Annotated[
        int | None,
        typer.Option("--lookback", min=2, max=20000, help="봉 개수. 기본은 파이프라인 값"),
    ] = None,
    include_delisted: Annotated[
        bool,
        typer.Option(
            "--include-delisted",
            help="상장폐지 종목도 폐지 시점 기준으로 모읍니다 (서바이버십 방지, krx).",
        ),
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="이미 이 봉까지 성공한 대상도 다시 받습니다.")
    ] = False,
    now: Annotated[str | None, typer.Option("--now", help="기준 시각 ISO8601 UTC")] = None,
    commit: Annotated[bool, typer.Option("--commit", help="캐시에 실제로 씁니다.")] = False,
    as_json: JsonOpt = False,
    limit: LimitOpt = 20,
) -> None:
    """일봉을 수집해 `ohlcv_cache`에 쌓습니다 (ARCHITECTURE.md 3.9).

    **기본은 계획만 보여 줍니다.** `--commit`을 붙여야 소스를 호출하고 캐시에 씁니다.

    캐시는 성능 최적화가 아니라 **영구 보관하는 데이터 자산**입니다 — 무료 소스가
    막혀도 이미 쌓인 이력으로 파이프라인과 백테스트가 계속 돕니다.
    """
    out = Out(as_json)
    spec = _load_spec(out, pipeline)
    asyncio.run(_ingest(out, spec, venue, lookback, include_delisted, force, now, commit, limit))


async def _ingest(  # noqa: PLR0913 - CLI 플래그를 그대로 옮긴 것뿐이다
    out: Out,
    spec: PipelineSpec,
    venue: str | None,
    lookback: int | None,
    include_delisted: bool,
    force: bool,
    now_raw: str | None,
    commit: bool,
    limit: int,
) -> None:
    ctx = RunContext.create(
        settings=spec.settings,
        mode=ExecutionMode.NOTIFY,
        now=_parse_now(out, now_raw),
        pipeline_id=spec.pipeline_id,
        commit=commit,
    )

    try:
        plan = await worker.plan_targets(
            spec, ctx, lookback=lookback, include_delisted=include_delisted
        )
        plan = plan.filtered(venue)

        if not commit:
            await _ingest_plan_only(out, plan, ctx, venue, limit)
            return

        await db.init_db()
        report = await worker.ingest(plan, ctx, db.get_sessionmaker(), force=force)
        _report_ingest(out, plan, report, venue, limit)
    finally:
        await ctx.providers.close()
        await db.dispose()

    if report.failures and report.fetched == 0:
        # 전부 실패한 것은 소스가 죽었다는 뜻이다. 자동 실행이 이 차이를 알아야 한다.
        raise typer.Exit(int(ExitCode.DATA))


async def _ingest_plan_only(
    out: Out, plan: Any, ctx: RunContext, venue: str | None, limit: int
) -> None:
    """dry-run — 무엇을 모을지와 지금 캐시에 뭐가 있는지만 보여 준다.

    커버리지를 함께 내는 이유는 "왜 아직 신호가 안 나오는가"의 답이 대개
    **캐시가 얕아서**이기 때문이다. 대상 목록만으로는 그것이 보이지 않는다.
    """
    rows: list[dict[str, Any]] = []
    if sqlite_path(db.database_url()) is not None and _database_exists():
        rows = await worker.coverage_of(plan, ctx, db.get_sessionmaker())
    else:
        rows = [{**t.to_dict(), "bars": 0, "first": None, "last": None} for t in plan.targets]

    for note in plan.notes:
        out.warn(note)
    out.warn("dry-run입니다. 소스를 호출하지도 캐시에 쓰지도 않았습니다 (--commit으로 실행).")

    empty = sum(1 for r in rows if not r["bars"])
    out.emit(
        {
            "ok": True,
            "committed": False,
            "venue": venue,
            "planned": len(plan.targets),
            "uncached": empty,
            "targets": rows[:limit],
            "truncated": max(0, len(rows) - limit),
            "notes": plan.notes,
        },
        [
            f"수집 대상 {len(plan.targets)}종목 · 캐시 없음 {empty}종목",
            "",
            *table(
                [
                    [r["instrument"], r["timeframe"], r["lookback"], r["bars"], r["last"]]
                    for r in rows[:limit]
                ],
                ["종목", "봉", "요청", "캐시", "마지막 봉"],
            ),
        ],
    )


def _report_ingest(out: Out, plan: Any, report: Any, venue: str | None, limit: int) -> None:
    for note in plan.notes:
        out.warn(note)
    for line in report.conflicts[:limit]:
        # 3.8 정합성 검증. 같은 봉을 두 소스가 다르게 준 것은 조용히 넘길 일이 아니다.
        out.warn(line)
    for instrument, error in report.failures[:limit]:
        out.warn(f"{instrument}: {error}")
    if report.empty:
        out.warn(
            f"봉을 하나도 받지 못한 종목 {len(report.empty)}건: "
            f"{', '.join(report.empty[:10])}"
        )

    out.emit(
        {"ok": True, "committed": True, "venue": venue, **report.to_dict()},
        [
            f"수집 {report.fetched}/{report.planned}종목 "
            f"(이미 최신 {report.skipped_fresh} · 실패 {len(report.failures)})",
            f"캐시 신규 {report.inserted}봉 · 갱신 {report.updated}봉",
        ],
    )


# ========================================================================== describe
@cli.command()
def describe(as_json: JsonOpt = False) -> None:
    """전략 목록·유니버스·노드·마지막 실행을 한 번에 보여 줍니다 (에이전트의 방향 잡기용)."""
    out = Out(as_json)
    settings = get_settings()

    sources = discover()
    strategies: list[dict[str, Any]] = []
    for source in sources:
        entry: dict[str, Any] = {**source.to_dict(), "loadable": True}
        try:
            entry.update(load_strategy(source.id).strategy.descriptor())
        except StrategyError as exc:
            entry.update({"loadable": False, "error": str(exc)})
        strategies.append(entry)

    spec: PipelineSpec | None = None
    pipeline_info: dict[str, Any] = {"path": str(pipeline_file.default_path()), "loaded": False}
    try:
        spec = pipeline_file.load()
    except pipeline_file.PipelineFileError as exc:
        pipeline_info["error"] = str(exc)
    else:
        issues = validate(spec, spec.settings.default_mode)
        universe = pipeline_file.universe_summary(spec)
        pipeline_info = {
            "path": str(pipeline_file.default_path()),
            "loaded": True,
            "pipeline_id": spec.pipeline_id,
            "name": spec.name,
            "nodes": len(spec.nodes),
            "universe": universe,
            "universe_size": universe["fixed_size"],
            "strategies": pipeline_file.strategy_ids(spec),
            "valid": issues.ok,
            "issues": [i.to_dict() for i in issues.issues],
        }

    last = asyncio.run(_last_run())

    payload = {
        "ok": True,
        "strategies_dir": str(strategies_dir()),
        "strategies": strategies,
        "pipeline": pipeline_info,
        "nodes": [{"type": n["type"], "category": n["category"]} for n in catalog()],
        "timeframes": sorted(JUDGEMENT),
        "database": settings.database_url,
        # 커버리지는 `ingest`가 대상별로 낸다 (3.9). 여기서 다시 집계하면 읽기 전용
        # 명령이 캐시 테이블을 훑게 되고, DB가 없을 때의 분기가 하나 더 생긴다.
        "cache_coverage": None,
        "last_run": last,
    }
    human = [
        f"전략 디렉터리 {strategies_dir()}",
        *(
            table(
                [
                    [s["id"], s.get("timeframe", "-"), s["sha256"][:12], s["loadable"]]
                    for s in strategies
                ],
                ["전략", "봉", "sha256", "로드"],
            )
            or ["  (전략 없음 — `marketscan strategy new <이름>`으로 만드세요)"]
        ),
        "",
        f"파이프라인 {pipeline_info.get('pipeline_id', '-')} "
        f"· 노드 {pipeline_info.get('nodes', 0)}개 "
        f"· 유니버스 {pipeline_file.describe_universe(pipeline_info['universe'])
                     if pipeline_info.get('loaded') else '-'} "
        f"· 검증 {'통과' if pipeline_info.get('valid') else '실패'}",
        f"마지막 실행 {last['run_id'] if last else '(없음)'}",
        "캐시 커버리지 — `marketscan ingest`가 대상별로 보여 줍니다",
    ]
    out.emit(payload, human)


async def _last_run() -> dict[str, Any] | None:
    if not _database_exists():
        return None
    async with db.session_scope() as session:
        record = await history.last_run(session)
    await db.dispose()
    return record.to_dict() if record else None


# =========================================================================== signals
@signals_app.command("list")
def signals_list(
    strategy: Annotated[str | None, typer.Option("--strategy", help="전략 id로 필터")] = None,
    venue: Annotated[str | None, typer.Option("--venue", help="venue로 필터")] = None,
    acted: Annotated[
        bool | None, typer.Option("--acted/--ignored", help="실행/무시한 신호만")
    ] = None,
    limit: LimitOpt = 20,
    as_json: JsonOpt = False,
) -> None:
    """기록된 신호를 최신순으로 봅니다. 부작용 없음."""
    out = Out(as_json)
    rows = asyncio.run(_query_signals(out, strategy, venue, acted, limit))
    payload = {"ok": True, "count": len(rows), "signals": rows}
    human = table(
        [[r["id"], r["instrument"], r["as_of"], r["strategy_id"], r["acted"]] for r in rows],
        ["id", "종목", "as_of", "전략", "실행"],
    ) or ["신호가 없습니다. `marketscan run --commit`으로 기록됩니다."]
    out.emit(payload, human)


@signals_app.command("ack")
def signals_ack(
    signal_id: Annotated[int, typer.Argument(help="`signals list`가 보여 주는 id")],
    acted: Annotated[
        bool,
        typer.Option("--acted/--ignored", help="이 신호대로 움직였는가"),
    ] = True,
    as_json: JsonOpt = False,
) -> None:
    """신호에 응답합니다 — 실행했는가, 무시했는가 (4.8 오버라이드 추적).

    **`run`과 달리 `--commit`이 필요 없습니다.** 이 명령의 존재 목적 자체가 기록이고,
    되돌릴 수 있기 때문입니다 (반대로 다시 부르면 됩니다). 되돌릴 수 없는 부작용은
    봉 소비뿐이고 그건 여전히 `--commit`만이 엽니다 (규칙 11).

    무시한 신호의 사후 성과는 Forward Return Evaluator(Phase 3) 이후에 나옵니다.
    """
    out = Out(as_json)
    if not _database_exists():
        out.fail(
            ExitCode.VALIDATION,
            "기록된 신호가 없습니다. `marketscan run --commit`으로 먼저 신호를 남기세요.",
        )
    record = asyncio.run(_ack(signal_id, acted))
    if record is None:
        out.fail(
            ExitCode.VALIDATION,
            f"신호 {signal_id}번을 찾을 수 없습니다. `marketscan signals list`로 id를 확인하세요.",
        )
        return

    out.emit(
        {"ok": True, **record},
        [
            f"{record['instrument']} · {record['as_of']} → "
            f"{'실행함' if acted else '무시함'}으로 기록했습니다.",
            "※ 잘못 눌렀으면 반대 플래그로 다시 부르면 됩니다.",
        ],
    )


async def _ack(signal_id: int, acted: bool) -> dict[str, Any] | None:
    async with db.session_scope() as session:
        row = await history.set_acted(session, signal_id, acted)
        record = _signal_dict(row) if row is not None else None
    await db.dispose()
    return record


async def _query_signals(
    out: Out, strategy: str | None, venue: str | None, acted: bool | None, limit: int
) -> list[dict[str, Any]]:
    if not _database_exists():
        return []
    async with db.session_scope() as session:
        rows = await history.list_signals(
            session, limit=limit, strategy_id=strategy, venue=venue, acted=acted
        )
        out_rows = [_signal_dict(r) for r in rows]
    await db.dispose()
    return out_rows


# =========================================================================== explain
@cli.command()
def explain(
    signal_id: Annotated[int, typer.Argument(help="`signals list`가 보여 주는 id")],
    as_json: JsonOpt = False,
) -> None:
    """이 신호가 왜 떴는지 한 번에 보여 줍니다. 부작용 없음.

    사람이 알림을 보고 던지는 질문은 언제나 하나다 — "이게 왜 떴어?"
    5개 테이블 조인을 이 명령 하나로 접는다 (12.5).
    """
    out = Out(as_json)
    payload = asyncio.run(_explain(out, signal_id))
    if payload is None:
        out.fail(
            ExitCode.VALIDATION,
            f"신호 {signal_id}번을 찾을 수 없습니다. `marketscan signals list`로 id를 확인하세요.",
        )
        return

    strategy = payload["strategy"]
    human = [
        f"{payload['instrument']} · {payload['as_of']} ({payload['timeframe']})",
        f"전략  {strategy['id']} @ {(strategy['sha256'] or '')[:12]}",
        f"순위  {strategy['features'].get('rank')} / {strategy['features'].get('universe_size')}"
        f"{_rank_pool(strategy['features'])}"
        f"  (상위 {strategy['features'].get('percentile')}%)"
        f"{_excluded_note(strategy['features'])}",
        f"데이터 {_data_origin(payload['data'])} · adjusted={payload['data']['adjusted']}"
        f" · fallback_from={payload['data']['fallback_from']}",
        f"실행  {payload['run']['run_id']} · {payload['run']['status']}",
        f"판정  acted={payload['acted']}",
    ]
    out.emit(payload, human)


def _rank_pool(features: dict[str, Any]) -> str:
    """어느 시장 안에서 매긴 순위인가 (규칙 17).

    이게 없으면 "7 / 200"의 200이 무엇의 200인지 알 수 없다. 혼합 유니버스에서는
    시장마다 분모가 다르므로 반드시 함께 보여야 한다.
    """
    pool = features.get("rank_pool")
    return f" ({pool})" if pool else ""


def _excluded_note(features: dict[str, Any]) -> str:
    """"무엇과 비교해서 이 순위인가"를 밝힌다.

    분모(`universe_size`)는 점수가 나온 종목 수라, 훑은 종목 수와 다르면 그 차이를
    적어 준다. 안 적으면 "훑은 30개 중 2등"으로 읽힌다.
    """
    ranked = features.get("universe_size")
    scanned = features.get("universe_scanned")
    if not isinstance(ranked, int) or not isinstance(scanned, int) or scanned <= ranked:
        return ""
    return f"  — {scanned}종목을 훑어 {scanned - ranked}종목은 봉 부족으로 제외"


async def _explain(out: Out, signal_id: int) -> dict[str, Any] | None:
    if not _database_exists():
        return None
    async with db.session_scope() as session:
        row = await history.get_signal(session, signal_id)
        if row is None:
            await db.dispose()
            return None
        run = await history.get_run(session, row.run_id)
        node_runs = await history.get_node_runs(session, row.run_id)
    await db.dispose()

    meta = row.meta or {}
    return {
        "ok": True,
        "signal_id": row.id,
        "instrument": row.instrument,
        "timeframe": row.timeframe,
        "as_of": row.as_of.isoformat(),
        "kind": row.kind,
        "acted": row.acted,
        "strategy": {
            "id": row.strategy_id,
            "sha256": row.strategy_sha256,
            "features": row.features or {},
            "tags": row.tags or {},
        },
        # 3.4의 폴백 가시화. 이게 없으면 지표 불연속의 원인을 되짚을 수 없다.
        "data": {
            "source": meta.get("source"),
            "adjusted": meta.get("adjusted"),
            "fallback_from": meta.get("fallback_from", []),
            # 캐시에서 읽었으면 `source`가 'cache'가 되어 원래 출처를 잃는다.
            # 그러면 "어느 소스로 계산된 판단인가"에 답할 수 없다 (4.7).
            "cached_sources": meta.get("cached_sources", []),
        },
        "run": {
            "run_id": row.run_id,
            "mode": run.mode if run else None,
            "status": run.status if run else None,
            "as_of": run.as_of.isoformat() if run else None,
        },
        "nodes": [
            {"node_id": n.node_id, "type": n.type, "status": n.status, "logs": n.logs}
            for n in node_runs
        ],
    }


# ============================================================================= stats
@cli.command()
def stats(
    group_by: Annotated[
        str, typer.Option("--group-by", help="strategy | venue | instrument | timeframe")
    ] = "strategy",
    compare: Annotated[
        str | None, typer.Option("--compare", help="acted — 오버라이드 분석 (4.8)")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """신호 이력 집계. 부작용 없음.

    ⚠️ **여기서 나오는 것은 건수와 분산뿐입니다.** forward return · hit rate · IC는
    Forward Return Evaluator가 사후 수익률을 채운 뒤에야 계산할 수 있고, 그건
    Phase 3입니다. 없는 숫자를 만들어 내지 않습니다 (4.8).
    """
    out = Out(as_json)
    payload = asyncio.run(_stats(out, group_by, compare))
    human = [
        *(
            table(
                [[g["group"], g["signals"], g["latest_as_of"]] for g in payload["groups"]],
                [group_by, "신호", "최근 as_of"],
            )
            or ["신호가 없습니다."]
        ),
    ]
    if payload.get("acted"):
        a = payload["acted"]
        human += [
            "",
            f"오버라이드 — 실행 {a['acted']} / 무시 {a['ignored']} / 미응답 {a['unanswered']}",
            "※ 무시한 신호의 사후 성과는 Forward Return Evaluator(Phase 3) 이후에 나옵니다.",
        ]
    out.emit(payload, human)


async def _stats(out: Out, group_by: str, compare: str | None) -> dict[str, Any]:
    if not _database_exists():
        return {"ok": True, "group_by": group_by, "groups": [], "quality_metrics": None}
    async with db.session_scope() as session:
        try:
            groups = await history.signal_counts(session, group_by)
        except ValueError as exc:
            await db.dispose()
            out.fail(ExitCode.VALIDATION, str(exc))
            raise
        acted = await history.acted_breakdown(session) if compare == "acted" else None
    await db.dispose()
    return {
        "ok": True,
        "group_by": group_by,
        "groups": groups,
        "acted": acted,
        # 신호 품질 지표를 아직 계산할 수 없다는 사실 자체를 스키마에 남긴다.
        "quality_metrics": None,
        "quality_metrics_note": "forward return · hit rate · IC는 Phase 3에서 추가됩니다.",
    }


# ========================================================================== strategy
@strategy_app.command("list")
def strategy_list(as_json: JsonOpt = False) -> None:
    """`strategies/`의 전략과 소스 해시를 보여 줍니다."""
    out = Out(as_json)
    sources = [s.to_dict() for s in discover()]
    out.emit(
        {"ok": True, "strategies_dir": str(strategies_dir()), "strategies": sources},
        table([[s["id"], s["sha256"][:16], s["path"]] for s in sources], ["전략", "sha256", "경로"])
        or [f"{strategies_dir()}에 전략이 없습니다."],
    )


@strategy_app.command("new")
def strategy_new(
    name: Annotated[str, typer.Argument(help="전략 id이자 파일 이름")],
    as_json: JsonOpt = False,
) -> None:
    """`Params`가 포함된 전략 템플릿을 만듭니다."""
    out = Out(as_json)
    directory = strategies_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    if path.exists():
        out.fail(ExitCode.VALIDATION, f"이미 있는 파일입니다: {path}")

    path.write_text(STRATEGY_TEMPLATE.format(strategy_id=name), encoding="utf-8")
    out.progress(f"{path} 를 만들었습니다.")
    out.emit(
        {"ok": True, "strategy_id": name, "path": str(path)},
        [f"다음: compute를 채우고 `marketscan strategy check {name}`을 돌리세요."],
    )


@strategy_app.command("check")
def strategy_check(
    name: Annotated[str, typer.Argument(help="검사할 전략 id")],
    as_json: JsonOpt = False,
) -> None:
    """전략의 인과성을 AST로 정적 검사합니다. 부작용 없음.

    ⚠️ **통과가 인과성을 보장하지는 않습니다.** 사후 방어선은 난수 신호 테스트입니다 (4.8).
    """
    out = Out(as_json)
    path = strategies_dir() / f"{name}.py"
    if not path.is_file():
        available = ", ".join(s.id for s in discover()) or "(없음)"
        out.fail(
            ExitCode.VALIDATION,
            f"전략을 찾을 수 없습니다: {name} ({path}). 사용 가능: {available}",
        )
        return

    result = check_file(path, name)
    payload = result.to_dict()
    human = [
        f"{name} — {'통과' if result.ok else '위반 ' + str(len(result.errors)) + '건'}",
        *(f"  L{v.line} [{v.rule}] {v.detail}" for v in result.violations),
    ]
    if result.ok:
        human.append(
            "※ 정적 검사 통과가 인과성을 보장하지는 않습니다 — "
            "사후 방어선은 난수 신호 테스트입니다."
        )
    out.emit(payload, human)
    if not result.ok:
        raise typer.Exit(int(ExitCode.VALIDATION))


# ============================================================================= 공통
def _output_count(node: Any) -> int | None:
    """노드의 main 출력 건수. 실행되지 않았으면 None."""
    main = (node.outputs or {}).get("main")
    return main.get("count") if isinstance(main, dict) else None


def _data_origin(data: dict[str, Any]) -> str:
    """봉이 어디서 왔는가. 캐시면 **그 구간을 채운 원래 소스까지** 밝힌다.

    `cache`만 보여 주면 "어느 소스로 계산된 판단인가"에 답할 수 없고, 그건
    폴백 가시화(3.4)를 캐시가 도로 가려 버리는 것이다.
    """
    source = data.get("source")
    cached = data.get("cached_sources") or []
    return f"{source}({', '.join(cached)})" if cached else str(source)


def _load_spec(out: Out, pipeline: Path | None) -> PipelineSpec:
    try:
        return pipeline_file.load(pipeline)
    except pipeline_file.PipelineFileError as exc:
        out.fail(ExitCode.VALIDATION, str(exc))
        raise  # pragma: no cover


def _parse_now(out: Out, raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        out.fail(
            ExitCode.VALIDATION,
            f"--now는 ISO8601이어야 합니다 (받은 값: {raw!r}). 예: 2026-08-01T06:30:00Z",
        )
        raise  # pragma: no cover
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _database_exists() -> bool:
    """읽기 전용 명령이 DB 파일을 **만들지 않도록** 먼저 확인한다.

    `explain`·`signals`·`stats`는 부작용이 없어야 하는데, 엔진을 열기만 해도
    SQLite 파일이 생긴다 (12.1).
    """
    url = db.database_url()
    if not url.startswith("sqlite"):
        return True
    path = url.split("///")[-1]
    return path == ":memory:" or Path(path).exists()


def _signal_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "instrument": row.instrument,
        "venue": row.venue,
        "timeframe": row.timeframe,
        "as_of": row.as_of.isoformat(),
        "kind": row.kind,
        "strategy_id": row.strategy_id,
        "strategy_sha256": row.strategy_sha256,
        "features": row.features or {},
        "tags": row.tags or {},
        "acted": row.acted,
    }


def _force_utf8_output() -> None:
    """Windows 콘솔의 기본 코드페이지(한국어 환경은 cp949)는 `—`·이모지를 인코딩하지
    못해 **명령 자체가 UnicodeEncodeError로 죽는다.** 글자가 깨지는 것과 명령이
    죽는 것은 다르므로, UTF-8로 바꾸되 실패하면 대체 문자로 흘려보낸다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """`[project.scripts]`의 진입점."""
    _force_utf8_output()
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
