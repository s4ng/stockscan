"""landry_pullback 계약 테스트.

**이 전략에서 조용히 틀리기 쉬운 곳은 봉을 세는 자리다.** 오늘 봉은 조정이 아니라
재출발을 판정하는 봉인데, 이걸 조정에 포함하면 피벗이 한 봉 밀린다. 밀려도 신호는
계속 나오고 목록은 여전히 그럴듯해서 눈으로는 구분되지 않는다.

두 번째로 위험한 곳은 **종가 대신 고가로 재출발을 판정하는 것**이다. 랜드리의 실제
체결에는 그쪽이 가까운데, 이 시스템에서는 `evaluate`가 신호 봉의 종가를 기준가로
잡기 때문에 되돌려 마감한 날의 사후 수익률이 **실제보다 좋게** 나온다. 전략 파일의
docstring이 (B)로 적어 둔 그 선택지이고, 여기서 테스트로 막는다.
"""

from __future__ import annotations

import math
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

BARS = 200  # startup_candles(150)보다 넉넉히


@pytest.fixture
def strategy():
    return load_strategy("landry_pullback").strategy


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


def pattern(
    *, pullback: int = 3, resume: float = 2.0, end: float = 300.0, bars: int = BARS
) -> tuple[list[float], list[float], list[float]]:
    """상승 추세 → 조정 `pullback`봉 → 오늘 재출발. (closes, highs, lows)

    기본값(end=300, pullback=3)이면 뒤쪽 봉이 이렇게 된다. 아래 테스트의 상수는
    전부 이 표에서 온다.

        봉      -5(고점)  -4    -3    -2(피벗)  -1(오늘)
        close   300      299   298   297      300
        high    301      300   299   298      301
        low     299      298   297   296      299
    """
    rise = bars - pullback - 1
    closes = ramp(rise, 100.0, end)

    # 조정 — 고가가 한 봉씩 낮아진다 (lower high).
    closes += [end - i for i in range(1, pullback + 1)]

    # 오늘 — 직전 봉의 고가(피벗)를 **종가로** 넘어선다.
    pivot = closes[-1] + 1.0
    closes.append(pivot + resume)

    return closes, [c + 1.0 for c in closes], [c - 1.0 for c in closes]


def features(strategy, ctx: RunContext, symbol: str = "nasdaq:ABC", **kwargs):
    closes, highs, lows = pattern(**kwargs)
    return strategy.compute(
        make_item(symbol, closes, highs, lows), strategy.Params(), ctx
    ).features


# ------------------------------------------------------------------------ 봉 세기
def test_today_is_not_counted_as_part_of_the_pullback(strategy, ctx: RunContext):
    """★ 오늘 봉을 조정에 포함하면 여기가 깨진다.

    포함하면 조정이 4봉으로 세어지고 고점이 한 봉 앞으로 밀린다. 그래도 게이트는
    통과할 수 있어서 결과만 봐서는 알 수 없다.
    """
    f = features(strategy, ctx, pullback=3)

    assert f["pullback_bars"] == 3
    assert f["peak_high"] == pytest.approx(301.0)


def test_pivot_is_the_previous_bar_high(strategy, ctx: RunContext):
    """피벗은 **직전 봉**의 고가다. 오늘 고가가 아무리 높아도 피벗은 움직이지 않는다."""
    closes, highs, lows = pattern(pullback=3)
    highs[-1] = 500.0

    f = strategy.compute(
        make_item("nasdaq:ABC", closes, highs, lows), strategy.Params(), ctx
    ).features

    assert f["pivot"] == pytest.approx(298.0)
    assert f["resumed"] is True


# ------------------------------------------------------------------------ 재출발
def test_intraday_break_that_closes_below_the_pivot_is_not_a_signal(strategy, ctx: RunContext):
    """★★ (B)를 막는 테스트 — 이 파일에서 가장 중요하다.

    장중에 320까지 뚫었다가 피벗(298) 아래인 297로 마감한 봉이다. 고가로 판정하면
    신호가 되는데, 그러면 실제 체결가는 298인데 `evaluate`의 기준가는 297이 되어
    사후 수익률이 **실제보다 좋게** 나온다. 성적표가 자신감 기계가 되는 경로다.
    """
    closes, highs, lows = pattern(pullback=3)
    closes[-1] = 297.0
    highs[-1] = 320.0

    f = strategy.compute(
        make_item("nasdaq:ABC", closes, highs, lows), strategy.Params(), ctx
    ).features

    assert f["pivot"] == pytest.approx(298.0)
    assert f["resumed"] is False
    assert f["entry_ready"] is False


