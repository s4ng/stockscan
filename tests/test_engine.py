"""엔진 계약 테스트 (ARCHITECTURE.md Phase 0 산출물).

네트워크 없이 synthetic 소스로만 돈다. 같은 입력 + 같은 시각 → 같은 출력임을
확인하는 결정성 테스트가 핵심이다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

import app.nodes  # noqa: F401  — 노드 등록
from app.engine.context import RunContext
from app.engine.graph import PipelineValidationError, execution_levels, validate
from app.engine.runner import NodeStatus, RunStatus, execute
from app.engine.types import Bundle, Item, empty_ohlcv
from app.market.calendar import krx_calendar, us_equity_calendar
from app.market.instrument import InstrumentRef
from app.market.timeframe import TIMEFRAMES
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.providers.base import MarketDataProvider, ProviderCapabilities
from app.providers.registry import ProviderRegistry
from app.providers.synthetic import SyntheticProvider
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


class _EmptyParams(BaseModel):
    """테스트용 노드의 파라미터 자리."""


def make_pipeline(**overrides) -> PipelineSpec:
    instruments = overrides.pop("instruments", ["krx:005930", "krx:000660"])
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("krx:005930", "KRW"),
        ("nasdaq:AAPL", "USD"),
        ("nyse:KO", "USD"),
    ],
)
def test_quote_currency_comes_from_venue(raw: str, expected: str):
    """주식은 venue가 통화를 고정한다 (3.7). 통화가 틀리면 표기와 비교가 함께 어긋난다."""
    assert InstrumentRef.parse(raw).quote_currency == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("krx:005930", "krx"), ("nasdaq:AAPL", "us"), ("nyse:KO", "us")],
)
def test_market_groups_nasdaq_and_nyse_together(raw: str, expected: str):
    """랭킹 풀의 어휘다 (규칙 17). nasdaq과 nyse를 나눌 이유가 없다."""
    assert InstrumentRef.parse(raw).market == expected


# ------------------------------------------------------------------------- 캘린더
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


@pytest.mark.parametrize(
    "mode", [ExecutionMode.BACKTEST, ExecutionMode.NOTIFY, ExecutionMode.SHADOW]
)
def test_intraday_timeframe_is_rejected_in_every_mode(mode: ExecutionMode):
    """v0.4는 백테스트만 막았지만 v0.5는 판단 자체에서 분봉을 제외한다 (규칙 12)."""
    result = validate(make_pipeline(timeframe="1h"), mode)
    assert not result.ok
    assert any("판단 단위는" in i.message for i in result.errors)


@pytest.mark.parametrize("timeframe", ["1d", "1w"])
def test_daily_and_weekly_timeframes_pass(timeframe: str):
    assert validate(make_pipeline(timeframe=timeframe), ExecutionMode.BACKTEST).ok


def test_timeframe_types_are_not_frozen():
    """정책 계층에서만 막는다 — 타입을 Literal["1d"]로 굳히면 되돌릴 수 없다 (3.6)."""
    assert "1h" in TIMEFRAMES  # 정규화 표는 그대로 살아 있다
    assert krx_calendar().last_closed_bar(NOW, "1h") is not None


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
    spec = make_pipeline(instruments=["krx:005930", "nasdaq:AAPL", "nyse:KO"])
    ctx = RunContext.create(settings=spec.settings, now=NOW)
    result = await execute(spec, ctx)

    for item in result.node("data").outputs["main"]["items"]:
        assert datetime.fromisoformat(item["as_of"]) <= NOW


async def test_fresh_bar_gate_skips_unchanged_bar():
    """같은 봉으로 두 번 실행하면 두 번째는 stale로 제외된다."""
    spec = make_pipeline()
    ctx = RunContext.create(settings=spec.settings, now=NOW, commit=True)

    first = await execute(spec, ctx)
    second = await execute(spec, ctx)  # 같은 ctx → bar_state 공유

    assert first.node("data").outputs["main"]["count"] == 2
    assert second.node("data").outputs["main"]["count"] == 0


async def test_dry_run_does_not_consume_the_bar():
    """`--commit` 없이 돈 실행이 봉을 삼키면, 다음 실제 실행에서 stale로 걸러져
    **그 신호가 영영 사라진다.** 기본값이 안전한 쪽이어야 한다 (규칙 11 / 12.2)."""
    spec = make_pipeline()
    ctx = RunContext.create(settings=spec.settings, now=NOW, commit=False)

    first = await execute(spec, ctx)
    second = await execute(spec, ctx)

    assert first.node("data").outputs["main"]["count"] == 2
    assert second.node("data").outputs["main"]["count"] == 2


async def test_fresh_bar_gate_passes_on_new_bar():
    spec = make_pipeline()
    ctx = RunContext.create(settings=spec.settings, now=NOW, commit=True)
    await execute(spec, ctx)

    later = RunContext.create(
        settings=spec.settings,
        now=NOW + timedelta(days=2),
        bar_state=ctx.bar_state,
        commit=True,
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
                params={"instruments": ["krx:005930"], "source": "does_not_exist"},
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
                params={"instruments": ["krx:005930"], "source": "does_not_exist"},
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


async def test_failed_run_does_not_consume_the_bar():
    """실행이 실패하면 봉은 소비되지 않는다 — 아니면 그 신호가 영영 사라진다."""
    spec = make_pipeline()
    # data 노드가 봉을 예약한 뒤, 하류에서 런타임 실패를 일으킨다
    spec.nodes.append(
        NodeSpec(
            id="boom",
            type="marketData",
            params={"instruments": ["krx:005930"], "source": "does_not_exist"},
            on_error=OnError(policy=ErrorPolicy.FAIL),
        )
    )
    spec.edges.append(EdgeSpec(id="e5", source="alert", target="boom"))

    ctx = RunContext.create(settings=spec.settings, now=NOW, commit=True)
    first = await execute(spec, ctx)
    assert first.status is RunStatus.FAILED

    # 같은 봉으로 다시 돌리면 stale이 아니라 정상 수집되어야 한다
    second = await execute(spec, ctx)
    assert second.node("data").outputs["main"]["count"] == 2


async def test_external_message_nodes_never_run_in_a_single_run():
    """단일 실행의 산출물은 stdout과 HTML뿐이다. 손으로 돌릴 때마다 채널로 메시지가
    나가면 알림 자체를 신뢰하지 않게 된다 — 전송은 serve의 몫이다."""
    sent: list[str] = []

    @register
    class FakeTelegramNode(BaseNode):
        type = "fakeTelegram"
        display_name = "가짜 텔레그램"
        category = "action"
        ParamsModel = _EmptyParams
        sends_external_messages = True

        async def run(self, inputs, params, ctx):
            sent.append(ctx.run_id)  # 여기 닿으면 실제로 나간 것이다
            return {"main": inputs.get("main", Bundle.empty())}

    spec = make_pipeline()
    spec.nodes.append(NodeSpec(id="tg", type="fakeTelegram"))
    spec.edges.append(EdgeSpec(id="e9", source="alert", target="tg"))

    # --commit을 붙여도 CLI 실행에서는 열리지 않는다
    ctx = RunContext.create(settings=spec.settings, now=NOW, commit=True)
    result = await execute(spec, ctx)

    assert sent == []
    assert result.node("tg").status is NodeStatus.SKIPPED
    assert result.status is RunStatus.SUCCESS  # skip은 실패가 아니다


async def test_serve_can_open_the_alert_channel():
    """차단은 정책이지 봉인이 아니다 — allow_alerts를 켜면 그대로 돈다."""
    sent: list[str] = []

    @register
    class FakeChannelNode(BaseNode):
        type = "fakeChannel"
        display_name = "가짜 채널"
        category = "action"
        ParamsModel = _EmptyParams
        sends_external_messages = True

        async def run(self, inputs, params, ctx):
            sent.append(ctx.run_id)
            return {"main": inputs.get("main", Bundle.empty())}

    spec = make_pipeline()
    spec.nodes.append(NodeSpec(id="ch", type="fakeChannel"))
    spec.edges.append(EdgeSpec(id="e9", source="alert", target="ch"))

    ctx = RunContext.create(
        settings=spec.settings, now=NOW, commit=True, allow_alerts=True
    )
    result = await execute(spec, ctx)

    assert sent == [ctx.run_id]
    assert result.node("ch").status is NodeStatus.SUCCESS


async def test_alerts_stay_shut_without_commit_even_when_allowed():
    """allow_alerts만으로는 부족하다 — 규칙 11의 --commit이 여전히 앞을 막는다."""
    ctx = RunContext.create(now=NOW, commit=False, allow_alerts=True)
    assert ctx.sends_alerts is False


@pytest.mark.parametrize("mode", [ExecutionMode.BACKTEST, ExecutionMode.SHADOW])
def test_backtest_and_shadow_never_send(mode: ExecutionMode):
    ctx = RunContext.create(now=NOW, mode=mode, commit=True, allow_alerts=True)
    assert ctx.sends_alerts is False


async def test_bundle_merge_keeps_timeframes_apart():
    """같은 종목의 일봉·시간봉 item이 서로를 덮어쓰면 안 된다."""
    daily = Item(
        instrument=InstrumentRef.parse("krx:005930"),
        timeframe="1d",
        as_of=NOW,
        ohlcv=empty_ohlcv(),
        features={"sma_20": 1.0},
    )
    hourly = replace(daily, timeframe="1h", features={"sma_20": 2.0})

    merged = Bundle.merge([Bundle([daily]), Bundle([hourly])])

    assert len(merged) == 2
    assert {it.timeframe for it in merged.items} == {"1d", "1h"}


async def test_source_fallback_is_recorded_in_run_history():
    """폴백은 조용히 넘어가면 안 된다 — 로그와 meta 양쪽에 남아야 한다."""

    class BrokenProvider(MarketDataProvider):
        id = "broken"
        display_name = "항상 실패하는 소스"
        venues = ("krx",)
        credential_schema = None
        capabilities = ProviderCapabilities(timeframes=("1d",))

        async def fetch_ohlcv(self, instrument, timeframe, end, limit):
            raise RuntimeError("연결 거부")

    registry = ProviderRegistry()
    registry.register(BrokenProvider())
    registry.register(SyntheticProvider())
    registry.set_route("krx", "*", ["broken", "synthetic"])

    spec = make_pipeline(instruments=["krx:005930"])
    ctx = RunContext.create(settings=spec.settings, now=NOW, providers=registry)
    result = await execute(spec, ctx)

    record = result.node("data")
    assert record.status is NodeStatus.SUCCESS
    assert any("소스 폴백 발동" in line for line in record.logs)
    assert record.outputs["main"]["count"] == 1
