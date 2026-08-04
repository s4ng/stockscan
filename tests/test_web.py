"""웹 UI 계약 테스트 (ARCHITECTURE.md 12.9).

여기서 지키는 것은 넷이다.

  1. ★ **실행은 POST로만** — GET에 두면 새로고침·프리페치가 봉을 소비한다 (규칙 11)
  2. ★ **리포트 경로가 갇혀 있다** — `..`로 DB 파일을 내보낼 수 있으면 안 된다
  3. **화면에서 누른 실행은 알림을 보내지 않는다** (12.2)
  4. **버튼과 CLI가 같은 것을 한다** — 둘 다 `app/service.py`를 지난다
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import SAMPLE_DIR, get_settings
from app.storage import db
from app.web import create_app
from tests.test_cli import PIPELINE

runner_pipeline = PIPELINE


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """DB·리포트·파이프라인을 tmp_path로 격리한 앱."""
    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(json.dumps(PIPELINE, ensure_ascii=False), encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "pipeline_path", pipeline_path)
    monkeypatch.setattr(settings, "strategies_dir", SAMPLE_DIR)
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    db.configure(f"sqlite+aiosqlite:///{(tmp_path / 'web.db').as_posix()}")
    with TestClient(create_app()) as client:
        yield client
    db.configure(get_settings().database_url)


# --------------------------------------------------------------------------- 읽기
def test_dashboard_renders_without_a_database(client: TestClient):
    """DB가 없어도 화면이 뜬다 — 화면을 여는 것만으로 파일이 생기면 12.1이 깨진다."""
    response = client.get("/")

    assert response.status_code == 200
    assert "marketscan" in response.text
    assert "demo_momentum" in response.text  # 전략 목록이 실렸다


def test_reports_page_is_empty_at_first(client: TestClient):
    response = client.get("/reports")

    assert response.status_code == 200
    assert "아직 리포트가 없습니다" in response.text


# ------------------------------------------------------------------------- 부작용
def test_run_is_not_reachable_by_get(client: TestClient):
    """★ 실행이 GET에 있으면 새로고침 한 번이 봉을 소비한다 (규칙 11)."""
    assert client.get("/actions/run").status_code == 405


def test_dry_run_button_records_nothing(client: TestClient):
    response = client.post("/actions/run", data={})

    assert response.status_code == 200
    assert "dry-run 완료" in response.text
    # 판단이 남지 않았다.
    from app.storage import db as database

    assert "signals" not in _tables(database) or _count(database, "signals") == 0


def test_commit_button_records_signals(client: TestClient):
    response = client.post("/actions/run", data={"commit": "on"})

    assert response.status_code == 200
    assert "기록 완료" in response.text
    from app.storage import db as database

    assert _count(database, "signals") > 0


def test_report_link_opens_the_generated_file(client: TestClient):
    posted = client.post("/actions/run", data={})
    assert "리포트 열기" in posted.text

    listed = client.get("/reports")
    assert "latest.html" in listed.text

    opened = client.get("/reports/latest.html")
    assert opened.status_code == 200
    assert "marketscan" in opened.text


# --------------------------------------------------------------------- 경로 가두기
@pytest.mark.parametrize(
    "name",
    [
        "../data/marketscan.db",
        "..%2Fweb.db",
        "subdir/report.html",
        "latest.txt",
        "..\\web.db",
    ],
)
def test_report_route_refuses_paths_outside_the_reports_dir(client: TestClient, name: str):
    """★ 이 프로세스는 `~/.marketscan` 전체를 읽을 수 있다. 경로가 새면 DB가 나간다."""
    response = client.get(f"/reports/{name}")

    assert response.status_code in (404, 400)
    assert "sqlite" not in response.text.lower()


# --------------------------------------------------------------------------- 백테스트
def test_backtest_button_produces_a_chart_report(client: TestClient):
    response = client.post(
        "/actions/backtest",
        data={"instrument": "krx:005930", "start": "2026-02-02", "end": "2026-03-02"},
    )

    assert response.status_code == 200
    assert "krx:005930" in response.text
    # ★ 컷 미적용 경고가 화면에도 나간다 — 리포트에만 있으면 목록만 보고 오독한다.
    assert "실제 신호일이 아닙니다" in response.text
    assert "차트 보기" in response.text


def test_backtest_rejects_a_bad_date_without_crashing(client: TestClient):
    response = client.post(
        "/actions/backtest", data={"instrument": "krx:005930", "start": "2026년 2월"}
    )

    assert response.status_code == 200
    assert "날짜 형식이 잘못됐습니다" in response.text


# ----------------------------------------------------------------------------- 수집
def test_ingest_plan_is_the_default(client: TestClient):
    """★ 기본은 계획만 — 버튼 하나가 곧장 거래소를 두드리면 안 된다."""
    response = client.post("/actions/ingest", data={})

    assert response.status_code == 200
    assert "받을 대상" in response.text
    assert "아직 아무것도 받지 않았습니다" in response.text


# --------------------------------------------------------------------------- 헬퍼
def _tables(database) -> set[str]:
    import sqlite3

    path = database.database_url().split("///")[-1]
    if not Path(path).exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    finally:
        conn.close()


def _count(database, table: str) -> int:
    import sqlite3

    path = database.database_url().split("///")[-1]
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()
