"""MA Filter — 이동평균 조건 필터.

**ohlcv를 보존한 채 items만 걸러낸다.** 이것이 필터를 몇 개든 이어붙일 수 있게
하는 규칙이다 (ARCHITECTURE.md 4.1). 판단 근거는 features/tags에 남긴다.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.schemas.pipeline import MAIN

Condition = Literal["cross_above", "cross_below", "above", "below"]


class MaFilterParams(BaseModel):
    period: int = Field(default=20, ge=1, le=1000)
    kind: Literal["sma", "ema"] = Field(default="sma", description="단순/지수 이동평균")
    condition: Condition = Field(
        default="cross_above",
        description="cross_above=골든크로스, cross_below=데드크로스, above/below=현재 위치",
    )
    source: Literal["close", "open", "high", "low"] = "close"


@register
class MaFilterNode(BaseNode):
    type = "maFilter"
    display_name = "MA Filter"
    category = "indicator"
    description = "이동평균선 돌파·위치 조건으로 종목을 걸러냅니다."
    ParamsModel = MaFilterParams
    inputs = (MAIN,)
    outputs = (MAIN,)

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: MaFilterParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        bundle = inputs.get(MAIN, Bundle.empty())
        feature_name = f"{params.kind}_{params.period}"
        kept: list[Item] = []

        for item in bundle:
            series = item.ohlcv[params.source]
            # 교차 판정에는 직전 봉이 필요하므로 period + 1개 이상이어야 한다
            if len(series) < params.period + 1:
                ctx.log.warning(
                    f"{item.instrument.key}: 봉이 부족합니다 "
                    f"({len(series)}개 < {params.period + 1}개 필요) — 건너뜁니다"
                )
                continue

            ma = _moving_average(series, params.period, params.kind)
            price_now, price_prev = float(series.iloc[-1]), float(series.iloc[-2])
            ma_now, ma_prev = float(ma.iloc[-1]), float(ma.iloc[-2])

            enriched = item.with_features(**{feature_name: ma_now, params.source: price_now})
            if _matches(params.condition, price_now, price_prev, ma_now, ma_prev):
                kept.append(
                    enriched.with_tags(
                        ma_signal=params.condition,
                        ma_gap_pct=round((price_now - ma_now) / ma_now * 100, 4) if ma_now else None,
                    )
                )

        ctx.log.info(
            f"MA 필터({params.kind}{params.period} {params.condition}): "
            f"{len(bundle)}개 중 {len(kept)}개 통과"
        )
        return {MAIN: bundle.replace_items(kept)}


def _moving_average(series: pd.Series, period: int, kind: str) -> pd.Series:
    if kind == "ema":
        return series.ewm(span=period, adjust=False).mean()
    return series.rolling(window=period).mean()


def _matches(
    condition: Condition,
    price_now: float,
    price_prev: float,
    ma_now: float,
    ma_prev: float,
) -> bool:
    if pd.isna(ma_now) or pd.isna(ma_prev):
        return False
    match condition:
        case "cross_above":
            return price_prev <= ma_prev and price_now > ma_now
        case "cross_below":
            return price_prev >= ma_prev and price_now < ma_now
        case "above":
            return price_now > ma_now
        case "below":
            return price_now < ma_now
    return False
