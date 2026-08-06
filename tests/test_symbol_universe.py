"""Symbol Universe 계약 테스트.

가장 중요한 단언은 **백테스트 차단**이다. 이 경로의 미래 참조는 전략 코드가
완전히 인과적인 채로 발생하므로 `strategy check`의 AST 검사에 걸리지 않는다.
막을 곳이 여기밖에 없다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app.nodes  # noqa: F401  — 노드 등록
from app.engine.context import RunContext
from app.engine.runner import NodeStatus, execute
from app.engine.types import Bundle
from app.market.instrument import InstrumentRef
from app.nodes.base import NodeError
from app.nodes.inputs.symbol_universe import (
    UNIVERSE_KEY,
    UNIVERSE_META_KEY,
    SymbolUniverseNode,
    SymbolUniverseParams,
)
from app.providers.base import (
    MarketDataProvider,
    ProviderCapabilities,
    UniverseEntry,
    UniverseNotSupportedError,
)
from app.providers.registry import ProviderRegistry
from app.schemas.pipeline import MAIN, EdgeSpec, ExecutionMode, NodeSpec, PipelineSpec

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class FakeUniverseProvider(MarketDataProvider):
    id = "fake"
    display_name = "가짜"
    venues = ("nasdaq",)
    credential_schema = None
    capabilities = ProviderCapabilities(timeframes=("1d",), provides_universe=True)

    #: (심볼, 거래대금). None은 소스가 값을 주지 않은 경우다.
    LISTING = [
        ("BTC", 1_000.0),
        ("ETH", 900.0),
        ("DOGE", 100.0),
        ("GHOST", None),
    ]

    async def fetch_ohlcv(self, instrument, timeframe, end, limit):  # pragma: no cover
        raise NotImplementedError

    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        return [
            UniverseEntry(InstrumentRef.parse(f"{venue}:{symbol}"), volume)
            for symbol, volume in self.LISTING
        ]


def make_ctx(mode: ExecutionMode = ExecutionMode.NOTIFY) -> RunContext:
    registry = ProviderRegistry()
    registry.register(FakeUniverseProvider())
    registry.set_route("nasdaq", "*", ["fake"])
    return RunContext.create(mode=mode, now=NOW, providers=registry, pipeline_id="pipe_t").bind(
        "universe"
    )


async def run_node(params: dict, ctx: RunContext) -> Bundle:
    node = SymbolUniverseNode()
    outputs = await node.run({}, SymbolUniverseParams.model_validate(params), ctx)
    return outputs[MAIN]


# ------------------------------------------------------------------ 백테스트 차단
async def test_dynamic_universe_is_refused_in_backtest():
    """★ 거래소 목록은 언제나 '지금'이다. 과거 리플레이에 쓰면 유니버스가 미래를 본다."""
    ctx = make_ctx(ExecutionMode.BACKTEST)

    with pytest.raises(NodeError, match="백테스트"):
        await run_node({"venue": "nasdaq"}, ctx)


async def test_backtest_does_not_silently_fall_back_to_a_fixed_list():
    """조용히 물러서면 사용자가 적지 않은 유니버스로 백테스트가 돌아간다."""
    ctx = make_ctx(ExecutionMode.BACKTEST)

    with pytest.raises(NodeError):
        await run_node({"venue": "nasdaq", "instruments": ["nasdaq:BTC"]}, ctx)


async def test_fixed_list_still_works_in_backtest():
    ctx = make_ctx(ExecutionMode.BACKTEST)

    bundle = await run_node({"instruments": ["nasdaq:BTC", "nasdaq:ETH"]}, ctx)

    assert bundle.context[UNIVERSE_KEY] == ["nasdaq:BTC", "nasdaq:ETH"]
    assert bundle.context[UNIVERSE_META_KEY]["point_in_time"] is True


# ---------------------------------------------------------------------- 유니버스 산출
async def test_dynamic_universe_lists_the_exchange():
    bundle = await run_node({"venue": "nasdaq"}, make_ctx())

    assert "nasdaq:BTC" in bundle.context[UNIVERSE_KEY]
    meta = bundle.context[UNIVERSE_META_KEY]
    # 산출 근거는 venue별로 남는다 — 시장을 섞어 훑을 때 어느 소스가 어느
    # 시장을 채웠는지가 보여야 한다.
    assert meta["venues"][0]["source"] == "fake"
    assert meta["venues"][0]["venue"] == "nasdaq"
    assert meta["venues"][0]["market"] == "us"
    assert bundle.context[UNIVERSE_META_KEY]["point_in_time"] is False


async def test_top_by_turnover_cuts_and_says_so():
    """조용한 절삭 금지 — "전부 훑었다"는 오해가 실사용에서 가장 위험하다."""
    ctx = make_ctx()

    bundle = await run_node({"venue": "nasdaq", "top_by_turnover": 2}, ctx)

    assert bundle.context[UNIVERSE_KEY] == ["nasdaq:BTC", "nasdaq:ETH"]
    assert any("상위 2종목" in r.message for r in ctx.log.records)


async def test_missing_turnover_is_reported_not_treated_as_zero():
    """"거래가 없었다"와 "소스가 값을 안 줬다"는 다르다."""
    ctx = make_ctx()

    bundle = await run_node({"venue": "nasdaq", "top_by_turnover": 10}, ctx)

    assert "nasdaq:GHOST" not in bundle.context[UNIVERSE_KEY]
    assert any("거래대금을 받지 못해" in r.message for r in ctx.log.records)


async def test_exclude_removes_symbols():
    bundle = await run_node(
        {"venue": "nasdaq", "quote_currency": "KRW", "exclude": ["nasdaq:DOGE"]}, make_ctx()
    )

    assert "nasdaq:DOGE" not in bundle.context[UNIVERSE_KEY]


async def test_fixed_and_dynamic_are_merged_without_duplicates():
    bundle = await run_node(
        {"venue": "nasdaq", "quote_currency": "KRW", "instruments": ["nasdaq:BTC"]}, make_ctx()
    )

    keys = bundle.context[UNIVERSE_KEY]
    assert keys.count("nasdaq:BTC") == 1


async def test_empty_universe_is_not_silent():
    """빈 유니버스는 실패가 아니지만(4.1) 조용해서도 안 된다."""
    ctx = make_ctx()

    bundle = await run_node(
        {
            "venue": "nasdaq",
            "exclude": ["nasdaq:BTC", "nasdaq:ETH", "nasdaq:DOGE", "nasdaq:GHOST"],
        },
        ctx,
    )

    assert bundle.context[UNIVERSE_KEY] == []
    assert any("0종목" in r.message for r in ctx.log.records)


async def test_unsupported_source_gives_an_actionable_error():
    registry = ProviderRegistry()

    class NoUniverse(FakeUniverseProvider):
        id = "bare"

        async def list_instruments(self, venue: str):
            raise UniverseNotSupportedError("이 소스는 목록을 주지 않습니다")

    registry.register(NoUniverse())
    registry.set_route("nasdaq", "*", ["bare"])
    ctx = RunContext.create(now=NOW, providers=registry, pipeline_id="p").bind("universe")

    with pytest.raises(NodeError, match="목록"):
        await run_node({"venue": "nasdaq"}, ctx)


# ------------------------------------------------------------------- 하류 배선
async def test_universe_emits_no_items_only_context():
    """봉을 받기 전이라 Item을 만들 수 없다 — 없는 as_of를 지어내지 않는다."""
    bundle = await run_node({"instruments": ["nasdaq:BTC"]}, make_ctx())

    assert bundle.items == []
    assert bundle.context[UNIVERSE_KEY] == ["nasdaq:BTC"]


async def test_market_data_uses_the_upstream_universe():
    """Phase 1의 E2E 첫 두 칸 — Symbol Universe → Market Data."""
    spec = PipelineSpec(
        pipeline_id="pipe_wire",
        name="배선",
        nodes=[
            NodeSpec(
                id="universe",
                type="symbolUniverse",
                params={"instruments": ["nasdaq:BTC", "nasdaq:ETH"]},
            ),
            NodeSpec(
                id="data",
                type="marketData",
                # instruments를 적지 않는다 — 상류가 정한다
                params={"timeframe": "1d", "lookback": 60, "source": "synthetic"},
            ),
        ],
        edges=[EdgeSpec(id="e1", source="universe", target="data")],
    )
    ctx = RunContext.create(now=NOW, pipeline_id=spec.pipeline_id)

    result = await execute(spec, ctx)

    data = result.node("data")
    assert data.status is NodeStatus.SUCCESS
    assert data.outputs["main"]["count"] == 2


async def test_market_data_without_any_universe_says_what_to_do():
    """조용히 0종목을 수집하면 배선이 끊긴 것을 아무도 모른다."""
    spec = PipelineSpec(
        pipeline_id="pipe_bare",
        name="배선 없음",
        nodes=[
            NodeSpec(
                id="data",
                type="marketData",
                params={"timeframe": "1d", "lookback": 60, "source": "synthetic"},
            )
        ],
        edges=[],
    )
    ctx = RunContext.create(now=NOW, pipeline_id=spec.pipeline_id)

    result = await execute(spec, ctx)

    data = result.node("data")
    assert data.status is NodeStatus.ERROR
    assert "symbolUniverse" in data.error


# ------------------------------------------------------- venue 목록 (혼합 파이프라인)
class FakeEquityProvider(MarketDataProvider):
    """거래대금을 주지 않는 소스. FDR의 미국 목록이 정확히 이 모양이다."""

    id = "fake_equity"
    display_name = "가짜 주식"
    venues = ("krx", "nasdaq", "nyse")
    credential_schema = None
    capabilities = ProviderCapabilities(timeframes=("1d",), provides_universe=True)

    #: krx만 거래대금이 있다. 미국은 None — 소스가 주지 않는 것이다.
    LISTING = {
        "krx": [("005930", 900.0), ("000660", 500.0), ("035720", 100.0)],
        "nasdaq": [("AAPL", None), ("MSFT", None), ("NVDA", None)],
        "nyse": [("KO", None), ("JPM", None), ("XOM", None)],
    }

    async def fetch_ohlcv(self, instrument, timeframe, end, limit):  # pragma: no cover
        raise NotImplementedError

    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        return [
            UniverseEntry(InstrumentRef.parse(f"{venue}:{symbol}"), volume)
            for symbol, volume in self.LISTING[venue]
        ]


def mixed_ctx() -> RunContext:
    registry = ProviderRegistry()
    registry.register(FakeUniverseProvider())
    registry.register(FakeEquityProvider())
    for venue in ("krx", "nasdaq", "nyse"):
        registry.set_route(venue, "*", ["fake_equity"])
    return RunContext.create(now=NOW, providers=registry, pipeline_id="pipe_t").bind("universe")


async def test_one_node_scans_several_markets():
    """★ 노드가 하나라 `context["universe"]` 덮어쓰기가 애초에 생기지 않는다 (§4.1)."""
    bundle = await run_node(
        {
            "venues": [
                {"venue": "krx", "top_by_turnover": 2},
                {"venue": "nasdaq", "limit": 2},
                {"venue": "nyse", "limit": 2},
            ]
        },
        mixed_ctx(),
    )

    keys = bundle.context[UNIVERSE_KEY]
    assert {k.split(":")[0] for k in keys} == {"krx", "nasdaq", "nyse"}
    # 컷이 venue마다 따로 걸렸다 — 섞어 잘랐다면 거래대금이 큰 쪽이 자리를 다 가져간다
    assert len([k for k in keys if k.startswith("krx:")]) == 2
    assert len([k for k in keys if k.startswith("nasdaq:")]) == 2
    assert len([k for k in keys if k.startswith("nyse:")]) == 2


async def test_meta_records_each_venue_separately():
    bundle = await run_node(
        {"venues": [{"venue": "krx", "top_by_turnover": 2}, {"venue": "nasdaq", "limit": 1}]},
        mixed_ctx(),
    )

    venues = bundle.context[UNIVERSE_META_KEY]["venues"]
    assert [v["venue"] for v in venues] == ["krx", "nasdaq"]
    assert [v["market"] for v in venues] == ["krx", "us"]
    assert [v["count"] for v in venues] == [2, 1]


async def test_turnover_cut_on_a_source_without_turnover_is_refused():
    """★ 경고만 남기고 빈 목록을 돌려주면 **그 시장이 통째로 사라진 채 실행이 성공한다.**"""
    with pytest.raises(NodeError, match="거래대금"):
        await run_node({"venues": [{"venue": "nasdaq", "top_by_turnover": 2}]}, mixed_ctx())


async def test_turnover_and_limit_together_is_refused():
    with pytest.raises(NodeError, match="함께"):
        await run_node(
            {"venues": [{"venue": "krx", "top_by_turnover": 2, "limit": 2}]}, mixed_ctx()
        )


async def test_singular_venue_still_works():
    """정의는 버전으로 보존되므로(4.7) 옛 표기를 못 읽으면 과거 실행의 이력이 끊긴다."""
    bundle = await run_node(
        {"venue": "nasdaq", "quote_currency": "KRW", "top_by_turnover": 2}, make_ctx()
    )

    keys = bundle.context[UNIVERSE_KEY]
    assert keys == ["nasdaq:BTC", "nasdaq:ETH"]
    assert bundle.context[UNIVERSE_META_KEY]["venues"][0]["venue"] == "nasdaq"


async def test_venue_and_venues_together_is_refused():
    with pytest.raises(ValueError, match="함께"):
        SymbolUniverseParams.model_validate(
            {"venue": "nasdaq", "venues": [{"venue": "krx"}]}
        )
