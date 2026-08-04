"""FastAPI 앱 — 대시보드 · 리포트 뷰어 · 명령 실행.

**읽기(대시보드·리포트)는 GET, 실행은 POST다.** 부작용이 있는 것을 GET에 두면
브라우저의 프리페치나 새로고침이 실행을 부른다 — `run --commit`이 열려 있는
화면에서 그건 봉을 소리 없이 소비하는 길이다 (규칙 11).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app import service
from app.alerts import default_channel
from app.cli import pipeline_file
from app.core.config import get_settings
from app.core.formatting import format_price, format_time
from app.market.timeframe import JUDGEMENT
from app.schemas.pipeline import PipelineSpec
from app.serve import Scheduler, SchedulerState
from app.storage import db, history
from app.strategies.registry import StrategyError, discover, load_strategy, strategies_dir

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: 대시보드에 띄울 최근 리포트 수. 전부 나열하면 몇 달 뒤 화면이 리포트 목록이 된다.
RECENT_REPORTS = 12


@dataclass
class Flash:
    """실행 결과 배너. 성공도 실패도 **화면에 남는다** — 조용히 지나가면
    "눌렀는데 아무 일도 없었다"가 되고, 그게 규칙 11을 의심하게 만든다."""

    kind: str  # ok | warn | error
    message: str
    link: str | None = None
    link_text: str = ""


def create_app(*, scheduler: bool = True) -> FastAPI:
    """웹 앱. `scheduler=False`면 화면만 띄운다 (테스트·수동 실행 전용).

    스케줄 루프를 앱 수명주기에 묶는 이유는 하나다 — **화면이 살아 있는 동안에만
    알림이 나가야** 하고, 반대로 화면이 떠 있는데 스케줄이 죽어 있으면 안 된다.
    """
    state = SchedulerState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if scheduler:
            loop = Scheduler(default_channel(), state)
            task = asyncio.create_task(loop.run_forever())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="marketscan", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.scheduler_state = state

    # ---------------------------------------------------------------- 읽기 (GET)
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Any:
        return await _render_dashboard(request)

    @app.get("/reports", response_class=HTMLResponse)
    async def reports(request: Request) -> Any:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="reports.html",
            context={"reports": _reports(limit=None), "title": "리포트"},
        )

    @app.get("/reports/{name}")
    async def report_file(name: str) -> Any:
        """리포트 HTML을 그대로 돌려준다.

        ★ **경로를 반드시 가둔다.** `..`나 절대 경로가 들어오면 리포트 디렉터리
        바깥의 파일을 그대로 내보내게 된다 — 이 프로세스는 `~/.marketscan` 전체에
        접근할 수 있고 거기엔 DB도 있다.
        """
        target = _safe_report(name)
        if target is None:
            return HTMLResponse("<h1>404</h1><p>그런 리포트가 없습니다.</p>", status_code=404)
        return FileResponse(target, media_type="text/html")

    # ---------------------------------------------------------------- 실행 (POST)
    @app.post("/actions/run", response_class=HTMLResponse)
    async def action_run(
        request: Request,
        market: str = Form(default=""),
        commit: str = Form(default=""),
    ) -> Any:
        committed = commit == "on"
        try:
            spec = pipeline_file.load()
            if market:
                spec, _ = pipeline_file.filter_by_market(spec, market)
        except pipeline_file.PipelineFileError as exc:
            return await _render_dashboard(request, Flash("error", str(exc)))

        warnings: list[str] = []
        # ★ 한 번에 하나만 (service.run_lock 참조). 화면에 버튼이 생기면 연타가
        #   기본이고, 겹친 `--commit` 둘은 같은 봉을 두 번 소비한다.
        if service.run_lock().locked():
            return await _render_dashboard(
                request, Flash("warn", "이미 실행 중입니다. 끝난 뒤 다시 눌러 주세요.")
            )

        async with service.run_lock():
            try:
                outcome = await service.execute_run(
                    spec,
                    commit=committed,
                    # ⚠️ 화면에서 누른 실행은 알림을 보내지 않는다. 손으로 돌릴 때마다
                    #    채널로 나가면 알림 자체를 믿지 않게 된다 (12.2).
                    allow_alerts=False,
                    warn=warnings.append,
                )
            except Exception as exc:  # noqa: BLE001 - 화면은 죽지 않고 사유를 보여 준다
                return await _render_dashboard(request, Flash("error", f"실행 실패 — {exc}"))
            report = service.write_report(outcome, spec, warnings.append)

        label = "기록" if committed else "dry-run"
        flash = Flash(
            "ok",
            f"{label} 완료 · 신호 {outcome.written}건"
            + (f" · 경고 {len(warnings)}건" if warnings else ""),
            link=f"/reports/{report.name}" if report else None,
            link_text="리포트 열기",
        )
        return await _render_dashboard(request, flash, outcome=outcome, spec=spec)

    @app.post("/actions/backtest", response_class=HTMLResponse)
    async def action_backtest(
        request: Request,
        instrument: str = Form(...),
        start: str = Form(...),
        end: str = Form(default=""),
        strategy: str = Form(default=""),
    ) -> Any:
        try:
            spec = pipeline_file.load()
            start_date = _parse_date(start)
            end_date = _parse_date(end) if end else datetime.now(UTC).date()
        except (pipeline_file.PipelineFileError, ValueError) as exc:
            return await _render_dashboard(request, Flash("error", str(exc)))

        warnings: list[str] = []
        try:
            result = await service.execute_backtest(
                spec,
                instrument=instrument,
                start=start_date,
                end=end_date,
                strategy_id=strategy or None,
                warn=warnings.append,
            )
        except service.ServiceError as exc:
            return await _render_dashboard(request, Flash("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            return await _render_dashboard(request, Flash("error", f"백테스트 실패 — {exc}"))

        report = service.write_backtest(result, spec, warnings.append)
        flash = Flash(
            "ok",
            f"{result.instrument.key} · 판정 {len(result.days)}일 · "
            f"조건 충족 {len(result.signal_days)}일 "
            f"(마커는 조건 충족일이지 실제 신호일이 아닙니다)",
            link=f"/reports/{report.name}" if report else None,
            link_text="차트 보기",
        )
        return await _render_dashboard(request, flash)

    @app.post("/actions/ingest", response_class=HTMLResponse)
    async def action_ingest(
        request: Request,
        venue: str = Form(default=""),
        commit: str = Form(default=""),
    ) -> Any:
        committed = commit == "on"
        try:
            spec = pipeline_file.load()
        except pipeline_file.PipelineFileError as exc:
            return await _render_dashboard(request, Flash("error", str(exc)))

        if service.run_lock().locked():
            return await _render_dashboard(request, Flash("warn", "이미 실행 중입니다."))

        async with service.run_lock():
            try:
                outcome = await service.execute_ingest(
                    spec, venue=venue or None, commit=committed
                )
            except Exception as exc:  # noqa: BLE001
                return await _render_dashboard(request, Flash("error", f"수집 실패 — {exc}"))

        if committed:
            report = outcome.report
            message = (
                f"저장 완료 {report.fetched}/{report.planned}종목 · "
                f"새 봉 {report.inserted}개 · 갱신 {report.updated}개 "
                f"(이미 최신 {report.skipped_fresh} · 실패 {len(report.failures)})"
            )
        else:
            uncached = sum(1 for row in outcome.coverage if not row["bars"])
            message = (
                f"받을 대상 {len(outcome.plan.targets)}종목 · 아직 없는 종목 {uncached}개 "
                f"— 실제로 받으려면 '지금 받아서 저장'을 누르세요 (아직 아무것도 받지 않았습니다)"
            )
        return await _render_dashboard(request, Flash("ok", message))

    @app.post("/actions/alert-test", response_class=HTMLResponse)
    async def action_alert_test(request: Request) -> Any:
        """채널이 살아 있는지 지금 확인한다 — 스케줄 시각까지 기다리지 않게."""
        channel = default_channel()
        if channel.id == "log":
            return await _render_dashboard(
                request,
                Flash(
                    "warn",
                    "보낼 채널이 없습니다 — 텔레그램 토큰·chat_id를 "
                    f"{get_settings().resolve('.env')} 또는 환경변수에 설정하세요 "
                    "(MARKETSCAN_TELEGRAM_TOKEN · MARKETSCAN_TELEGRAM_CHAT_ID).",
                ),
            )
        stamp = format_time(datetime.now(UTC))
        delivery = await channel.send(f"🔔 marketscan 테스트 알림 ({stamp})")
        flash = (
            Flash("ok", f"{channel.id} 채널로 보냈습니다. 받은 메시지를 확인하세요.")
            if delivery.ok
            else Flash("error", f"보내지 못했습니다 — {delivery.error}")
        )
        return await _render_dashboard(request, flash)

    return app


# --------------------------------------------------------------------------- 내부
async def _render_dashboard(
    request: Request,
    flash: Flash | None = None,
    outcome: service.RunOutcome | None = None,
    spec: PipelineSpec | None = None,
) -> Any:
    settings = get_settings()
    context: dict[str, Any] = {
        "title": "marketscan",
        "flash": flash,
        "config_dir": settings.resolve("."),
        "strategies_dir": strategies_dir(),
        "strategies": _strategies(),
        "reports": _reports(limit=RECENT_REPORTS),
        "timeframes": sorted(JUDGEMENT),
        "markets": sorted(pipeline_file.MARKETS),
        "signals": _signal_rows(outcome, spec),
        "today": datetime.now(UTC).date().isoformat(),
        "scheduler": _scheduler_rows(request),
    }
    context.update(_pipeline_context())
    context["last_run"] = await _last_run()
    return TEMPLATES.TemplateResponse(request=request, name="dashboard.html", context=context)


def _scheduler_rows(request: Request) -> dict[str, Any]:
    """스케줄 상태. **"다음 언제"와 "마지막 결과"가 같이 보여야** 죽은 것을 알아챈다."""
    state: SchedulerState | None = getattr(request.app.state, "scheduler_state", None)
    if state is None:
        return {"running": False}
    return {
        "running": True,
        "error": state.error,
        "lines": state.schedule.describe() if state.schedule else [],
        "next_fire": format_time(state.next_fire) if state.next_fire else None,
        "next_heartbeat": format_time(state.next_heartbeat) if state.next_heartbeat else None,
        "skipped": state.skipped_on_start,
        "history": [
            {
                "at": format_time(f.at),
                "label": f.label,
                "ok": f.ok,
                "detail": f.detail,
            }
            for f in reversed(state.history[-8:])
        ],
        "deliveries": [
            {"at": format_time(d.at), "channel": d.channel, "ok": d.ok,
             "text": d.text.splitlines()[0] if d.text else "", "error": d.error}
            for d in reversed(state.deliveries[-5:])
        ],
    }


def _pipeline_context() -> dict[str, Any]:
    try:
        spec = pipeline_file.load()
    except pipeline_file.PipelineFileError as exc:
        return {"pipeline": None, "pipeline_error": str(exc), "pipeline_path": None}
    universe = pipeline_file.universe_summary(spec)
    return {
        "pipeline": spec,
        "pipeline_error": None,
        "pipeline_path": pipeline_file.default_path(),
        "universe": pipeline_file.describe_universe(universe),
        "strategy_ids": pipeline_file.strategy_ids(spec),
    }


def _strategies() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in discover():
        entry: dict[str, Any] = {**source.to_dict(), "loadable": True, "timeframe": "-"}
        try:
            entry.update(load_strategy(source.id).strategy.descriptor())
        except StrategyError as exc:
            entry.update({"loadable": False, "error": str(exc)})
        rows.append(entry)
    return rows


def _reports(limit: int | None) -> list[dict[str, Any]]:
    root = get_settings().resolve(get_settings().reports_dir)
    if not root.is_dir():
        return []
    files = sorted(root.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = [
        {
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024),
            "modified": format_time(datetime.fromtimestamp(f.stat().st_mtime, UTC)),
            "kind": "백테스트" if f.name.startswith("backtest_") else "실행",
        }
        for f in files
    ]
    return rows if limit is None else rows[:limit]


def _safe_report(name: str) -> Path | None:
    """리포트 디렉터리 안의 `.html` 하나로 가둔다."""
    if "/" in name or "\\" in name or not name.endswith(".html"):
        return None
    root = get_settings().resolve(get_settings().reports_dir).resolve()
    target = (root / name).resolve()
    # `..`을 다 편 뒤에도 리포트 디렉터리 **안**이어야 한다.
    if root not in target.parents or not target.is_file():
        return None
    return target


def _signal_rows(
    outcome: service.RunOutcome | None, spec: PipelineSpec | None
) -> list[dict[str, Any]]:
    if outcome is None or spec is None:
        return []
    tz = spec.settings.user_timezone
    return [
        {
            "instrument": s["instrument"],
            "display_name": s.get("display_name") or "",
            "close": format_price(s.get("close")),
            "as_of": format_time(s["as_of"], tz),
            "market": (s.get("features") or {}).get("rank_pool") or "-",
            "rank": (s.get("features") or {}).get("rank") or "-",
            "strategy_id": s.get("strategy_id") or "",
        }
        for s in outcome.signals[:50]
    ]


async def _last_run() -> dict[str, Any] | None:
    """마지막 실행. **DB가 없으면 열지 않는다** — 화면을 여는 것만으로 파일이 생기면
    "읽기 전용은 DB 파일조차 만들지 않는다"(12.1)가 웹에서 깨진다."""
    if not service.database_exists():
        return None
    async with db.session_scope() as session:
        record = await history.last_run(session)
    return record.to_dict() if record else None


def _parse_date(raw: str) -> Any:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"날짜 형식이 잘못됐습니다: {raw!r}. 20251201 또는 2025-12-01로 적으세요.")