def test_entry_slippage_records_how_late_we_bought(strategy, ctx: RunContext):
    """랜드리는 피벗에서 체결되지만 이 시스템은 종가에 산다. 그 차이가 보여야 한다."""
    f = features(strategy, ctx)

    assert f["entry_slippage_pct"] == pytest.approx(300.0 / 298.0 - 1)


# ------------------------------------------------------------------------ 게이트
def test_a_finished_pullback_passes_every_gate(strategy, ctx: RunContext):
    f = features(strategy, ctx)

    assert f["trend_ok"] is True
    assert f["peak_is_high"] is True
    assert f["pullback_ok"] is True
    assert f["resumed"] is True
    assert f["entry_ready"] is True


def test_pullback_longer_than_the_limit_is_a_broken_trend(strategy, ctx: RunContext):
    """6봉을 넘어가면 조정이 아니라 추세가 꺾인 것이다.

    한도를 1 넘긴 값을 그대로 돌려주므로 "너무 길다"와 "딱 한도다"가 구분된다.
    """
    f = features(strategy, ctx, pullback=6)

    assert f["pullback_bars"] == 6
    assert f["pullback_ok"] is False
    assert f["entry_ready"] is False


def test_single_down_bar_is_not_a_pullback(strategy, ctx: RunContext):
    """하루 쉰 것은 조정이 아니다 (랜드리의 1-2-3은 최소 2봉)."""
    f = features(strategy, ctx, pullback=1)

    assert f["pullback_bars"] == 1
    assert f["pullback_ok"] is False
    assert f["entry_ready"] is False


def test_peak_must_be_the_recent_high(strategy, ctx: RunContext):
    """조정은 **고점에서** 시작해야 한다.

    9봉 전에 더 높은 고가가 있으면 이건 고점에서의 조정이 아니라 이미 한 번 꺾인
    뒤의 반등이다. 조정 봉 수는 그대로여서 게이트 3만으로는 걸러지지 않는다.
    """
    closes, highs, lows = pattern(pullback=3)
    highs[-9] = 500.0

    f = strategy.compute(
        make_item("nasdaq:ABC", closes, highs, lows), strategy.Params(), ctx
    ).features

    assert f["pullback_bars"] == 3
    assert f["peak_is_high"] is False
    assert f["entry_ready"] is False


def test_downtrend_fails_the_trend_qualifier(strategy, ctx: RunContext):
    """모양이 같아도 추세가 아니면 후보가 아니다 — 떨어지는 칼을 받지 않는다."""
    closes, highs, lows = pattern(pullback=3, end=300.0)
    closes = [400.0 - c for c in closes]  # 같은 구조를 위아래로 뒤집는다
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    f = strategy.compute(
        make_item("nasdaq:DOWN", closes, highs, lows), strategy.Params(), ctx
    ).features

    assert f["trend_ok"] is False
    assert f["entry_ready"] is False


# ------------------------------------------------------------------------ 리스크
def test_stop_is_the_low_of_the_pullback(strategy, ctx: RunContext):
    """랜드리의 손절은 조정 구간의 최저 저가다. 손실 폭이 함께 나와야 크기를 정한다."""
    f = features(strategy, ctx)

    assert f["stop_swing"] == pytest.approx(296.0)
    assert f["risk_pct"] == pytest.approx((300.0 - 296.0) / 300.0)


def test_pullback_depth_is_reported_but_not_gated(strategy, ctx: RunContext):
    """깊이는 남기기만 한다 — 랜드리가 수치를 주지 않았고, 내가 정하면 내 파라미터가 된다."""
    f = features(strategy, ctx)

    assert f["pullback_depth_pct"] == pytest.approx((301.0 - 296.0) / 301.0)
    assert f["entry_ready"] is True


def test_atr_converges_to_a_constant_true_range(strategy, ctx: RunContext):
    """Wilder 평활은 지수가중이라 창 길이만큼만 보는 게 아니다."""
    closes = [100.0] * BARS
    highs = [105.0] * BARS
    lows = [95.0] * BARS

    f = strategy.compute(
        make_item("nasdaq:FLAT", closes, highs, lows), strategy.Params(), ctx
    ).features

    assert f["atr"] == pytest.approx(10.0)


