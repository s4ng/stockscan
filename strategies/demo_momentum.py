"""demo_momentum — 배선 확인용 더미 전략.

⚠️ **이것으로 매매 판단을 하지 마세요.** 이 파일의 목적은 하나입니다 —
`compute`(시계열) → `rank`(횡단면) → `select`(컷)가 실제로 순서대로 도는지,
그리고 `rank`가 정말 쓰이는지 확인하는 것입니다.

진짜 첫 전략(횡단면 모멘텀 12-1)은 Phase 1에서 들어옵니다. 그때는 파라미터를
백테스트로 뒤져서 고르지 않고 **검증된 팩터의 표준값**을 씁니다 (4.8).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.strategies import Strategy, top_pct


class DemoMomentumStrategy(Strategy):
    id = "demo_momentum"
    display_name = "데모 모멘텀 (배선 확인용)"
    timeframe = "1d"
    startup_candles = 60
    score_feature = "momentum"

    class Params(BaseModel):
        lookback: int = Field(default=20, ge=2, le=500, description="모멘텀 계산 기간(봉)")
        top_pct: float = Field(default=0.5, gt=0, le=1, description="상위 비율만 통과")

    def compute(self, item: Item, p: Params, ctx: RunContext) -> Item:
        close = item.ohlcv["close"]
        # rolling · iloc[-n]은 과거만 본다. shift(음수)를 쓰면 여기서 미래가 샌다.
        momentum = float(close.iloc[-1] / close.iloc[-p.lookback] - 1)
        volatility = float(close.pct_change().rolling(p.lookback).std().iloc[-1])
        return item.with_features(
            momentum=momentum,
            volatility=volatility,
            close=float(close.iloc[-1]),
        )

    def select(self, bundle: Bundle, p: Params, ctx: RunContext) -> Bundle:
        return top_pct(bundle, p.top_pct, ctx)
