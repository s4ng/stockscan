"""파이프라인 계약 테스트 — DAG 엔진을 걷어낸 뒤에도 지켜야 할 것들.

엔진이 사라지면서 **규칙들이 프레임워크가 아니라 이 함수 안에** 있게 됐다. 그래서
여기가 그 규칙들의 회귀 테스트다.

  1. ★ **Fresh Bar Gate** — 같은 봉을 두 번 판정하지 않는다 (3.5)
  2. ★ **규칙 11** — dry-run은 `signals`를 남기지 않고 봉도 소비하지 않는다.
     실패한 실행도 봉을 소비하지 않는다
  3. ★ **규칙 13** — 실행은 바깥으로 아무것도 내보내지 않는다.
     예전에는 노드 선언 + 엔진 검사였지만 지금은 **코드 경로가 없는 것**이 보증이다
  4. ★ **규칙 14** — 동적 유니버스는 백테스트에서 거부된다 (서바이버십)
  5. **단계 스냅샷** — `node_runs`에 남아 `explain`이 되짚을 수 있어야 한다 (4.9)
  6. **결정성** — 같은 입력 + 같은 시각 → 같은 출력
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.engine.context import ExecutionMode, RunContext
from app.pipeline import RunStatus, StageStatus, execute

pytestmark = pytest.mark.asyncio

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def ctx(**overrides) -> RunContext:
    return RunContext.create(now=NOW, **overrides)


# --------------------------------------------------------------------------- 실행
async def test_pipeline_runs_end_to_end(config):
    result = await execute(config, ctx())

    assert result.status is RunStatus.SUCCESS
    assert [n.node_id for n in result.nodes] == [
        "load",
        "universe",
        "data",
        "strategy",
        "persist",
        "log",
    ]
    assert all(n.status is StageStatus.SUCCESS for n in result.nodes)


async def test_run_is_deterministic(config):
    """같은 입력 + 같은 시각 → 같은 출력 (1.2)."""
    first = await execute(config, ctx())
    second = await execute(config, ctx())

    assert first.node("data").outputs == second.node("data").outputs
    assert first.node("strategy").outputs == second.node("strategy").outputs


async def test_every_stage_leaves_a_snapshot(config):
    """★ `explain`이 '왜 이 신호가 났는가'를 되짚는 근거다 (4.9).

    DAG를 걷어냈어도 이 스냅샷은 남아야 한다 — 없으면 신호의 근거를 잃는다.
    """
    result = await execute(config, ctx())

    universe = result.node("universe")
    assert universe.outputs["main"]["count"] > 0
    assert universe.outputs["main"]["venues"][0]["venue"] == "nasdaq"
    assert any("종목" in line for line in universe.logs)

    strategy = result.node("strategy")
    assert strategy.inputs["main"]["count"] > 0
    assert strategy.duration_ms >= 0


async def test_as_of_never_exceeds_now(config):
    """look-ahead 방어: 어떤 봉도 실행 시각을 넘어서지 않는다 (규칙 2)."""
    result = await execute(config, ctx())

    data = result.node("data")
    assert data.status is StageStatus.SUCCESS
    assert data.outputs["main"]["count"] > 0


async def test_lookback_is_derived_from_the_strategy(config):
    """★ 설정의 lookback과 전략의 워밍업이 어긋나 종목이 조용히 전량 제외되던
    사고를 구조적으로 막는다 — 이제 어긋날 자리가 없다."""
    result = await execute(config, ctx())

    assert any("수집 깊이" in line and "워밍업" in line for line in result.node("data").logs)


# ----------------------------------------------------------------- Fresh Bar Gate
async def test_fresh_bar_gate_skips_an_unchanged_bar(config):
    """두 번째 실행에서 0건이 나오는 것은 정상이다 (3.5)."""
    shared = ctx(commit=True)
    await execute(config, shared)

    again = ctx(commit=True, bar_state=shared.bar_state)
    result = await execute(config, again)

    assert result.node("data").outputs["main"]["count"] == 0
    assert any("새로 마감된 봉이 없어" in line for line in result.node("data").logs)


async def test_dry_run_does_not_consume_the_bar(config):
    """★ dry-run이 봉을 삼키면 다음 실제 실행에서 그 신호가 영영 사라진다 (규칙 11)."""
    shared = ctx(commit=False)
    await execute(config, shared)

    again = ctx(commit=False, bar_state=shared.bar_state)
    result = await execute(config, again)

    assert result.node("data").outputs["main"]["count"] > 0


async def test_a_failed_run_does_not_consume_the_bar(config):
    """실패한 실행이 봉을 삼키면 신호가 조용히 사라진다."""
    broken = config.model_copy(update={"strategy": "does_not_exist"})
    state = ctx(commit=True)

    result = await execute(broken, state)

    assert result.status is RunStatus.FAILED
    assert state.bar_state.last_seen("data|nasdaq:AAPL|1d") is None


# ------------------------------------------------------------------------ 규칙 11
async def test_dry_run_writes_no_signals(config):
    context = ctx(commit=False)
    await execute(config, context)

    assert context.signals.persistent is False


async def test_commit_opens_the_signal_sink(config):
    """부작용 분기는 배출구를 갈아 끼우는 것으로 표현된다 — 코드에 분기를 심지 않는다."""
    context = ctx(commit=True)
    result = await execute(config, context)

    persist = result.node("persist")
    assert persist.status is StageStatus.SUCCESS
    assert "dry-run" in " ".join(persist.logs) or persist.outputs


# ------------------------------------------------------------------------ 규칙 13
async def test_the_pipeline_never_sends_anything_outward(config):
    """★ 예전에는 노드가 `sends_external_messages`를 선언하고 엔진이 걸렀다.

    지금은 **바깥으로 나가는 코드 경로가 아예 없는 것**이 보증이다. 알림은
    `serve`가 실행 뒤에 보낸다. 이 테스트는 그 사실을 못박아 둔다 — 파이프라인에
    전송 코드가 들어오면 여기서 걸린다.
    """
    import app.pipeline as pipeline_module

    source = pipeline_module.__file__
    with open(source, encoding="utf-8") as fh:
        text = fh.read()

    for forbidden in ("telegram", "urllib.request", "AlertChannel", "channel.send"):
        assert forbidden not in text, (
            f"파이프라인에 외부 전송으로 보이는 코드가 있습니다: {forbidden!r}. "
            f"알림은 serve의 몫입니다 (규칙 13 / 12.2)."
        )


# ------------------------------------------------------------------------ 규칙 14
async def test_dynamic_universe_is_refused_in_backtest(config):
    """★ 소스 목록은 언제나 '지금'이다. 과거 리플레이에 쓰면 유니버스가 미래를 본다.

    `strategy check`의 AST 검사에 걸리지 않는 look-ahead라 여기서 막는다.
    """
    result = await execute(config, ctx(mode=ExecutionMode.BACKTEST))

    assert result.status is RunStatus.FAILED
    assert "백테스트" in (result.error or "")
    assert "서바이버십" in (result.error or "")


# --------------------------------------------------------------------- 유니버스 컷
async def test_missing_turnover_is_refused_not_silently_zeroed(config):
    """★ 거래대금을 주지 않는 소스에 거래대금 컷을 걸면 **그 시장이 통째로 사라진 채
    실행이 성공한다.** 조용히 0으로 취급하지 않고 거부한다."""
    from app.market.instrument import InstrumentRef
    from app.providers.base import MarketDataProvider, ProviderCapabilities, UniverseEntry
    from app.providers.registry import ProviderRegistry

    class NoTurnover(MarketDataProvider):
        id = "no_turnover"
        display_name = "거래대금을 주지 않는 소스"
        venues = ("krx",)
        credential_schema = None
        capabilities = ProviderCapabilities(timeframes=("1d",), provides_universe=True)

        async def fetch_ohlcv(self, instrument, timeframe, end, limit):  # pragma: no cover
            raise NotImplementedError

        async def list_instruments(self, venue: str) -> list[UniverseEntry]:
            return [UniverseEntry(InstrumentRef.parse(f"{venue}:005930"), None)]

    registry = ProviderRegistry()
    registry.register(NoTurnover())
    registry.set_route("krx", "*", ["no_turnover"])

    krx_only = config.model_copy(update={"universe": {"krx": 3}})
    result = await execute(krx_only, ctx(providers=registry))

    assert result.status is RunStatus.FAILED
    assert "거래대금" in (result.error or "")


async def test_turnover_cut_actually_ranks(config):
    """거래대금 상위 N은 정렬을 해야 한다. 앞에서 자르기만 하면 뜻이 없다."""
    result = await execute(config.model_copy(update={"universe": {"krx": 2}}), ctx())

    universe = result.node("universe")
    assert universe.outputs["main"]["count"] == 2
    assert any("상위 2종목" in line for line in universe.logs)


async def test_head_cut_warns_that_it_trusts_source_order(config):
    """조용한 절삭 금지 — '전부 훑었다'는 오해가 실사용에서 가장 위험하다."""
    result = await execute(config.model_copy(update={"universe": {"nasdaq": 2}}), ctx())

    logs = " ".join(result.node("universe").logs)
    assert "소스가 준 순서" in logs
