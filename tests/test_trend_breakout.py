"""trend_breakout_55 계약 테스트.

**이 전략에서 조용히 틀리기 쉬운 곳은 돌파 채널의 `shift(1)`이다.** 오늘 봉을
창에서 빼지 않으면 오늘 고가가 자기 자신과 비교돼 돌파 판정이 통째로 무너지는데,
신호가 아주 안 나오는 것도 아니어서 눈으로는 구분되지 않는다. 그래서 "오늘 고가가
채널에 영향을 주지 않는가"를 직접 겨눈다.

두 번째로 위험한 곳은 게이트다. 셋 중 하나라도 빠지면 추세추종이 아니게 되는데
결과 목록은 여전히 그럴듯해 보인다.
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

BARS = 320  # startup_candles(253)보다 넉넉히


@pytest.fixture
def strategy():
    return load_strategy("trend_breakout_55").strategy


@pytest.fixture
def ctx() -> RunContext:
    return RunContext.create(now=NOW)


def make_item(
    symbol: str,
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> Item:
    index = pd.date_range(end=NOW, periods=len(closes), freq="D", tz="UTC", name="time")
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": highs if highs is not None else closes,
            "low": lows if lows is not None else closes,
            "close": closes,
            "volume": 1.0,
        },
        index=index,
    )
    return Item(instrument=InstrumentRef.parse(symbol), timeframe="1d", as_of=NOW, ohlcv=frame)


def ramp(total: int, start: float, end: float) -> list[float]:
    step = (end - start) / (total - 1)
    return [start + step * i for i in range(total)]


# ------------------------------------------------------------------------ 돌파
def test_channel_excludes_the_judged_bar(strategy, ctx: RunContext):
    """★ shift(1)이 빠지면 이 테스트가 깨진다.

    오늘 고가(120)가 채널에 들어가면 채널 상단이 120이 되어 종가 110이 돌파가
    아니게 된다. 오늘을 빼면 상단은 직전 55봉의 100이고 돌파가 맞다.
    """
    closes = [100.0] * (BARS - 1) + [110.0]
    highs = [100.0] * (BARS - 1) + [120.0]

    result = strategy.compute(
        make_item("upbit:KRW-BTC", closes, highs=highs), strategy.Params(), ctx
    )

    assert result.features["channel_high"] == pytest.approx(100.0)
    assert result.features["breakout"] is True


def test_close_below_the_channel_is_not_a_breakout(strategy, ctx: RunContext):
    """장중에 뚫었다가 되돌려 마감한 봉은 돌파가 아니다 (마감 봉으로만 판단한다)."""
    closes = [100.0] * (BARS - 1) + [99.0]
    highs = [100.0] * (BARS - 1) + [130.0]

    result = strategy.compute(
        make_item("upbit:KRW-BTC", closes, highs=highs), strategy.Params(), ctx
    )

    assert result.features["breakout"] is False
    assert result.features["entry_ready"] is False


def test_breakout_margin_is_measured_against_the_channel(strategy, ctx: RunContext):
    closes = [100.0] * (BARS - 1) + [110.0]

    result = strategy.compute(make_item("upbit:KRW-BTC", closes), strategy.Params(), ctx)

    assert result.features["breakout_margin_pct"] == pytest.approx(0.10)


# ------------------------------------------------------------------------ 게이트
def test_uptrend_breakout_passes_every_gate(strategy, ctx: RunContext):
    result = strategy.compute(
        make_item("upbit:KRW-BTC", ramp(BARS, 100.0, 400.0)), strategy.Params(), ctx
    )

    assert result.features["trend_ok"] is True
    assert result.features["tsmom_12m"] > 0
    assert result.features["breakout"] is True
    assert result.features["entry_ready"] is True


def test_breakout_below_the_trend_ma_is_rejected(strategy, ctx: RunContext):
    """오래 눌린 종목의 반등은 돌파여도 후보가 아니다 — 추세 방향이 아직 아래다."""
    closes = [300.0] * 200 + [100.0] * (BARS - 201) + [105.0]

    result = strategy.compute(make_item("upbit:KRW-BTC", closes), strategy.Params(), ctx)

    assert result.features["breakout"] is True
    assert result.features["trend_ok"] is False
    assert result.features["entry_ready"] is False


def test_breakout_without_absolute_momentum_is_rejected(strategy, ctx: RunContext):
    """1년 전보다 아래인 종목의 신고가는 반등이지 추세가 아니다 (MOP 2012)."""
    closes = [*ramp(250, 200.0, 100.0), *ramp(BARS - 250, 100.0, 150.0)]

    result = strategy.compute(make_item("upbit:KRW-BTC", closes), strategy.Params(), ctx)

    assert result.features["breakout"] is True
    assert result.features["trend_ok"] is True
    assert result.features["tsmom_12m"] < 0
    assert result.features["entry_ready"] is False


def test_select_keeps_only_ready_items_and_says_why(strategy, ctx: RunContext):
    params = strategy.Params()
    items = [
        strategy.compute(make_item("upbit:KRW-UP", ramp(BARS, 100.0, 400.0)), params, ctx),
        strategy.compute(make_item("upbit:KRW-DOWN", ramp(BARS, 400.0, 100.0)), params, ctx),
    ]
    ranked = strategy.rank(Bundle(items), params, ctx)

    selected = strategy.select(ranked, params, ctx)

    assert [i.instrument.symbol for i in selected] == ["KRW-UP"]
    assert any("게이트 통과 현황" in r.message for r in ctx.log.records)


def test_no_candidate_is_a_verdict_not_a_failure(strategy, ctx: RunContext):
    """0건은 정상 출력이다. 여기서 예외가 나면 파이프라인이 실패로 끝난다."""
    params = strategy.Params()
    items = [strategy.compute(make_item("upbit:KRW-DOWN", ramp(BARS, 400.0, 100.0)), params, ctx)]

    selected = strategy.select(strategy.rank(Bundle(items), params, ctx), params, ctx)

    assert len(selected) == 0


# ------------------------------------------------------------------------ 랭킹
def test_ranking_prefers_the_steadier_trend(strategy, ctx: RunContext):
    """수익률이 같으면 덜 흔들린 쪽이 위다 — 변동성으로 나누는 이유다.

    나누지 않으면 점수가 같아져 순서가 입력 순서로 결정된다.
    """
    params = strategy.Params()
    smooth = ramp(BARS, 100.0, 300.0)
    # 시작·끝과 모멘텀 기준점은 그대로 두고 중간만 흔든다 → tsmom은 같고 변동성만 다르다.
    jumpy = list(smooth)
    for i in range(1, BARS - 1):
        if i % 2 == 0 and i != BARS - 1 - params.momentum:
            jumpy[i] *= 1.15

    calm = strategy.compute(make_item("upbit:KRW-CALM", smooth), params, ctx)
    wild = strategy.compute(make_item("upbit:KRW-WILD", jumpy), params, ctx)

    assert calm.features["tsmom_12m"] == pytest.approx(wild.features["tsmom_12m"])
    assert calm.features["annual_vol"] < wild.features["annual_vol"]

    ranked = strategy.rank(Bundle([wild, calm]), params, ctx)
    assert [i.instrument.symbol for i in ranked] == ["KRW-CALM", "KRW-WILD"]


def test_top_n_caps_the_list_per_market(strategy, ctx: RunContext):
    params = strategy.Params(top_n=3)
    items = [
        strategy.compute(
            make_item(f"upbit:KRW-C{i}", ramp(BARS, 100.0, 200.0 + i * 20)), params, ctx
        )
        for i in range(1, 8)
    ]
    ranked = strategy.rank(Bundle(items), params, ctx)

    selected = strategy.select(ranked, params, ctx)

    assert len(selected) == 3
    assert selected.items[0].features["rank"] == 1


# ------------------------------------------------------------------------ 리스크
def test_atr_converges_to_a_constant_true_range(strategy, ctx: RunContext):
    """Wilder 평활은 지수가중이라 창 길이만큼만 보는 게 아니다. 상수 구간에서 값이 맞는지 본다."""
    closes = [100.0] * BARS
    highs = [105.0] * BARS
    lows = [95.0] * BARS

    result = strategy.compute(
        make_item("upbit:KRW-BTC", closes, highs=highs, lows=lows), strategy.Params(), ctx
    )

    assert result.features["atr"] == pytest.approx(10.0)


def test_stop_is_two_atr_below_the_close(strategy, ctx: RunContext):
    """손절 가격과 손실 폭이 후보와 함께 나와야 포지션 크기를 정할 수 있다."""
    params = strategy.Params()
    closes = ramp(BARS, 100.0, 400.0)
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]

    f = strategy.compute(
        make_item("upbit:KRW-BTC", closes, highs=highs, lows=lows), params, ctx
    ).features

    assert f["stop_2n"] == pytest.approx(f["close"] - params.stop_atr * f["atr"])
    assert f["risk_pct"] == pytest.approx(params.stop_atr * f["atr"] / f["close"])
    assert f["exit_low_20"] < f["close"]


# ------------------------------------------------------------------------ 봉 부족
def test_insufficient_bars_yields_no_score(strategy, ctx: RunContext):
    """신규 상장 종목이 짧은 이력으로 '사상 최고가 돌파'를 만들어 내면 안 된다."""
    result = strategy.compute(
        make_item("upbit:KRW-NEW", ramp(60, 100.0, 500.0)), strategy.Params(), ctx
    )

    assert "trend_strength" not in result.features
    assert result.features["insufficient_bars"] == 60


def test_startup_candles_covers_every_window(strategy):
    p = strategy.Params()

    assert strategy.startup_candles >= max(p.momentum, p.trend_ma, p.breakout) + 1


def test_parameters_are_the_published_standard(strategy):
    """백테스트로 고른 값이 아니라는 사실을 테스트로 박아 둔다 (4.8)."""
    d = strategy.Params()

    assert (d.trend_ma, d.momentum, d.breakout) == (200, 252, 55)
    assert (d.exit_channel, d.atr, d.stop_atr) == (20, 20, 2.0)
