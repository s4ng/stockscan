"""Market Data Fetcher — 시세 수집 진입점.

두 가지 안전장치를 담고 있다.
  - **closed_only**: 미완성 봉을 잘라내 신호가 생겼다 사라지는 현상을 막는다 (4.4)
  - **skip_stale**:  직전 실행과 같은 봉이면 제외한다. 코인+주식 혼합 파이프라인에서
                     장 마감 중인 종목이 같은 신호를 매번 재발생시키는 것을 막는다 (3.5)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.state import bar_key
from app.engine.types import Bundle, Item, validate_ohlcv
from app.market.instrument import InstrumentRef
from app.market.timeframe import normalize
from app.nodes.base import BaseNode
from app.nodes.registry import register
from app.providers.registry import AUTO
from app.schemas.pipeline import MAIN


class MarketDataParams(BaseModel):
    instruments: list[str] = Field(
        default_factory=list,
        description="venue:symbol 형식. 예: upbit:KRW-BTC, krx:005930, nasdaq:AAPL",
        min_length=1,
    )
    timeframe: str = Field(default="1d", description="1m 5m 15m 30m 1h 4h 1d 1w")
    lookback: int = Field(default=200, ge=2, le=5000, description="가져올 봉 개수")
    closed_only: bool = Field(default=True, description="미완성 봉 제외")
    skip_stale: bool = Field(
        default=True, description="직전 실행과 같은 봉이면 제외 (Fresh Bar Gate)"
    )
    source: str = Field(default=AUTO, description="'auto'면 라우팅 표를 따름. 또는 Connection ID")


@register
class MarketDataNode(BaseNode):
    type = "marketData"
    display_name = "Market Data"
    category = "input"
    description = "거래소·증권사에서 OHLCV 캔들을 수집합니다. venue별 소스 라우팅은 자동입니다."
    ParamsModel = MarketDataParams
    inputs = (MAIN,)
    outputs = (MAIN,)
    requires_input = False  # 트리거 뒤에 놓을 수도, 단독 루트로 둘 수도 있다

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: MarketDataParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        timeframe = normalize(params.timeframe)
        items: list[Item] = []
        stale: list[str] = []
        no_bar: list[str] = []

        for raw in params.instruments:
            instrument = InstrumentRef.parse(raw)
            calendar = ctx.calendar_for(instrument)

            as_of = calendar.last_closed_bar(ctx.now, timeframe)
            if as_of is None:
                no_bar.append(instrument.key)
                continue
            ctx.assert_not_future(as_of, f"{instrument.key}의 as_of")

            # ---- Fresh Bar Gate -------------------------------------------------
            key = bar_key(ctx.node_id or self.type, instrument.key, timeframe)
            if params.skip_stale and not ctx.is_backtest:
                previous = ctx.bar_state.last_seen(key)
                if previous is not None and previous >= as_of:
                    stale.append(instrument.key)
                    continue

            result = await ctx.providers.fetch_ohlcv(
                instrument, timeframe, as_of, params.lookback, source=params.source
            )
            df = validate_ohlcv(result.df)
            if params.closed_only:
                df = df[df.index <= as_of]

            ctx.bar_state.mark(key, as_of)
            items.append(
                Item(
                    instrument=instrument,
                    timeframe=timeframe,
                    as_of=as_of,
                    ohlcv=df,
                    meta={"source": result.provider_id, "adjusted": ctx.settings.adjusted},
                )
            )

        if stale:
            ctx.log.info(f"새로 마감된 봉이 없어 제외: {', '.join(stale)}")
        if no_bar:
            ctx.log.warning(f"마감된 봉을 찾지 못함: {', '.join(no_bar)}")
        ctx.log.info(f"{len(items)}개 종목 수집 완료 ({timeframe})")

        return {MAIN: Bundle(items)}
