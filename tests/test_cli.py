"""CLI 계약 테스트 (ARCHITECTURE.md 12장).

여기서 지키는 것은 셋이다.

  1. **`--commit` 없이는 부작용이 없다** (규칙 11) — DB 파일조차 만들지 않는다
  2. **종료 코드가 상태를 구분한다** — 신호 0건(0)과 검증 실패(3)는 다르다
  3. **`--json`이면 stdout에 JSON만 나간다** — LLM이 파싱하는 표면이다
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import main as cli_main
from app.core.config import SAMPLE_DIR, get_settings
from app.storage import db

runner = CliRunner()

PIPELINE = {
    "pipeline_id": "pipe_test",
    "name": "테스트",
    "settings": {"default_mode": "notify"},
    "nodes": [
        {
            "id": "data",
            "type": "marketData",
            "params": {
                "instruments": ["upbit:KRW-BTC", "krx:005930", "nasdaq:AAPL"],
                "timeframe": "1d",
                "lookback": 80,
                # 테스트는 네트워크를 타지 않는다. 기본 라우팅은 코인을 실물
                # 거래소로 보내므로(DEFAULT_ROUTES) 소스를 못 박는다.
                "source": "synthetic",
            },
        },
        {
            "id": "strategy",
            "type": "strategyRunner",
            "params": {"strategy_id": "demo_momentum", "params": {"top_pct": 1.0}},
        },
        {"id": "persist", "type": "persistSignal", "params": {}},
    ],
    "edges": [
        {"id": "e1", "source": "data", "target": "strategy"},
        {"id": "e2", "source": "strategy", "target": "persist"},
    ],
}

NOW = "2026-03-10T12:00:00Z"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """DB와 파이프라인 파일을 tmp_path로 격리한다."""
    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(json.dumps(PIPELINE, ensure_ascii=False), encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "pipeline_path", pipeline_path)
    # 전략은 파이프라인 파일 옆에서 찾는 것이 기본이다. 여기서는 파이프라인만
    # tmp_path로 옮기므로 전략 디렉터리를 저장소 예제로 못 박는다.
    monkeypatch.setattr(settings, "strategies_dir", SAMPLE_DIR)
    db.configure(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    yield tmp_path
    db.configure(get_settings().database_url)


def invoke(*args: str):
    return runner.invoke(cli_main.cli, list(args))


def payload(result) -> dict:
    return json.loads(result.stdout)


# ------------------------------------------------------------------- 부작용 규약
def test_dry_run_records_no_signals(workspace: Path):
    """★ dry-run이 막는 것은 **되돌릴 수 없는 것** 셋이다 — 알림·`signals`·봉 소비.

    봉을 캐시에 넣는 것은 그중 어느 것도 아니라서 dry-run도 쓴다(3.9). 그래서
    DB 파일 자체는 생기고, 확인해야 할 것은 "파일이 없다"가 아니라 **"판단이
    남지 않았다"**로 바뀐다.
    """
    result = invoke("run", "--now", NOW)
    assert result.exit_code == 0
    assert payload(invoke("signals", "list", "--json"))["count"] == 0


def test_commit_records_signals(workspace: Path):
    dry = invoke("run", "--now", NOW, "--json")
    assert payload(dry)["committed"] is False

    committed = invoke("run", "--now", NOW, "--commit", "--json")
    body = payload(committed)
    assert body["committed"] is True
    assert body["signal_count"] > 0

    listed = payload(invoke("signals", "list", "--json"))
    assert listed["count"] == body["signal_count"]
    assert listed["signals"][0]["strategy_id"] == "demo_momentum"


def test_commit_and_dry_run_together_are_refused(workspace: Path):
    result = invoke("run", "--commit", "--dry-run")
    assert result.exit_code == 3


def test_second_commit_run_records_nothing_new(workspace: Path):
    """같은 봉으로 다시 돌리면 새로 기록되는 신호가 0건이다 — 정상이다."""
    first = payload(invoke("run", "--now", NOW, "--commit", "--json"))
    second = payload(invoke("run", "--now", NOW, "--commit", "--json"))

    assert first["signal_count"] > 0
    assert second["signal_count"] == 0
    assert second["ok"] is True  # 신호 0건은 실패가 아니다


def test_bar_gate_survives_a_new_process(workspace: Path):
    """★ Phase 1에서 막은 구멍 — 봉 상태가 프로세스를 넘어 남는다 (3.5).

    두 번째 실행의 marketData는 **0종목을 수집해야 한다.** 신호가 0건인 것만으로는
    부족하다 — 그건 `signals.dedup_key` UNIQUE로도 같은 모양이 나오기 때문이다.
    막는 지점이 봉 게이트라는 것은 수집 건수에서만 드러난다.
    """
    first = payload(invoke("run", "--now", NOW, "--commit", "--json"))
    second = payload(invoke("run", "--now", NOW, "--commit", "--json"))

    def collected(body: dict) -> int:
        return next(n["items"] for n in body["nodes"] if n["node_id"] == "data")

    assert collected(first) == 3
    assert collected(second) == 0


def test_dry_run_reads_the_bar_gate_but_does_not_consume(workspace: Path):
    """dry-run은 실제 실행을 **예측**해야 하고, 봉을 삼켜서는 안 된다.

    삼키면 다음 `--commit` 실행에서 stale로 걸러져 그 신호가 영영 사라진다 (규칙 11).
    """
    payload(invoke("run", "--now", NOW, "--commit", "--json"))
    dry = payload(invoke("run", "--now", NOW, "--json"))
    committed = payload(invoke("run", "--now", NOW, "--commit", "--json"))

    def collected(body: dict) -> int:
        return next(n["items"] for n in body["nodes"] if n["node_id"] == "data")

    assert collected(dry) == 0  # 커밋된 봉을 읽었다
    assert collected(committed) == 0  # dry-run이 새 봉을 소비하지 않았다


def test_dry_run_does_not_consume_bars(workspace: Path):
    """★ 봉 소비만은 dry-run이 절대 하면 안 된다 — 그 신호는 영영 사라진다 (3.5).

    캐시 쓰기가 dry-run에 열리면서 DB 파일은 생기지만, `bar_state`는 그대로여야 한다.
    두 번을 돌려도 같은 봉을 계속 수집하는 것이 그 증거다.
    """
    first = payload(invoke("run", "--now", NOW, "--json"))
    second = payload(invoke("run", "--now", NOW, "--json"))

    def collected(body: dict) -> int:
        return next(n["items"] for n in body["nodes"] if n["node_id"] == "data")

    assert collected(first) == 3
    assert collected(second) == 3  # 봉을 삼키지 않았다
    assert payload(invoke("signals", "list", "--json"))["count"] == 0


def test_dry_run_still_fills_the_cache(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """받아 놓고 버리면 무료 API를 두 번 두드린다 — 그 호출을 아끼는 것이 3.9다."""
    import sqlite3
    from dataclasses import replace as dc_replace

    from app.providers.registry import ProviderRegistry
    from app.providers.synthetic import SyntheticProvider

    def cacheable_registry() -> ProviderRegistry:
        # 진짜 synthetic은 `cacheable=False`라 캐시에 남지 않는다(그게 옳다).
        # 여기서는 캐시 **쓰기 경로**를 보려는 것이므로 실제 소스를 흉내 낸다.
        source = SyntheticProvider()
        source.capabilities = dc_replace(SyntheticProvider.capabilities, cacheable=True)
        registry = ProviderRegistry()
        registry.register(source)
        return registry

    monkeypatch.setattr("app.engine.context.default_registry", cacheable_registry)

    result = invoke("run", "--now", NOW)
    assert result.exit_code == 0

    conn = sqlite3.connect(workspace / "test.db")
    try:
        assert conn.execute("select count(*) from ohlcv_cache").fetchone()[0] > 0
        # 판단은 남지 않았다 — 캐시 쓰기와 부작용은 별개다
        assert conn.execute("select count(*) from signals").fetchone()[0] == 0
        # `bar_state` 테이블은 생기지도 않는다. dry-run의 저장소가 readonly라
        # DDL 자체를 돌리지 않기 때문이다 — 봉이 소비되지 않았다는 가장 강한 증거다.
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        assert "bar_state" not in tables
    finally:
        conn.close()


# --------------------------------------------------------------------- 종료 코드
def test_zero_signals_still_exits_zero(workspace: Path, tmp_path: Path):
    """4.1이 빈 Bundle을 정상이라고 정했으므로 자동 실행이 매일 실패로 잡히면 안 된다."""
    spec = json.loads(json.dumps(PIPELINE))
    # 워밍업(60봉)에 못 미치는 lookback → 전 종목이 제외되어 신호가 0건이 된다
    spec["nodes"][0]["params"]["lookback"] = 10
    path = tmp_path / "short.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    result = invoke("run", "-p", str(path), "--now", NOW, "--json")
    assert result.exit_code == 0
    body = payload(result)
    assert body["signal_count"] == 0
    assert body["ok"] is True


def test_missing_pipeline_file_exits_three(tmp_path: Path):
    result = invoke("run", "-p", str(tmp_path / "nope.json"))
    assert result.exit_code == 3


def test_intraday_timeframe_exits_three(workspace: Path, tmp_path: Path):
    spec = json.loads(json.dumps(PIPELINE))
    spec["nodes"][0]["params"]["timeframe"] = "1h"
    path = tmp_path / "intraday.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    result = invoke("run", "-p", str(path), "--json")
    assert result.exit_code == 3
    assert "판단 단위는" in result.stdout


def test_bad_now_exits_three(workspace: Path):
    assert invoke("run", "--now", "어제").exit_code == 3


# --------------------------------------------------------------------- 출력 규약
def test_json_output_is_parseable(workspace: Path):
    """진행 로그가 stdout에 섞이면 LLM 쪽 파싱이 깨진다."""
    body = payload(invoke("run", "--now", NOW, "--json"))
    assert body["pipeline_id"] == "pipe_test"
    assert isinstance(body["signals"], list)


def test_limit_caps_signal_output(workspace: Path):
    body = payload(invoke("run", "--now", NOW, "--json", "--limit", "1"))
    assert len(body["signals"]) == 1
    assert body["truncated"] >= 1


def test_ohlcv_is_never_in_the_default_output(workspace: Path):
    body = payload(invoke("run", "--now", NOW, "--json"))
    # 리포트 경로는 뺀다 — 임시 디렉터리 이름이 이 테스트의 이름을 포함해서
    # (pytest가 그렇게 짓는다) 경로만으로 "ohlcv"가 잡힌다.
    body.pop("report", None)
    assert "ohlcv" not in json.dumps(body)


# ------------------------------------------------------------------------ 시장 필터
def test_market_filter_narrows_the_universe(workspace: Path):
    body = payload(invoke("run", "--market", "krx", "--now", NOW, "--json"))
    venues = {s["venue"] for s in body["signals"]}
    assert venues <= {"krx"}


def test_unknown_market_exits_three(workspace: Path):
    assert invoke("run", "--market", "화성").exit_code == 3


# --------------------------------------------------------- 읽기 전용 명령 (부작용 없음)
def test_read_only_commands_do_not_create_the_database(workspace: Path):
    for args in (("signals", "list"), ("stats",), ("describe",)):
        assert invoke(*args).exit_code == 0
    assert not (workspace / "test.db").exists()


def test_explain_returns_the_evidence_chain(workspace: Path):
    invoke("run", "--now", NOW, "--commit")
    signal_id = payload(invoke("signals", "list", "--json"))["signals"][0]["id"]

    body = payload(invoke("explain", str(signal_id), "--json"))
    assert body["strategy"]["id"] == "demo_momentum"
    assert len(body["strategy"]["sha256"]) == 64  # 4.7 — 어느 코드로 돌았는가
    assert body["data"]["fallback_from"] == []  # 3.4 — 폴백 가시화
    assert body["run"]["status"] == "success"
    assert any(n["node_id"] == "strategy" for n in body["nodes"])


def test_explain_records_what_the_rank_was_measured_against(workspace: Path):
    """분모(`universe_size`)는 **그 시장에서** 점수가 나온 종목 수다 (규칙 17).

    이 파이프라인은 코인·한국·미국 한 종목씩이라 랭킹 풀이 셋으로 갈린다.
    `universe_scanned`(훑은 전체)와 `universe_size`(내 풀의 크기)가 다른 것이
    정상이고, `rank_pool`이 없으면 "1 / 1"이 무엇의 1인지 알 수 없다.
    """
    invoke("run", "--now", NOW, "--commit")
    signal_id = payload(invoke("signals", "list", "--json"))["signals"][0]["id"]

    features = payload(invoke("explain", str(signal_id), "--json"))["strategy"]["features"]

    assert features["universe_scanned"] == 3  # 훑은 종목 전체
    assert features["universe_size"] == 1  # 그중 같은 시장에서 점수가 나온 종목
    assert features["rank_pool"] in {"crypto", "krx", "us"}


@pytest.mark.parametrize(
    ("features", "expected"),
    [
        ({"universe_size": 34, "universe_scanned": 60}, "60종목을 훑어 26종목"),
        ({"universe_size": 3, "universe_scanned": 3}, ""),  # 같으면 적지 않는다
        ({"universe_size": 3}, ""),  # 옛 신호에는 없는 값이다
    ],
)
def test_excluded_note_only_appears_when_the_numbers_differ(features: dict, expected: str):
    note = cli_main._excluded_note(features)

    assert expected in note
    if not expected:
        assert note == ""


def test_explain_unknown_signal_exits_three(workspace: Path):
    assert invoke("explain", "424242").exit_code == 3


def test_stats_reports_no_quality_metrics_yet(workspace: Path):
    """없는 숫자를 만들어 내지 않는다 (4.8). Phase 3 전까지는 명시적으로 null이다."""
    invoke("run", "--now", NOW, "--commit")
    body = payload(invoke("stats", "--json"))
    assert body["quality_metrics"] is None
    assert body["groups"][0]["group"] == "demo_momentum"


def test_stats_rejects_unknown_group_by(workspace: Path):
    invoke("run", "--now", NOW, "--commit")
    assert invoke("stats", "--group-by", "없는기준").exit_code == 3


# -------------------------------------------------------------------------- 전략
def test_strategy_new_then_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "strategies_dir", tmp_path)

    created = invoke("strategy", "new", "my_factor", "--json")
    assert created.exit_code == 0
    assert (tmp_path / "my_factor.py").exists()

    checked = invoke("strategy", "check", "my_factor", "--json")
    assert checked.exit_code == 0
    assert payload(checked)["ok"] is True


def test_strategy_new_refuses_to_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "strategies_dir", tmp_path)
    invoke("strategy", "new", "dup")
    assert invoke("strategy", "new", "dup").exit_code == 3


def test_strategy_check_fails_on_future_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "strategies_dir", tmp_path)
    (tmp_path / "leaky.py").write_text(
        "from app.strategies import Strategy\n\n\n"
        "class Leaky(Strategy):\n"
        "    id = 'leaky'\n"
        "    class Params:\n        pass\n"
        "    def compute(self, item, p, ctx):\n"
        "        return item.with_features(x=item.ohlcv['close'].shift(-1).iloc[-1])\n",
        encoding="utf-8",
    )
    result = invoke("strategy", "check", "leaky", "--json")
    assert result.exit_code == 3
    assert payload(result)["violations"][0]["rule"] == "causality"


# ------------------------------------------------------------------ 파이프라인 형식
def test_yaml_pipeline_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """형식은 YAML로 확정됐다 (11장 4번). 6장 스키마는 그대로다."""
    import yaml

    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(PIPELINE, allow_unicode=True), encoding="utf-8")
    # 전략은 설정 파일 **옆**에서 찾는다. 설정만 옮기면 전략을 못 찾는 것이 정상이다.
    shutil.copy(SAMPLE_DIR / "demo_momentum.py", tmp_path / "demo_momentum.py")
    db.configure(f"sqlite+aiosqlite:///{(tmp_path / 'y.db').as_posix()}")

    body = payload(invoke("run", "-p", str(path), "--now", NOW, "--json"))

    assert body["pipeline_id"] == "pipe_test"
    assert body["signal_count"] > 0
    db.configure(get_settings().database_url)


def test_strategy_is_looked_up_next_to_the_pipeline_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """설정과 전략은 한 디렉터리에 산다 — 설정만 옮기면 전략을 못 찾는다.

    이것이 `~/.marketscan/config.yml`과 저장소의 `sample/`을 같은 방식으로
    다루게 하는 규칙이다. 조용히 다른 디렉터리로 물러서면 **어느 파일이 돈
    전략인지** 알 수 없게 되고, 소스 해시를 기록하는 의미가 사라진다 (§4.7).
    """
    import yaml

    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(PIPELINE, allow_unicode=True), encoding="utf-8")
    db.configure(f"sqlite+aiosqlite:///{(tmp_path / 'n.db').as_posix()}")

    body = payload(invoke("run", "-p", str(path), "--now", NOW, "--json"))
    node = next(n for n in body["nodes"] if n["node_id"] == "strategy")

    assert body["signal_count"] == 0
    assert "전략을 찾을 수 없습니다" in str(node.get("error"))
    assert str(tmp_path) in str(node.get("error"))  # 어디서 찾았는지 말해 준다
    db.configure(get_settings().database_url)


def test_json_pipelines_still_load(workspace: Path):
    """저장 스냅샷이 JSON이므로 로더는 계속 JSON을 읽는다."""
    assert invoke("run", "--now", NOW, "--json").exit_code == 0


def test_unknown_pipeline_format_exits_three(tmp_path: Path):
    path = tmp_path / "pipeline.toml"
    path.write_text("nope = true", encoding="utf-8")

    result = invoke("run", "-p", str(path))
    assert result.exit_code == 3


def test_empty_yaml_gives_an_actionable_error(tmp_path: Path):
    path = tmp_path / "pipeline.yaml"
    path.write_text("# 주석만 있는 파일\n", encoding="utf-8")

    result = invoke("run", "-p", str(path))
    assert result.exit_code == 3


# ------------------------------------------------------------------- acted 응답
def test_signals_ack_records_the_override(workspace: Path):
    """★ 규율 기계라면 측정할 것은 전략 성과만이 아니라 사용자가 규율을 지켰는지다."""
    invoke("run", "--now", NOW, "--commit")
    signal_id = payload(invoke("signals", "list", "--json"))["signals"][0]["id"]

    acked = payload(invoke("signals", "ack", str(signal_id), "--acted", "--json"))
    assert acked["acted"] is True

    body = payload(invoke("stats", "--compare", "acted", "--json"))
    assert body["acted"]["acted"] == 1


def test_signals_ack_is_reversible(workspace: Path):
    """잘못 눌렀으면 반대로 다시 부르면 된다 — 되돌릴 수 없는 것은 봉 소비뿐이다."""
    invoke("run", "--now", NOW, "--commit")
    signal_id = payload(invoke("signals", "list", "--json"))["signals"][0]["id"]

    invoke("signals", "ack", str(signal_id), "--acted")
    reverted = payload(invoke("signals", "ack", str(signal_id), "--ignored", "--json"))

    assert reverted["acted"] is False


def test_signals_ack_can_be_filtered(workspace: Path):
    invoke("run", "--now", NOW, "--commit")
    signals = payload(invoke("signals", "list", "--json"))["signals"]
    invoke("signals", "ack", str(signals[0]["id"]), "--ignored")

    ignored = payload(invoke("signals", "list", "--ignored", "--json"))
    assert [s["id"] for s in ignored["signals"]] == [signals[0]["id"]]


def test_signals_ack_unknown_id_exits_three(workspace: Path):
    invoke("run", "--now", NOW, "--commit")
    assert invoke("signals", "ack", "424242").exit_code == 3


def test_signals_ack_without_a_database_exits_three(workspace: Path):
    assert invoke("signals", "ack", "1").exit_code == 3
    assert not (workspace / "test.db").exists()


def test_ingest_without_commit_writes_nothing(workspace: Path):
    """계획만 보여 준다. 소스도 캐시도 건드리지 않는다 (규칙 11)."""
    body = payload(invoke("ingest", "--now", NOW, "--json"))
    assert body["committed"] is False
    assert body["planned"] == 3
    assert body["uncached"] == 3  # 아직 아무것도 안 쌓였다
    assert not (workspace / "test.db").exists()


def test_ingest_commit_fills_the_cache(workspace: Path):
    body = payload(invoke("ingest", "--now", NOW, "--commit", "--json"))
    assert body["committed"] is True
    assert body["fetched"] == 3
    assert body["inserted"] > 0
    assert body["failures"] == []
    assert (workspace / "test.db").exists()

    # 두 번째 실행은 같은 봉을 다시 받지 않는다 — 무료 소스를 하루에 한 번만 밟는다.
    again = payload(invoke("ingest", "--now", NOW, "--commit", "--json"))
    assert again["skipped_fresh"] == 3
    assert again["fetched"] == 0


def test_ingest_venue_filter(workspace: Path):
    body = payload(invoke("ingest", "--now", NOW, "--venue", "krx", "--json"))
    assert body["planned"] == 1
    assert body["targets"][0]["instrument"] == "krx:005930"


def test_cache_only_run_needs_an_ingest_first(workspace: Path):
    """★ 3.9의 존재 이유 — 수집해 두면 외부 호출 없이 실행이 돈다.

    `cache: only`는 소스를 아예 부르지 않으므로, 수집 전과 후의 차이가 곧
    "캐시가 실제로 실행을 떠받쳤는가"의 증거다.
    """
    pipeline = json.loads(json.dumps(PIPELINE))
    pipeline["nodes"][0]["params"]["cache"] = "only"
    path = workspace / "cache_only.json"
    path.write_text(json.dumps(pipeline, ensure_ascii=False), encoding="utf-8")

    before = payload(invoke("run", "-p", str(path), "--now", NOW, "--json"))
    assert before["ok"] is True  # 빈 결과는 실패가 아니다 (4.1)
    assert before["signal_count"] == 0

    invoke("ingest", "-p", str(path), "--now", NOW, "--commit")
    after = payload(invoke("run", "-p", str(path), "--now", NOW, "--json"))

    assert after["signal_count"] > 0
    assert next(n["items"] for n in after["nodes"] if n["node_id"] == "data") == 3


def test_market_filter_narrows_the_dynamic_universe(workspace: Path):
    """★ `--market`이 동적 조회를 못 거르면 세 시장을 그대로 훑으면서 로그만 좁아진다."""
    pipeline = json.loads(json.dumps(PIPELINE))
    pipeline["nodes"].insert(
        0,
        {
            "id": "universe",
            "type": "symbolUniverse",
            "params": {
                "venues": [
                    {"venue": "upbit", "top_by_turnover": 5},
                    {"venue": "krx", "top_by_turnover": 5},
                ]
            },
        },
    )
    path = workspace / "mixed.json"
    path.write_text(json.dumps(pipeline, ensure_ascii=False), encoding="utf-8")

    from app.cli import pipeline_file

    spec = pipeline_file.load(path)
    filtered, dropped = pipeline_file.filter_by_market(spec, "krx")

    universe = next(n for n in filtered.nodes if n.type == "symbolUniverse")
    assert [q["venue"] for q in universe.params["venues"]] == ["krx"]
    assert "venues[upbit]" in dropped
    # 동적 조회가 남아 있으면 "종목 0개"로 보이더라도 빈 유니버스가 아니다
    assert pipeline_file.has_empty_universe(filtered) is False


# -------------------------------------------------------------------------- 백테스트
def test_backtest_marks_days_and_writes_a_report(workspace: Path):
    """`backtest`는 날짜별로 전략을 돌리고 리포트를 남긴다 (§12.7)."""
    body = payload(
        invoke(
            "backtest", "krx:005930",
            "--start", "2026-02-02", "--end", "2026-03-02", "--json",
        )
    )

    assert body["ok"] is True
    assert body["instrument"] == "krx:005930"
    assert body["strategy_id"] == "demo_momentum"
    assert body["judged_days"] > 0
    # ★ 단일 종목이라 횡단면 컷이 걸리지 않았다는 사실이 스키마에 남아야 한다.
    assert body["cut_applied"] is False
    assert Path(body["report"]).exists()


def test_backtest_leaves_no_signals_behind(workspace: Path):
    """부작용이 없다 — signals에 남지 않는다 (백테스트는 판단이 아니다)."""
    invoke("backtest", "krx:005930", "--start", "2026-02-02", "--end", "2026-03-02")

    assert payload(invoke("signals", "list", "--json"))["count"] == 0


def test_backtest_rejects_a_bad_date(workspace: Path):
    result = invoke("backtest", "krx:005930", "--start", "2026년 2월")
    assert result.exit_code == 3


def test_backtest_rejects_an_inverted_range(workspace: Path):
    result = invoke(
        "backtest", "krx:005930", "--start", "2026-03-02", "--end", "2026-02-02"
    )
    assert result.exit_code == 3


def test_backtest_uses_the_pipeline_params(workspace: Path):
    """설정 파일의 파라미터로 돈다 — 전략 기본값으로 돌면 다른 것을 검증하게 된다."""
    body = payload(
        invoke(
            "backtest", "krx:005930",
            "--start", "2026-02-02", "--end", "2026-03-02", "--json",
        )
    )

    assert body["params"]["top_pct"] == 1.0  # PIPELINE이 준 값
