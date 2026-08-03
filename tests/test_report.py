"""HTML 리포트 테스트 (ARCHITECTURE.md 2.1 / 7장).

리포트는 **반년 뒤에 열어도 그대로 보여야 한다.** 그래서 확인하는 것은 모양이
아니라 두 가지다 — 외부 리소스를 참조하지 않는가, 그리고 dry-run 여부가 파일에
남는가.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import main as cli_main
from app.core.config import get_settings
from app.engine.runner import NodeRunRecord, NodeStatus, RunResult, RunStatus
from app.report.run_report import ReportInput, report_path, write_run_report
from app.storage import db
from tests.test_cli import PIPELINE

runner = CliRunner()
NOW = "2026-03-10T12:00:00Z"


def make_result(status: RunStatus = RunStatus.SUCCESS) -> RunResult:
    return RunResult(
        run_id="run_test123",
        pipeline_id="pipe_test",
        mode="notify",
        now=NOW,
        status=status,
        nodes=[
            NodeRunRecord(
                node_id="data",
                type="marketData",
                status=NodeStatus.SUCCESS,
                duration_ms=12.3,
                logs=["[info] 3개 종목 수집 완료 (1d)"],
            )
        ],
    )


SIGNAL = {
    "instrument": "krx:005930",
    "timeframe": "1d",
    "as_of": "2026-03-09T06:30:00+00:00",
    "strategy_id": "demo_momentum",
    "strategy_sha256": "a" * 64,
    "features": {"rank": 1, "universe_size": 6, "percentile": 16.7, "score": 0.42},
    "tags": {},
}


# ------------------------------------------------------------------- 자기완결성
def test_report_has_no_external_references(tmp_path: Path):
    """CDN·폰트·이미지를 걸면 그 링크가 죽는 날 과거 리포트가 통째로 깨진다."""
    path = write_run_report(
        ReportInput(make_result(), [SIGNAL], committed=True), tmp_path / "r.html"
    )
    text = path.read_text(encoding="utf-8")

    assert not re.search(r'(src|href)\s*=\s*["\']https?://', text)
    assert "<script" not in text.lower()


def test_report_escapes_untrusted_text(tmp_path: Path):
    """종목명·로그는 소스에서 온 문자열이다. 그대로 넣으면 마크업이 깨진다."""
    hostile = {**SIGNAL, "instrument": "krx:<script>alert(1)</script>"}
    path = write_run_report(
        ReportInput(make_result(), [hostile], committed=True), tmp_path / "r.html"
    )
    text = path.read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


# ----------------------------------------------------------------- dry-run 표시
def test_dry_run_is_marked_in_the_file(tmp_path: Path):
    """리포트만 보고도 이게 실제로 나간 판단인지 알 수 있어야 한다."""
    dry = write_run_report(
        ReportInput(make_result(), [SIGNAL], committed=False), tmp_path / "dry.html"
    )
    wet = write_run_report(
        ReportInput(make_result(), [SIGNAL], committed=True), tmp_path / "wet.html"
    )

    assert "dry-run" in dry.read_text(encoding="utf-8")
    assert "dry-run" not in wet.read_text(encoding="utf-8")


def test_dry_runs_share_one_file_but_commits_are_kept(tmp_path: Path):
    """전략을 고치며 스무 번 돌려도 파일이 스무 개 쌓이면 안 된다."""
    assert report_path("run_a", committed=False, directory=tmp_path).name == "latest.html"
    assert report_path("run_b", committed=False, directory=tmp_path).name == "latest.html"
    assert report_path("run_a", committed=True, directory=tmp_path).name == "run_a.html"
    # run_id에 이미 붙어 있는 접두사를 겹쳐 붙이지 않는다
    assert report_path("a", committed=True, directory=tmp_path).name == "run_a.html"


def test_zero_signals_reads_as_normal_not_broken(tmp_path: Path):
    """빈 결과는 정상이다 (4.1). 리포트에서도 그렇게 읽혀야 한다."""
    path = write_run_report(ReportInput(make_result(), [], committed=True), tmp_path / "e.html")
    text = path.read_text(encoding="utf-8")

    assert "신호 0건" in text
    assert "실패와 다릅니다" in text


def test_failed_nodes_are_surfaced(tmp_path: Path):
    result = make_result(RunStatus.PARTIAL)
    result.nodes.append(
        NodeRunRecord(
            node_id="strategy",
            type="strategyRunner",
            status=NodeStatus.ERROR,
            error="전략을 찾을 수 없습니다",
        )
    )
    text = write_run_report(
        ReportInput(result, [], committed=True), tmp_path / "f.html"
    ).read_text(encoding="utf-8")

    assert "전략을 찾을 수 없습니다" in text


# ---------------------------------------------------------------------- CLI 연동
@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(json.dumps(PIPELINE, ensure_ascii=False), encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "pipeline_path", pipeline_path)
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    db.configure(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    yield tmp_path
    db.configure(get_settings().database_url)


def test_run_writes_a_report_by_default(workspace: Path):
    result = runner.invoke(cli_main.cli, ["run", "--now", NOW, "--json"])
    body = json.loads(result.stdout)

    assert result.exit_code == 0
    assert body["report"].endswith("latest.html")
    assert Path(body["report"]).exists()


def test_no_report_flag_writes_nothing(workspace: Path):
    result = runner.invoke(cli_main.cli, ["run", "--now", NOW, "--no-report", "--json"])

    assert json.loads(result.stdout)["report"] is None
    assert not (workspace / "reports").exists()


def test_run_never_reports_sending_alerts(workspace: Path):
    """단일 실행은 외부로 아무것도 내보내지 않는다 — 출력이 그렇게 말해야 한다."""
    body = json.loads(runner.invoke(cli_main.cli, ["run", "--now", NOW, "--json"]).stdout)
    assert body["alerts_sent"] is False

    committed = json.loads(
        runner.invoke(cli_main.cli, ["run", "--now", NOW, "--commit", "--json"]).stdout
    )
    assert committed["alerts_sent"] is False


def test_report_keeps_every_signal_even_when_stdout_is_limited(workspace: Path):
    """좁혀야 하는 쪽은 화면이지 파일이 아니다."""
    body = json.loads(
        runner.invoke(cli_main.cli, ["run", "--now", NOW, "--json", "--limit", "1"]).stdout
    )
    text = Path(body["report"]).read_text(encoding="utf-8")

    assert len(body["signals"]) == 1
    assert body["truncated"] >= 1
    assert text.count("<tr>") > len(body["signals"]) + 1  # 신호 여러 건 + 노드 행


# ------------------------------------------------------------------ 가격 표시
def test_price_format_adapts_to_magnitude():
    """★ 한 유니버스에 삼성전자 239,500원과 알트코인 0.00000123이 함께 들어온다.

    자릿수를 고정하면 한쪽이 반드시 못 읽힌다 — 코인은 `0.00`으로 찌그러지고
    주식은 소수점이 붙는다.
    """
    from app.core.formatting import format_price, format_price_change

    assert format_price(239500) == "239,500"
    assert format_price(1567000.0) == "1,567,000"
    assert format_price(308.91) == "308.91"
    assert format_price(0.00000123) == "0.00000123"  # 지수 표기가 아니어야 한다
    assert format_price(0) == "0"
    assert format_price(None) == ""

    assert format_price_change(239500, 0.0234) == "239,500 (+2.34%)"
    assert format_price_change(308.91, -0.0051) == "308.91 (-0.51%)"
    assert format_price_change(100, None) == "100.00"  # 등락률을 모르면 가격만


def test_change_pct_uses_the_judged_bar_not_live_price():
    """마감된 봉으로만 판단하므로(4.4) 표시도 그 봉을 따라가야 한다."""
    import pandas as pd

    from app.engine.types import Item
    from app.market.instrument import InstrumentRef

    index = pd.DatetimeIndex(pd.date_range("2026-08-01", periods=3, tz="UTC"), name="time")
    frame = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [100.0, 100.0, 100.0],
            "low": [100.0, 100.0, 100.0],
            "close": [100.0, 200.0, 220.0],
            "volume": [1.0, 1.0, 1.0],
        },
        index=index,
    )
    item = Item(
        instrument=InstrumentRef.parse("krx:005930"),
        timeframe="1d",
        as_of=index[-1].to_pydatetime(),
        ohlcv=frame,
    )

    assert item.last_close == 220.0
    assert item.change_pct == pytest.approx(0.1)  # 200 → 220


def test_single_bar_has_no_change():
    import pandas as pd

    from app.engine.types import Item
    from app.market.instrument import InstrumentRef

    index = pd.DatetimeIndex(pd.date_range("2026-08-01", periods=1, tz="UTC"), name="time")
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=index,
    )
    item = Item(
        instrument=InstrumentRef.parse("krx:005930"),
        timeframe="1d",
        as_of=index[-1].to_pydatetime(),
        ohlcv=frame,
    )

    assert item.change_pct is None


def test_change_is_coloured_red_up_blue_down():
    """★ 상승 빨강 · 하락 파랑 — 국내 HTS 관례다. 뒤집으면 정반대로 읽힌다."""
    from app.report.run_report import _price_cell

    up = _price_cell(239500, 0.0234)
    down = _price_cell(308.91, -0.0051)
    flat = _price_cell(100, 0.0)

    assert 'class="chg up"' in up and "+2.34%" in up
    assert 'class="chg down"' in down and "-0.51%" in down
    assert 'class="chg flat"' in flat
    # 가격 자체에는 색을 주지 않는다 — 넓게 깔리면 어느 쪽이 신호인지 흐려진다
    assert up.startswith("239,500 <span")


def test_price_cell_without_change_is_plain():
    from app.report.run_report import _price_cell

    assert _price_cell(100, None) == "100.00"
    assert _price_cell(None, None) == "-"