# ------------------------------------------------------------------------ 랭킹
def test_ranking_prefers_the_stronger_trend(strategy, ctx: RunContext):
    """랜드리가 ADX를 쓰는 이유 — 재출발 모양이 같아도 오는 길이 곧았던 쪽이 위다."""
    params = strategy.Params()
    closes, highs, lows = pattern()
    steady = strategy.compute(make_item("nasdaq:STEADY", closes, highs, lows), params, ctx)

    # ⚠️ 뒤 8봉(고점·조정·오늘)은 건드리지 않는다 — 건드리면 비교가 ADX가 아니라
    #    조정 모양의 차이가 된다. 아래로만 흔드는 것은 고점이 여전히 최고가여야 해서다.
    choppy = list(closes)
    for i in range(len(closes) - 60, len(closes) - 8, 2):
        choppy[i] *= 0.97
    wild = strategy.compute(
        make_item(
            "nasdaq:CHOPPY", choppy, [c + 1.0 for c in choppy], [c - 1.0 for c in choppy]
        ),
        params,
        ctx,
    )

    assert steady.features["adx"] > wild.features["adx"]

    ranked = strategy.rank(Bundle([wild, steady]), params, ctx)
    assert [i.instrument.symbol for i in ranked] == ["STEADY", "CHOPPY"]


def test_undefined_adx_is_not_zero(strategy, ctx: RunContext):
    """움직이지 않은 종목의 ADX는 **잴 수 없는 것**이지 0이 아니다.

    0으로 채우면 "추세가 약하다"가 되어 랭킹 맨 아래에 조용히 줄을 선다. NaN이어야
    rank_by가 경고와 함께 제외한다 — 없는 숫자를 지어내지 않는다.
    """
    params = strategy.Params()
    flat = strategy.compute(make_item("nasdaq:FLAT", [100.0] * BARS), params, ctx)

    assert math.isnan(flat.features["adx"])

    ranked = strategy.rank(Bundle([flat]), params, ctx)

    assert len(ranked) == 0
    assert any("랭킹에서 제외" in r.message for r in ctx.log.records)


# ------------------------------------------------------------------------ 컷
def test_select_keeps_only_ready_items_and_says_why(strategy, ctx: RunContext):
    params = strategy.Params()
    up_c, up_h, up_l = pattern()
    down_c = [400.0 - c for c in up_c]
    items = [
        strategy.compute(make_item("nasdaq:UP", up_c, up_h, up_l), params, ctx),
        strategy.compute(
            make_item(
                "nasdaq:DOWN", down_c, [c + 1.0 for c in down_c], [c - 1.0 for c in down_c]
            ),
            params,
            ctx,
        ),
    ]
    ranked = strategy.rank(Bundle(items), params, ctx)

    selected = strategy.select(ranked, params, ctx)

    assert [i.instrument.symbol for i in selected] == ["UP"]
    assert any("게이트 통과 현황" in r.message for r in ctx.log.records)


def test_no_candidate_is_a_verdict_not_a_failure(strategy, ctx: RunContext):
    """0건은 정상 출력이다. 여기서 예외가 나면 파이프라인이 실패로 끝난다."""
    params = strategy.Params()
    closes, _, _ = pattern()
    closes = [400.0 - c for c in closes]
    items = [
        strategy.compute(
            make_item("nasdaq:DOWN", closes, [c + 1.0 for c in closes], [c - 1.0 for c in closes]),
            params,
            ctx,
        )
    ]

    selected = strategy.select(strategy.rank(Bundle(items), params, ctx), params, ctx)

    assert len(selected) == 0


def test_top_n_caps_the_list_per_market(strategy, ctx: RunContext):
    params = strategy.Params(top_n=3)
    items = []
    for i in range(1, 8):
        closes, highs, lows = pattern(end=200.0 + i * 20)
        items.append(
            strategy.compute(make_item(f"nasdaq:C{i}", closes, highs, lows), params, ctx)
        )
    ranked = strategy.rank(Bundle(items), params, ctx)

    selected = strategy.select(ranked, params, ctx)

    assert len(selected) == 3
    assert selected.items[0].features["rank"] == 1


# ------------------------------------------------------------------------ 선언
def test_insufficient_bars_yields_no_score(strategy, ctx: RunContext):
    result = strategy.compute(
        make_item("nasdaq:NEW", ramp(20, 100.0, 300.0)), strategy.Params(), ctx
    )

    assert "adx" not in result.features
    assert result.features["insufficient_bars"] == 20


def test_startup_candles_covers_every_window(strategy):
    p = strategy.Params()

    assert strategy.startup_candles >= p.swing_high + p.max_pullback + 2
    assert strategy.startup_candles >= p.trend_slow * 5


def test_parameters_are_the_published_standard(strategy):
    """백테스트로 고른 값이 아니라는 사실을 테스트로 박아 둔다 (4.8)."""
    d = strategy.Params()

    assert (d.trend_fast, d.trend_mid, d.trend_slow) == (10, 20, 30)
    assert (d.min_pullback, d.max_pullback) == (2, 5)
    assert (d.wilder, d.stop_atr) == (14, 2.0)
