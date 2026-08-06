"""stockscan CLI (ARCHITECTURE.md 12장).

사람과 LLM이 같은 표면으로 결과에 질문한다. **실행 본체는 스케줄러도 터미널도
`app/service.py`를 지난다** — 갈라지면 규칙 11·13이 우회된다.

**읽기 전용이 기본이다.** `explain` · `signals` · `stats` · `describe` ·
`strategy check`는 부작용이 없고, `run`은 `--commit` 없이는 알림도 기록도 하지
않으며 봉도 소비하지 않는다 (규칙 11).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer

from app import config as app_config
from app import service
from app.alerts import default_channel
from app.backtest import ReplayResult
from app.cli.output import ExitCode, Out, table
from app.cli.templates import STRATEGY_TEMPLATE
from app.config import AppConfig, ConfigError
from app.core.config import get_settings
from app.core.formatting import (
    format_price,
    format_price_change,
    format_time,
    timezone_label,
)
from app.engine.context import ExecutionMode, RunSettings
from app.market.timeframe import JUDGEMENT
from app.pipeline import RunStatus, StageStatus
from app.storage import db, history
from app.strategies.check import check_file
from app.strategies.registry import (
    StrategyError,
    discover,
    load_strategy,
    strategies_dir,
)

UTC = ZoneInfo("UTC")

cli = typer.Typer(
    name="stockscan",
    help="매수 후보를 뽑아 텔레그램으로 보내 주는 프로그램",
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
    config_path: Annotated[
        Path | None, typer.Option("--config", "-c", help="설정 파일 경로")
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", help="krx | us — 해당 시장만 실행")
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

    config = _load_config(out, config_path)
    dropped: list[str] = []
    if market:
        try:
            narrowed = config.for_market(market)
        except ConfigError as exc:
            out.fail(ExitCode.VALIDATION, str(exc))
        dropped = [v for v in config.universe if v not in narrowed.universe]
        config = narrowed
        if not config.universe:
            out.progress(f"'{market}' 시장에 해당하는 venue가 없어 실행하지 않습니다.")
            out.emit({"ok": True, "status": "skipped", "market": market, "signals": []})
            raise typer.Exit(int(ExitCode.OK))

    outcome = asyncio.run(_execute(out, config, mode, now, commit))
    _report_run(out, outcome, config, dropped, limit, report)


async def _execute(
    out: Out,
    config: AppConfig,
    mode: ExecutionMode | None,
    now_raw: str | None,
    commit: bool,
) -> service.RunOutcome:
    """실행 본체는 `app/service.py`에 있다 — 스케줄러도 **같은 함수**를 부른다.

    여기 남은 것은 CLI의 몫뿐이다: 시각 파싱, 실패의 종료 코드, 경고 출력.
    """
    return await service.execute_run(
        config,
        mode=mode,
        now=_parse_now(out, now_raw),
        commit=commit,
        warn=out.warn,
    )


def _report_run(
    out: Out,
    outcome: service.RunOutcome,
    config: AppConfig,
    dropped: list[str],
    limit: int,
    report: bool,
) -> None:
    result = outcome.result
    # 리포트에는 --limit과 무관하게 전부 싣는다. stdout은 좁게, 파일은 넓게가
    # 맞는 배분이다 — 좁혀야 하는 쪽은 사람과 LLM이 읽는 화면이다 (12.4).
    signals = outcome.signals[:limit]
    failed = [n for n in result.nodes if n.status is StageStatus.ERROR]
    written = service.write_report(outcome, config, out.warn) if report else None

    payload = {
        "ok": result.status is not RunStatus.FAILED and not failed,
        "run_id": result.run_id,
        "pipeline_id": result.pipeline_id,
        "mode": result.mode,
        "now": result.now,
        "status": str(result.status),
        "committed": outcome.committed,
        "signals": signals,
        "signal_count": outcome.written,
        "truncated": max(0, len(outcome.signals) - limit),
        "report": str(written) if written else None,
        "alerts_sent": False,  # 단일 실행은 외부로 아무것도 내보내지 않는다
        "nodes": [
            {
                "node_id": n.node_id,
                "type": n.type,
                "status": str(n.status),
                # 이 단계가 몇 건을 내보냈는가. "왜 신호가 0건인가"에 답하려면
                # 어느 단계에서 0이 됐는지가 보여야 한다 — 상태만으로는 구분되지
                # 않는다(0종목을 수집한 단계도 success다).
                "items": _output_count(n),
                "error": n.error,
            }
            for n in result.nodes
        ],
    }

    # 저장은 UTC, 표시만 사용자 타임존 (규칙 5).
    tz = config.timezone
    label = timezone_label(tz)
    human = [
        f"실행 {result.run_id} · {result.pipeline_id} · mode={result.mode} · {result.status}",
        f"기준 시각 {format_time(result.now, tz)} ({label})",
        "",
    ]
    rows = [
        [
            s["instrument"],
            # `005930`만으로는 무슨 회사인지 알 수 없다. 소스가 준 이름을 쓴다.
            s.get("display_name") or "",
            format_price_change(s.get("close"), s.get("change_pct")),
            s["timeframe"],
            format_time(s["as_of"], tz),
            s["features"].get("rank_pool"),
            s["features"].get("rank"),
            s["strategy_id"],
        ]
        for s in signals
    ]
    signal_table = table(
        rows,
        ["종목", "이름", "종가 (등락)", "봉", f"봉 마감 ({label})", "시장", "순위", "전략"],
    )
    human.extend(signal_table or ["신호 0건 — 정상입니다 (빈 결과와 실패는 다릅니다)."])

    if written:
        human += ["", f"리포트 {written}"]
    if not outcome.committed:
        human += ["※ dry-run입니다. signals 미기록 · 봉 미소비 (--commit으로 실행)."]
    if dropped:
        out.warn(f"--market 필터로 {len(dropped)}개 종목을 제외했습니다.")
    for node in failed:
        out.warn(f"[{node.node_id}] {node.error}")

    out.emit(payload, human)
    if result.status is RunStatus.FAILED or failed:
        # 4.1이 "빈 Bundle도 정상"이라고 정했으므로 신호 0건은 여기 오지 않는다.
        # 여기 오는 것은 소스나 단계가 실제로 터진 경우뿐이다 (12.3).
        raise typer.Exit(int(ExitCode.DATA))


# ========================================================================== backtest
@cli.command()
def backtest(
    instrument: Annotated[
        str, typer.Argument(help="종목. 'krx:005930' 또는 심볼·이름 ('005930' · '삼성전자')")
    ],
    start: Annotated[
        str, typer.Option("--start", help="시작일. 20251201 또는 2025-12-01")
    ],
    end: Annotated[
        str | None, typer.Option("--end", help="종료일. 기본은 오늘")
    ] = None,
    strategy: Annotated[
        str | None, typer.Option("--strategy", help="전략 id. 기본은 설정 파일의 전략")
    ] = None,
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    report: Annotated[
        bool, typer.Option("--report/--no-report", help="정적 HTML 리포트를 씁니다")
    ] = True,
    as_json: JsonOpt = False,
    limit: LimitOpt = 20,
) -> None:
    """한 종목을 `--start`부터 하루씩 되감아 전략을 돌립니다.

    그날까지 마감된 봉만 잘라서 전략에 넣으므로 미래를 볼 수 없습니다. 조건을
    만족한 날이 차트에 마커로 찍힙니다.

    ⚠️ **마커는 "조건 충족일"이지 "실제 신호일"이 아닙니다** — 종목 하나만 보면
    횡단면 컷(상위 N개)의 후보가 1개라 항상 통과합니다. 리포트 상단에 같은
    경고가 있습니다.

    부작용은 없습니다 — `signals`를 남기지 않고 봉도 소비하지 않습니다.
    """
    out = Out(as_json)
    config = _load_config(out, config_path)
    start_date = _parse_date(out, start, "--start")
    end_date = _parse_date(out, end, "--end") if end else datetime.now(UTC).date()

    result = _service_call(
        out,
        service.execute_backtest(
            config,
            instrument=instrument,
            start=start_date,
            end=end_date,
            strategy_id=strategy,
            warn=out.warn,
            progress=out.progress,
        ),
    )
    _report_backtest(out, result, config, report, limit)


def _report_backtest(
    out: Out, result: ReplayResult, config: AppConfig, report: bool, limit: int
) -> None:
    written = service.write_backtest(result, config, out.warn) if report else None

    payload = {"ok": True, **result.to_dict(), "report": str(written) if written else None}
    payload["signals"] = payload["signals"][:limit]
    payload["truncated"] = max(0, len(result.signal_days) - limit)

    tz = config.timezone
    label = timezone_label(tz)
    human = [
        f"백테스트 {result.instrument.key}"
        f"{f' ({result.instrument.display_name})' if result.instrument.display_name else ''}"
        f" · {result.strategy_id} @ {result.strategy_sha256[:12]}",
        f"기간 {result.start} ~ {result.end} · 판정 {len(result.days)}일 "
        f"· 워밍업 부족 {result.skipped_warmup}일 (필요 {result.startup_candles}봉)",
        "",
    ]
    rows = [
        [
            str(d.session),
            format_time(d.as_of, tz),
            format_price(d.close),
            _feature_digest(d.features),
        ]
        for d in result.signal_days[:limit]
    ]
    human.extend(
        table(rows, ["세션", f"봉 마감 ({label})", "종가", "근거"])
        or ["조건을 만족한 날이 없습니다 — 0건은 실패가 아닙니다."]
    )
    if written:
        human += ["", f"리포트 {written}"]
    human += [
        "※ 마커는 '조건 충족일'입니다. 종목 하나만 보면 횡단면 컷(상위 N개)이 "
        "적용되지 않아, 실제 실행에서는 다른 종목에 밀렸을 수 있습니다."
    ]
    out.emit(payload, human)


def _feature_digest(features: dict[str, Any]) -> str:
    """표 한 칸에 들어갈 근거 요약. 숫자 피처 3개까지."""
    parts = [
        f"{k}={v:,.4g}" if isinstance(v, float) else f"{k}={v}"
        for k, v in features.items()
        if isinstance(v, int | float) and not isinstance(v, bool)
    ]
    return " · ".join(parts[:3]) or "-"


def _service_call(out: Out, coro: Any) -> Any:
    """서비스 호출을 CLI의 종료 코드로 번역한다.

    서비스는 종료 코드를 모른다(표현은 부르는 쪽의 몫이다). 대신 `ServiceError`에
    실린 `kind`를 보고 여기서 갈라 준다 — 메시지 문자열로 분기하면 문구를 고치는
    순간 자동 실행의 판단이 바뀐다 (12.3).
    """
    try:
        return asyncio.run(coro)
    except service.ServiceError as exc:
        code = ExitCode.DATA if exc.kind == "data" else ExitCode.VALIDATION
        out.fail(code, str(exc))
        raise  # pragma: no cover - fail이 이미 Exit를 던진다


def _parse_date(out: Out, raw: str, flag: str) -> date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    out.fail(
        ExitCode.VALIDATION,
        f"{flag}의 날짜 형식이 잘못됐습니다: {raw!r}. 20251201 또는 2025-12-01로 적으세요.",
    )
    raise  # pragma: no cover - fail이 이미 Exit를 던진다


# ==================================================================== alert-test
@cli.command("alert-test")
def alert_test(as_json: JsonOpt = False) -> None:
    """알림 채널로 테스트 메시지를 한 통 보냅니다.

    설정한 토큰이 맞는지 **스케줄 시각까지 기다려야 알 수 있으면** 설정이 반쯤
    끝난 것이다 — 그래서 이 명령이 있습니다. 토큰이 없으면 아무 데도 보내지 않고
    그 사실을 알려 줍니다.

    ⚠️ 이 명령은 예외적으로 바깥으로 나갑니다(§12.2의 "알림은 `serve`만"). 사람이
    **채널을 시험하려고 명시적으로** 부른 것이고, 신호가 아니라 테스트 문구를 보냅니다.
    """
    out = Out(as_json)
    channel = default_channel()
    if channel.id == "log":
        out.fail(
            ExitCode.VALIDATION,
            "보낼 채널이 없습니다 — STOCKSCAN_TELEGRAM_TOKEN·STOCKSCAN_TELEGRAM_CHAT_ID를 "
            f"설정하세요 ({get_settings().resolve('.env')} 또는 환경변수).",
        )
    stamp = format_time(datetime.now(UTC))
    delivery = asyncio.run(channel.send(f"🔔 stockscan 테스트 알림 ({stamp})"))
    if not delivery.ok:
        out.fail(ExitCode.DATA, f"보내지 못했습니다 — {delivery.error}")
    out.emit(
        {"ok": True, "channel": channel.id, "sent_at": delivery.at.isoformat()},
        [f"{channel.id} 채널로 보냈습니다. 받은 메시지를 확인하세요."],
    )


# ======================================================================== scorecard
@cli.command()
def scorecard(
    days: Annotated[int, typer.Option("--days", min=1, max=3650)] = 30,
    strategy: Annotated[str | None, typer.Option("--strategy")] = None,
    send: Annotated[
        bool, typer.Option("--send", help="알림 채널로도 보냅니다 (기본은 화면만)")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """성적표 — **알림을 얼마나 믿어야 하는지**에 답합니다 (§4.8).

    "내가 정한 규칙이 실제로 어땠는가"를 냅니다. 숫자를 혼자 두지 않습니다 —
    승률 옆에는 **기저율**(같은 기간 전 종목의 승률), 수익률 옆에는 **벤치마크 대비**.
    그것이 없으면 상승장에서 아무거나 찍어도 나오는 승률을 전략의 공으로 돌리게 됩니다.

    ★ **가장 값진 줄은 "산 것 vs 무시한 것"입니다.** 무시한 신호가 더 좋았다면
    재량이 손해라는 뜻이고, 개인 투자자가 이 숫자를 보는 일은 거의 없습니다.

    `--send`를 붙이면 텔레그램으로도 갑니다. `serve`는 한 달에 한 번 자동으로 보냅니다.
    """
    out = Out(as_json)
    if not _database_exists():
        out.emit(
            {"ok": True, "signals": 0},
            ["신호가 없습니다 (`run --commit`으로 먼저 기록하세요)."],
        )
        return

    card = asyncio.run(_scorecard(days, strategy))
    text = _render_scorecard(card)
    if send:
        _send_now(out, text)
    out.emit({"ok": True, **card}, text.split("\n"))


async def _scorecard(days: int, strategy: str | None) -> dict[str, Any]:
    from app import scorecard as sc

    await db.init_db()
    try:
        async with db.session_scope() as session:
            card = await sc.build(session, now=datetime.now(UTC), days=days, strategy=strategy)
        return card.to_dict()
    finally:
        await db.dispose()


def _render_scorecard(payload: dict[str, Any]) -> str:
    from app import scorecard as sc

    card = sc.Scorecard(
        strategy=payload["strategy"],
        days=payload["days"],
        signals=payload["signals"],
        evaluated=payload["evaluated"],
        horizons=[sc.Horizon(**h) for h in payload["horizons"]],
        override=sc.Override(**payload["override"]),
        notes=payload["notes"],
    )
    return sc.render(card)


def _send_now(out: Out, text: str) -> None:
    """⚠️ 예외적으로 바깥으로 나갑니다 — 사람이 `--send`로 **명시적으로** 부른 것이고,
    신호가 아니라 집계입니다 (12.2의 "알림은 serve만"이 겨누는 것은 신호 알림입니다)."""
    channel = default_channel()
    if channel.id == "log":
        out.warn("보낼 채널이 없어 화면에만 냅니다 (텔레그램 토큰 미설정).")
        return
    delivery = asyncio.run(channel.send(text))
    if not delivery.ok:
        out.warn(f"보내지 못했습니다 — {delivery.error}")


# ========================================================================= evaluate
@cli.command()
def evaluate(
    limit: Annotated[
        int, typer.Option("--limit", min=1, max=10000, help="한 번에 훑을 신호 수")
    ] = 500,
    as_json: JsonOpt = False,
) -> None:
    """신호의 **사후 수익률**을 채웁니다 (§4.8).

    성적표(`scorecard`)와 알림에 붙는 "최근 N건 승률"이 이 값 위에 섭니다.
    `as_of`로부터 1·5·20봉 뒤 종가를 `ohlcv_cache`에서 찾아 `signals`에 적습니다.
    **외부 호출이 없습니다** — 필요한 봉은 이미 캐시에 있습니다.

    ⚠️ **아직 N봉이 안 지난 신호는 비워 둡니다.** 0으로 채우면 최근 신호가 전부
    "수익률 0%"로 잡혀 통계가 조용히 희석됩니다. 시간이 지나면 다시 부르면 됩니다.

    ⚠️ **봉이 끊긴 종목은 세어서 보여 줍니다.** 유니버스에서 밀린 종목은 대개 내린
    종목이라, 조용히 빼면 **손실만 골라서 결측**됩니다 (규칙 18).
    """
    out = Out(as_json)
    if not _database_exists():
        out.emit(
            {"ok": True, "scanned": 0, "filled": {}, "pending": 0, "missing_bars": []},
            ["신호가 없습니다 (`run --commit`으로 먼저 기록하세요)."],
        )
        return

    report = asyncio.run(_evaluate(limit))
    filled = " · ".join(f"{k} {v}건" for k, v in report["filled"].items()) or "없음"
    human = [
        f"훑은 신호 {report['scanned']}건 · 새로 채움 {filled}",
        f"아직 봉이 모자란 신호 {report['pending']}건 (시간이 지나면 채워집니다)",
    ]
    if report["missing_bars"]:
        human.append(
            f"⚠️ 봉이 없어 채우지 못한 종목 {len(report['missing_bars'])}개 — "
            f"{', '.join(report['missing_bars'][:10])}"
        )
        human.append(
            "   `stockscan ingest --commit`으로 봉을 쌓으세요. 밀린 종목은 대개 "
            "내린 종목이라, 이대로 두면 성적표가 낙관 편향됩니다 (규칙 18)."
        )
    out.emit({"ok": True, **report}, human)


async def _evaluate(limit: int) -> dict[str, Any]:
    from app.evaluate import evaluate as run_evaluate

    await db.init_db()
    try:
        async with db.session_scope() as session:
            return (await run_evaluate(session, limit=limit)).to_dict()
    finally:
        await db.dispose()


# =========================================================================== serve
@cli.command()
def serve() -> None:
    """상주 실행 — 스케줄 + 알림 + 하트비트.

    설정의 `scheduleTrigger`가 정한 시각에 `run --commit`을 부르고, **신호가
    0건이어도 하루 1회 하트비트를 보냅니다** — 없으면 "신호가 없는 것"과 "프로세스가
    죽은 것"이 구분되지 않습니다.

    ⚠️ **알림이 나가는 유일한 명령입니다.** 사람이 손으로 부른 실행은 채널로
    아무것도 내보내지 않습니다 — 손으로 돌릴 때마다 메시지가 나가면 알림 자체를
    믿지 않게 됩니다.

    화면은 없습니다. 결과를 보는 창구는 텔레그램과 CLI(`signals` · `stats` ·
    `explain`)입니다 — 버튼이 하던 일은 전부 터미널에서 되는 일이었습니다.
    """
    from app.schedule import Schedule
    from app.serve import Scheduler

    out = Out(False)

    # 스케줄이 없거나 채널이 없으면 **시작할 때** 말한다. 하루가 지난 뒤
    # "왜 아무것도 안 왔지"가 되면 늦다 (미구현을 성공처럼 보이지 않게 한다).
    try:
        schedule = Schedule.from_config(app_config.load())
    except Exception as exc:  # noqa: BLE001
        schedule = None
        out.warn(f"설정을 읽지 못해 스케줄이 돌지 않습니다 — {exc}")
    if schedule is None:
        out.warn("설정에 scheduleTrigger가 없어 아무것도 돌지 않습니다.")
        raise typer.Exit(int(ExitCode.VALIDATION))

    for line in schedule.describe():
        out.progress(f"스케줄 {line}")
    if schedule.heartbeat is None:
        out.warn(
            "하트비트가 없습니다. 프로세스가 조용히 죽으면 '신호 0건'과 구분되지 "
            "않습니다 — scheduleTrigger에 heartbeat를 넣으세요."
        )
    channel = default_channel()
    if channel.id == "log":
        out.warn(
            "텔레그램 토큰이 없어 알림을 **기록만** 합니다 (아무 데도 안 갑니다). "
            "STOCKSCAN_TELEGRAM_TOKEN·STOCKSCAN_TELEGRAM_CHAT_ID를 설정하세요."
        )

    out.progress("상주 실행을 시작합니다. 종료는 Ctrl+C.")
    try:
        asyncio.run(Scheduler(channel).run_forever())
    except KeyboardInterrupt:  # pragma: no cover - 사람이 끄는 경로
        out.progress("종료합니다.")


# ============================================================================ ingest
@cli.command()
def ingest(
    venue: Annotated[
        str | None, typer.Option("--venue", help="수집할 venue (예: krx, nasdaq)")
    ] = None,
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
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
    config = _load_config(out, config_path)
    asyncio.run(
        _ingest(out, config, venue, lookback, include_delisted, force, now, commit, limit)
    )


async def _ingest(  # noqa: PLR0913 - CLI 플래그를 그대로 옮긴 것뿐이다
    out: Out,
    config: AppConfig,
    venue: str | None,
    lookback: int | None,
    include_delisted: bool,
    force: bool,
    now_raw: str | None,
    commit: bool,
    limit: int,
) -> None:
    outcome = await service.execute_ingest(
        config,
        venue=venue,
        lookback=lookback,
        include_delisted=include_delisted,
        force=force,
        now=_parse_now(out, now_raw),
        commit=commit,
    )
    if not outcome.committed:
        _ingest_plan_only(out, outcome, venue, limit)
        return

    _report_ingest(out, outcome.plan, outcome.report, venue, limit)
    if outcome.report.failures and outcome.report.fetched == 0:
        # 전부 실패한 것은 소스가 죽었다는 뜻이다. 자동 실행이 이 차이를 알아야 한다.
        raise typer.Exit(int(ExitCode.DATA))


def _ingest_plan_only(
    out: Out, outcome: service.IngestOutcome, venue: str | None, limit: int
) -> None:
    """dry-run — 무엇을 모을지와 지금 캐시에 뭐가 있는지만 보여 준다.

    커버리지를 함께 내는 이유는 "왜 아직 신호가 안 나오는가"의 답이 대개
    **캐시가 얕아서**이기 때문이다. 대상 목록만으로는 그것이 보이지 않는다.
    """
    plan, rows = outcome.plan, outcome.coverage
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

    config_info: dict[str, Any] = {"path": str(app_config.default_path()), "loaded": False}
    try:
        config = app_config.load()
    except ConfigError as exc:
        config_info["error"] = str(exc)
    else:
        config_info = {
            "path": str(app_config.default_path()),
            "loaded": True,
            "pipeline_id": config.pipeline_id,
            "strategy": config.strategy,
            "universe": dict(config.universe),
            "universe_summary": config.describe_universe(),
            "timezone": config.timezone,
            "schedule": [t.strftime("%H:%M") for t in config.schedule.at],
            "heartbeat": (
                config.schedule.heartbeat.strftime("%H:%M")
                if config.schedule.heartbeat
                else None
            ),
            "alerts": "telegram" if all(config.telegram.resolved()) else "log",
        }

    last = asyncio.run(_last_run())

    payload = {
        "ok": True,
        "config_dir": str(settings.resolve(".")),
        "strategies_dir": str(strategies_dir()),
        "strategies": strategies,
        "config": config_info,
        "timeframes": sorted(JUDGEMENT),
        # 설정값 그대로가 아니라 **실제로 열 경로**를 낸다. 상대 경로는 설정 디렉터리
        # 기준이라, 적힌 값만 보여 주면 "그래서 어느 파일인가"에 답하지 못한다.
        "database": db.database_url(),
        # 커버리지는 `ingest`가 대상별로 낸다 (3.9). 여기서 다시 집계하면 읽기 전용
        # 명령이 캐시 테이블을 훑게 되고, DB가 없을 때의 분기가 하나 더 생긴다.
        "cache_coverage": None,
        "last_run": last,
    }
    human = [
        f"설정 디렉터리 {settings.resolve('.')}",
        f"전략 디렉터리 {strategies_dir()}",
        *(
            table(
                [
                    [s["id"], s.get("timeframe", "-"), s["sha256"][:12], s["loadable"]]
                    for s in strategies
                ],
                ["전략", "봉", "sha256", "로드"],
            )
            or ["  (전략 없음 — `stockscan strategy new <이름>`으로 만드세요)"]
        ),
        "",
        f"설정 {config_info['path']}"
        + ("" if config_info["loaded"] else f" — ⚠️ {config_info.get('error', '읽지 못했습니다')}"),
        *(
            [
                f"전략 {config_info['strategy']} · 유니버스 {config_info['universe_summary']}",
                f"스케줄 {' · '.join(config_info['schedule']) or '(없음)'}"
                f" · 하트비트 {config_info['heartbeat'] or '(없음)'}"
                f" · 알림 {config_info['alerts']}",
            ]
            if config_info["loaded"]
            else []
        ),
        f"마지막 실행 {last['run_id'] if last else '(없음)'}",
        "캐시 커버리지 — `stockscan ingest`가 대상별로 보여 줍니다",
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
    tz = _display_tz()
    human = table(
        [
            [
                r["id"],
                r["instrument"],
                r.get("display_name") or "",
                format_time(r["as_of"], tz),
                r["strategy_id"],
                r["acted"],
            ]
            for r in rows
        ],
        ["id", "종목", "이름", f"봉 마감 ({timezone_label(tz)})", "전략", "실행"],
    ) or ["신호가 없습니다. `stockscan run --commit`으로 기록됩니다."]
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
            "기록된 신호가 없습니다. `stockscan run --commit`으로 먼저 신호를 남기세요.",
        )
    record = asyncio.run(_ack(signal_id, acted))
    if record is None:
        out.fail(
            ExitCode.VALIDATION,
            f"신호 {signal_id}번을 찾을 수 없습니다. `stockscan signals list`로 id를 확인하세요.",
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
            f"신호 {signal_id}번을 찾을 수 없습니다. `stockscan signals list`로 id를 확인하세요.",
        )
        return

    strategy = payload["strategy"]
    human = [
        f"{payload['instrument']}{_name_suffix(payload)} · "
        f"봉 마감 {format_time(payload['as_of'], _display_tz())} ({_display_tz_label()}) "
        f"· {payload['timeframe']}",
        *(
            [f"종가  {format_price_change(payload['close'], payload['change_pct'])}"]
            if payload.get("close") is not None
            else []
        ),
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


def _display_tz() -> str:
    """읽기 전용 명령의 표시 타임존.

    `explain`·`signals`는 파이프라인을 읽지 않으므로 그 설정을 볼 수 없다.
    `RunSettings`의 기본값을 그대로 쓴다 — 표시 규약의 출처를 두 곳에 두면
    같은 신호가 명령마다 다른 시각으로 보인다.
    """
    return RunSettings().user_timezone


def _display_tz_label() -> str:
    return timezone_label(_display_tz())


def _name_suffix(payload: dict[str, Any]) -> str:
    name = payload.get("display_name")
    return f" ({name})" if name else ""


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
        "display_name": meta.get("display_name"),
        "close": meta.get("close"),
        "change_pct": meta.get("change_pct"),
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

    사후 성적은 `stockscan scorecard`가 냅니다 — 이쪽은 건수와 분산입니다.
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
            "※ 무시한 신호의 사후 성과는 `stockscan scorecard`가 냅니다.",
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
    """설정 디렉터리(`~/.stockscan`)의 전략과 소스 해시를 보여 줍니다."""
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
        [f"다음: compute를 채우고 `stockscan strategy check {name}`을 돌리세요."],
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


def _load_config(out: Out, path: Path | None) -> AppConfig:
    try:
        return app_config.load(path)
    except ConfigError as exc:
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
        "display_name": (row.meta or {}).get("display_name"),
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
