"""엔진 계약 테스트 (ARCHITECTURE.md Phase 0 산출물).

네트워크 없이 synthetic 소스로만 돈다. 같은 입력 + 같은 시각 → 같은 출력임을
확인하는 결정성 테스트가 핵심이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app.nodes  # noqa: F401  — 노드 등록
from app.engine.context import RunContext
from app.engine.graph import PipelineValidationError, execution_levels, validate
from app.engine.runner import NodeStatus, RunStatus, execute
from app.market.calendar import Crypto24x7Calendar, krx_calendar, us_equity_calendar
from app.market.instrument import InstrumentRef
from app.schemas.pipeline import (
    EdgeSpec,
    ErrorPolicy,
    ExecutionMode,
    NodeSpec,
    OnError,
    PipelineSettings,
    PipelineSpec,
)

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def make_pipeline(**overrides) -> PipelineSpec:
    instruments = overrides.pop("instruments", ["upbit:KRW-BTC", "upbit:KRW-ETH"])
    timeframe = overrides.pop("timeframe", "1d")
    condition = overrides.pop("condition", "above")
    return PipelineSpec(
        pipeline_id="pipe_test",
        name="테스트 파이프라인",
        settings=PipelineSettings(**overrides.pop("settings", {})),
        nodes=[
            NodeSpec(id="trigger", type="manualTrigger"),
            NodeSpec(
                id="data",
                type="marketData",
                params={"instruments": instruments, "timeframe": timeframe, "lookback": 60},
            ),
            NodeSpec(
                id="ma", type="maFilter", params={"period": 5, "condition": condition}
            ),
            NodeSpec(
                id="split",
                type="conditionSplitter",
                params={"expression": "close > 0"},
            ),
            NodeSpec(id="alert", type="logAlert"),
        ],
        edges=[
            EdgeSpec(id="e1", source="trigger", target="data"),
            EdgeSpec(id="e2", source="data", target="ma"),
            EdgeSpec(id="e3", source="ma", target="split"),
            EdgeSpec(id="e4", source="split", source_handle="true", target="alert"),
        ],
        **overrides,
    )


# --------------------------------------------------------------------------- 심볼
def test_instrument_parse_and_key():
    inst = InstrumentRef.parse("krx:005930")
    assert inst.venue == "krx"
    assert inst.quote_currency == "KRW"
    assert inst.key == "krx:005930"


def test_instrument_rejects_bare_symbol():
    with pytest.raises(ValueError, match="venue:symbol"):
        InstrumentRef.parse("005930")


# ------------------------------------------------------------------------- 캘린더
def test_crypto_calendar_is_always_open():
    cal = Crypto24x7Calendar()
    assert cal.is_open(NOW)
    assert cal.last_closed_bar(NOW, "1h") == datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def test_krx_calendar_closed_on_weekend():
    cal = krx_calendar()
    saturday = datetime(2026, 3, 14, 5, 0, tzinfo=UTC)  # KST 14:00 토요일
    assert not cal.is_open(saturday)


def test_us_calendar_handles_dst():
    """서머타임 전후로 개장 시각(UTC)이 한 시간 달라져야 한다."""
    cal = us_equity_calendar()
    before = cal.session(datetime(2026, 3, 5, tzinfo=UTC).date())
    after = cal.session(datetime(2026, 3, 12, tzinfo=UTC).date())
    assert before and after
    assert before[0].hour == 14 and after[0].hour == 13


# --------------------------------------------------------------------------- 검증
def test_validate_detects_cycle():
    spec = PipelineSpec(
        pipeline_id="p",
        nodes=[
            NodeSpec(id="a", type="maFilter"),
            NodeSpec(id="b", type="maFilter"),
        ],
        edges=[
            EdgeSpec(id="e1", source="a", target="b"),
            EdgeSpec(id="e2", source="b", target="a"),
        ],
    )
    result = validate(spec)
    assert not result.ok
    assert any("순환" in i.message for i in result.errors)


def test_validate_detects_unknown_handle():
    spec = PipelineSpec(
        pipeline_id="p",
        nodes=[
            NodeSpec(id="a", type="manualTrigger"),
            NodeSpec(id="b", type="maFilter"),
        ],
        edges=[EdgeSpec(id="e1", source="a", source_handle="nope", target="b")],
    )
    result = validate(spec)
    assert any("출력 핸들" in i.message for i in result.errors)


def test_backtest_rejects_intraday_timeframe():
    spec = make_pipeline(timeframe="1h")
    result = validate(spec, ExecutionMode.BACKTEST)
    assert not result.ok
    assert any("일봉 이상" in i.message for i in result.errors)


def test_backtest_allows_daily_timeframe():
    spec = make_pipeline(timeframe="1d")
    assert validate(spec, ExecutionMode.BACKTEST).ok


def test_execution_levels_are_ordered():
    levels = execution_levels(make_pipeline())
    assert levels[0] == ["trigger"]
    assert levels[-1] == ["alert"]


# --------------------------------------------------------------------------- 실행
async def test_pipeline_runs_end_to_end():
    spec = make_pipeline()
    ctx = RunContext.create(settings=spec.settings, now=NOW)
    result = await execute(spec, ctx)

    assert result.status is RunStatus.SUCCESS
    assert [n.status for n in result.nodes] == [NodeStatus.SUCCESS] * 5
    assert result.node("data").outputs["main"]["count"] == 2


async def test_run_is_deterministic():
    """같은 입력 + 같은 시각 → 같은 출력 (ARCHITECTURE.md 1.2)."""
    spec = make_pipeline()
    first = await execute(spec, RunContext.create(settings=spec.settings, now=NOW))
    second = await execute(spec, RunContext.create(settings=spec.settings, now=NOW))

    assert first.node("data").outputs == second.node("data").outputs
    assert first.node("ma").outputs == second.node("ma").outputs


async def test_as_of_never_exceeds_now():
    """look-ahead 방어: 어떤 item도 실행 시각을 넘어선 봉을 기준으로 삼지 않는다."""
    spec = make_pipeline(instruments=["upbit:KRW-BTC", "krx:005930", "nasdaq:AAPL"])
    ctx = RunContext.create(settings=spec.settings, now=NOW)
    result = await execute(spec, ctx)

    for item in result.node("data").outputs["main"]["items"]:
        assert datetime.fromisoformat(item["as_of"]) <= NOW


async def test_fresh_bar_gate_skips_unchanged_bar():
    """같은 봉으로 두 번 실행하면 두 번째는 stale로 제외된다."""
    spec = make_pipeline()
    ctx = RunContext.create(settings=spec.settings, now=NOW)

    first = await execute(spec, ctx)
    second = await execute(spec, ctx)  # 같은 ctx → bar_state 공유

    assert first.node("data").outputs["main"]["count"] == 2
    assert second.node("data").outputs["main"]["count"] == 0


async def test_fresh_bar_gate_passes_on_new_bar():
    spec = make_pipeline()
    ctx = RunContext.create(settings=spec.settings, now=NOW)
    await execute(spec, ctx)

    later = RunContext.create(
        settings=spec.settings, now=NOW + timedelta(days=2), bar_state=ctx.bar_state
    )
    result = await execute(spec, later)
    assert result.node("data").outputs["main"]["count"] == 2


async def test_condition_splitter_routes_false_branch():
    spec = make_pipeline()
    for node in spec.nodes:
        if node.id == "split":
            node.params = {"expression": "close < 0"}
    ctx = RunContext.create(settings=spec.settings, now=NOW)
    result = await execute(spec, ctx)

    assert result.node("split").outputs["true"]["count"] == 0
    # true 브랜치에 아무것도 없어도 노드는 실행되고, alert는 빈 입력을 받는다
    assert result.node("alert").status is NodeStatus.SUCCESS


async def test_error_policy_route_sends_to_error_handle():
    spec = PipelineSpec(
        pipeline_id="p",
        nodes=[
            NodeSpec(
                id="data",
                type="marketData",
                params={"instruments": ["upbit:KRW-BTC"], "source": "does_not_exist"},
                on_error=OnError(policy=ErrorPolicy.ROUTE),
            ),
            NodeSpec(id="oops", type="logAlert"),
        ],
        edges=[EdgeSpec(id="e1", source="data", source_handle="error", target="oops")],
    )
    ctx = RunContext.create(now=NOW)
    result = await execute(spec, ctx)

    assert result.status is RunStatus.PARTIAL
    assert result.node("data").status is NodeStatus.ERROR
    assert result.node("oops").status is NodeStatus.SUCCESS


async def test_error_policy_fail_aborts_run():
    spec = PipelineSpec(
        pipeline_id="p",
        nodes=[
            NodeSpec(
                id="data",
                type="marketData",
                params={"instruments": ["upbit:KRW-BTC"], "source": "does_not_exist"},
                on_error=OnError(policy=ErrorPolicy.FAIL),
            ),
        ],
        edges=[],
    )
    result = await execute(spec, RunContext.create(now=NOW))
    assert result.status is RunStatus.FAILED


async def test_invalid_pipeline_is_refused_before_running():
    spec = make_pipeline()
    spec.nodes.append(NodeSpec(id="bad", type="doesNotExist"))
    with pytest.raises(PipelineValidationError):
        await execute(spec, RunContext.create(settings=spec.settings, now=NOW))
