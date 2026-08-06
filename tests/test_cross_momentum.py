"""cross_momentum_12_1 계약 테스트.

**이 전략에서 조용히 틀리기 쉬운 곳은 '-1'(스킵)이다.** 스킵이 빠지면 그냥
12개월 모멘텀이 되는데, 결과가 그럴듯해 보여서 눈으로는 구분되지 않는다.
그래서 "최근 구간이 점수에 영향을 주지 않는가"를 직접 겨눈다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.market.instrument import InstrumentRef
from app.strategies.registry import load_strategy

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 8, 2, tzinfo=UTC)


@pytest.fixture
def strategy():
    return load_strategy("cross_momentum_12_1").strategy


@pytest.fixture
def ctx() -> RunContext:
    return RunContext.create(now=NOW)


def make_item(symbol: str, closes: list[float]) -> Item:
    index = pd.date_range(end=NOW, periods=len(closes), freq="D", tz="UTC", name="time")
    frame = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0},
        index=index,
    )
    return Item(
        instrument=InstrumentRef.parse(symbol), timeframe="1d", as_of=NOW, ohlcv=frame
    )


def ramp(total: int, start: float, end: float) -> list[float]:
    step = (end - start) / (total - 1)
    return [start + step * i for i in range(total)]


# ----------------------------------------------------------------------- 스킵
def test_momentum_measures_the_window_before_the_skip(strategy, ctx: RunContext):
    """P[t-21] / P[t-273] - 1 이어야 한다."""
    params = strategy.Params(lookback=252, skip=21)
    closes = ramp(300, 100.0, 400.0)
    item = make_item("nasdaq:BTC", closes)

    result = strategy.compute(item, params, ctx)

    expected = closes[-1 - 21] / closes[-1 - 21 - 252] - 1
    assert result.features["momentum_12_1"] == pytest.approx(expected)


def test_recent_month_does_not_move_the_score(strategy, ctx: RunContext):
    """★ 스킵이 빠지면 이 테스트가 깨진다.

    최근 21봉을 통째로 다르게 만들어도 점수는 같아야 한다. 단기 반전 구간을
    점수에서 빼는 것이 12-1의 정의 그 자체다.
    """
    params = strategy.Params(lookback=252, skip=21)
    base = ramp(300, 100.0, 400.0)
    spiked = [*base[:-21], *[v * 3 for v in base[-21:]]]

    calm = strategy.compute(make_item("nasdaq:BTC", base), params, ctx)
    spike = strategy.compute(make_item("nasdaq:BTC", spiked), params, ctx)

    assert calm.features["momentum_12_1"] == pytest.approx(spike.features["momentum_12_1"])
    # 다만 건너뛴 구간의 수익률은 해석용으로 남는다 — explain에서 보여야 한다
    assert spike.features["skipped_return"] > calm.features["skipped_return"]


def test_skip_zero_includes_the_most_recent_bar(strategy, ctx: RunContext):
    params = strategy.Params(lookback=10, skip=0)
    closes = ramp(30, 100.0, 200.0)

    result = strategy.compute(make_item("nasdaq:BTC", closes), params, ctx)

    assert result.features["momentum_12_1"] == pytest.approx(closes[-1] / closes[-11] - 1)


# ------------------------------------------------------------------- 봉 부족
def test_insufficient_bars_yields_no_score(strategy, ctx: RunContext):
    """신규 상장 종목이 짧은 이력으로 극단적 모멘텀을 내면 안 된다."""
    params = strategy.Params()
    item = make_item("nasdaq:NEW", ramp(50, 100.0, 500.0))

    result = strategy.compute(item, params, ctx)

    assert "momentum_12_1" not in result.features
    assert result.features["insufficient_bars"] == 50


def test_scoreless_items_are_dropped_from_the_ranking_with_a_warning(strategy, ctx: RunContext):
    params = strategy.Params()
    items = [
        strategy.compute(make_item("nasdaq:BTC", ramp(300, 100.0, 400.0)), params, ctx),
        strategy.compute(make_item("nasdaq:NEW", ramp(50, 100.0, 500.0)), params, ctx),
    ]

    ranked = strategy.rank(Bundle(items), params, ctx)

    assert [i.instrument.symbol for i in ranked] == ["BTC"]
    assert any("제외" in r.message for r in ctx.log.records)


def test_startup_candles_covers_the_whole_window(strategy):
    """Strategy Runner가 이 값으로 종목을 거른다. 모자라면 compute가 헛돈다."""
    params = strategy.Params()

    assert strategy.startup_candles >= params.lookback + params.skip + 1


# ------------------------------------------------------------------ 횡단면
def test_ranking_puts_the_strongest_first(strategy, ctx: RunContext):
    params = strategy.Params()
    items = [
        strategy.compute(make_item("nasdaq:SLOW", ramp(300, 100.0, 120.0)), params, ctx),
        strategy.compute(make_item("nasdaq:FAST", ramp(300, 100.0, 900.0)), params, ctx),
        strategy.compute(make_item("nasdaq:FLAT", [100.0] * 300), params, ctx),
    ]

    ranked = strategy.rank(Bundle(items), params, ctx)

    assert [i.instrument.symbol for i in ranked] == ["FAST", "SLOW", "FLAT"]
    assert ranked.items[0].features["rank"] == 1
    assert ranked.items[0].features["universe_size"] == 3


def test_select_keeps_the_top_slice(strategy, ctx: RunContext):
    params = strategy.Params(top_pct=0.34)
    items = [
        strategy.compute(
            make_item(f"nasdaq:C{i}", ramp(300, 100.0, 100.0 + i * 50)), params, ctx
        )
        for i in range(1, 7)
    ]
    ranked = strategy.rank(Bundle(items), params, ctx)

    selected = strategy.select(ranked, params, ctx)

    assert len(selected) == 2
    assert selected.items[0].instrument.symbol == "C6"


def test_parameters_are_the_published_standard(strategy):
    """백테스트로 고른 값이 아니라는 사실을 테스트로 박아 둔다 (4.8)."""
    defaults = strategy.Params()

    assert (defaults.lookback, defaults.skip) == (252, 21)
