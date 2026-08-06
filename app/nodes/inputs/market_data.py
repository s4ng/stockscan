"""Market Data Fetcher — 시세 수집 진입점.

두 가지 안전장치를 담고 있다.
  - **closed_only**: 미완성 봉을 잘라내 신호가 생겼다 사라지는 현상을 막는다 (4.4)
  - **skip_stale**:  직전 실행과 같은 봉이면 제외한다. 코인+주식 혼합 파이프라인에서
                     장 마감 중인 종목이 같은 신호를 매번 재발생시키는 것을 막는다 (3.5)

봉은 `ctx.ohlcv`에서 온다. **이 노드는 뒤에 `ohlcv_cache`가 있는지 모른다** —
3.9가 "노드는 캐시 구현을 모른다"로 인터페이스를 고정했기 때문이다.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.state import bar_key
from app.engine.types import Bundle, Item, validate_ohlcv
from app.market.instrument import InstrumentRef
from app.market.timeframe import normalize
from app.nodes.base import BaseNode, NodeError
from app.nodes.inputs.symbol_universe import UNIVERSE_KEY, UNIVERSE_NAMES_KEY
from app.nodes.registry import register
from app.providers.ohlcv_source import CacheMissError
from app.providers.registry import AUTO
from app.schemas.pipeline import MAIN


class MarketDataParams(BaseModel):
    instruments: list[str] = Field(
        default_factory=list,
        description=(
            "venue:symbol 형식. 예: krx:005930, nasdaq:AAPL. "
            "비워 두면 상류 Symbol Universe가 정한 목록을 씁니다."
        ),
    )
    timeframe: str = Field(default="1d", description="1m 5m 15m 30m 1h 4h 1d 1w")
    lookback: int = Field(default=200, ge=2, le=5000, description="가져올 봉 개수")
    closed_only: bool = Field(default=True, description="미완성 봉 제외")
    skip_stale: bool = Field(
        default=True, description="직전 실행과 같은 봉이면 제외 (Fresh Bar Gate)"
    )
    source: str = Field(default=AUTO, description="'auto'면 라우팅 표를 따름. 또는 Connection ID")
    cache: Literal["auto", "off", "only"] = Field(
        default="auto",
        description=(
            "auto=캐시 우선·부족하면 소스 / off=항상 소스 / only=캐시만(외부 호출 없음). "
            "쓰기는 --commit에서만 일어납니다."
        ),
    )


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
        upstream = inputs.get(MAIN, Bundle.empty())
        targets = _targets(params, upstream)
        items: list[Item] = []
        stale: list[str] = []
        no_bar: list[str] = []
        fallbacks: list[str] = []
        from_cache: list[str] = []
        missing: list[str] = []

        # 상류가 실어 보낸 이름을 되살린다. 유니버스가 문자열 목록으로 넘어오면서
        # `display_name`이 사라지는데, 다시 얻으려면 종목마다 목록을 재조회해야 한다.
        names = upstream.context.get(UNIVERSE_NAMES_KEY)
        names = names if isinstance(names, dict) else {}

        for raw in targets:
            instrument = InstrumentRef.parse(raw)
            if names.get(instrument.key):
                instrument = replace(instrument, display_name=str(names[instrument.key]))
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

            assert ctx.ohlcv is not None  # RunContext.__post_init__이 채운다
            try:
                result = await ctx.ohlcv.load(
                    instrument,
                    timeframe,
                    as_of,
                    params.lookback,
                    source=params.source,
                    policy=params.cache,
                )
            except CacheMissError as exc:
                # cache=only에서 커버리지가 모자란 것은 **명시적 거부**다 (4.8).
                # 조용히 소스를 부르면 "외부 호출을 하지 않는다"는 전제가 깨진다.
                missing.append(instrument.key)
                ctx.log.warning(str(exc))
                continue

            df = validate_ohlcv(result.df)
            if params.closed_only:
                df = df[df.index <= as_of]

            meta: dict[str, object] = {
                "source": result.provider_id,
                # 설정값이 아니라 **소스가 실제로 준 것**을 적는다. 코인에는
                # 액면분할·배당이 없어서 adjusted 개념 자체가 없는데(3.8) 설정을
                # 그대로 베끼면 조정가를 받은 것처럼 남는다. 이 값은 캐시 키에
                # 들어가므로(규칙 8) 틀리면 조정가/비조정가가 섞여 지표가 조용히
                # 어긋나고 원인 추적이 불가능해진다.
                "adjusted": result.adjusted,
            }
            for note in result.notes:
                ctx.log.warning(note)
            if result.from_cache:
                # 어느 소스가 채운 구간인지가 남아야 정합성 문제를 되짚을 수 있다.
                meta["cached_sources"] = list(result.cached_sources)
                from_cache.append(instrument.key)
            if result.used_fallback:
                # 폴백은 소스가 바뀌었다는 뜻이다. 지표 불연속의 원인을 사후에
                # 추적하려면 어느 종목이 어느 소스로 대체됐는지가 남아야 한다.
                meta["fallback_from"] = list(result.failed_sources)
                fallbacks.append(
                    f"{instrument.key}: {', '.join(result.failed_sources)} → {result.provider_id}"
                )

            # 봉 소비는 예약만 한다. 실행이 성공해야 runner가 확정한다 (state.py 참조).
            ctx.bar_state.stage(key, as_of)
            items.append(
                Item(
                    instrument=instrument,
                    timeframe=timeframe,
                    as_of=as_of,
                    ohlcv=df,
                    meta=meta,
                )
            )

        if stale:
            ctx.log.info(f"새로 마감된 봉이 없어 제외: {', '.join(stale)}")
        if no_bar:
            ctx.log.warning(f"마감된 봉을 찾지 못함: {', '.join(no_bar)}")
        if from_cache:
            ctx.log.info(f"{len(from_cache)}종목을 ohlcv_cache에서 읽었습니다 (외부 호출 없음)")
        if missing:
            ctx.log.warning(
                f"캐시 부족으로 제외 {len(missing)}종목 — "
                f"`marketscan ingest --commit`으로 봉을 쌓으세요."
            )
        if fallbacks:
            ctx.log.warning(
                f"소스 폴백 발동 — {' | '.join(fallbacks)}. "
                f"지표가 불연속해 보이면 Connections에서 원래 소스의 상태를 확인하세요."
            )
        ctx.log.info(f"{len(items)}개 종목 수집 완료 ({timeframe})")

        # 유니버스 산출 근거를 하류로 흘려보낸다 — 리포트와 node_runs가 "그날 몇
        # 종목을 훑었는가"를 알아야 랭킹의 표본 수를 해석할 수 있다.
        return {MAIN: Bundle(items, dict(upstream.context))}


def _targets(params: MarketDataParams, upstream: Bundle) -> list[str]:
    """수집할 종목. 노드에 적힌 목록이 우선이고, 없으면 상류 유니버스를 쓴다.

    둘 다 비어 있으면 **조용히 0종목을 수집하지 않고** 터뜨린다 — 빈 결과는
    "오늘은 새 봉이 없다"와 구분되지 않아서, 배선이 끊긴 것을 아무도 모른다.
    """
    if params.instruments:
        return list(params.instruments)

    universe = upstream.context.get(UNIVERSE_KEY)
    if isinstance(universe, list) and universe:
        return [str(s) for s in universe]

    raise NodeError(
        "수집할 종목이 없습니다. marketData의 instruments에 종목을 적거나, "
        "상류에 symbolUniverse 노드를 연결하세요."
    )
