"""Symbol Universe — 무엇을 훑을 것인가 (ARCHITECTURE.md 5장).

**이 프로젝트의 목적이 "혼자서는 볼 수 없는 범위를 대신 보는 것"이므로, 종목을
손으로 적어 두는 한 그 목적에 닿지 않는다.** 이 노드가 거래소에 "지금 뭐가
거래되고 있나"를 물어 유니버스를 만든다.

산출물은 **items가 아니라 `Bundle.context["universe"]`의 심볼 목록**이다. 아직
봉을 받지 않았으므로 `Item`을 만들 수 없다 — `Item.as_of`는 "마감된 캔들의 종료
시각"인데(4.1), 그 값은 Market Data가 캘린더로 판정하기 전까지 존재하지 않는다.
없는 as_of를 지어내면 그 거짓말이 신호까지 따라간다.

⚠️ ★ **동적 유니버스는 백테스트에서 거부된다.**

거래소가 주는 목록은 언제나 **"지금"** 이다. 거래대금 상위 30개를 오늘 뽑아
2년치를 리플레이하면, 2년 전에는 알 수 없었던 정보로 종목을 고른 것이 된다.
2년간 살아남아 상위에 든 코인만 보게 되므로 성과가 구조적으로 부풀려진다 —
4.8의 서바이버십 편향과 같은 구조다.

**이 경로는 `strategy check`가 잡지 못한다.** 전략 코드는 완전히 인과적이고
미래 참조는 유니버스 쪽에 있기 때문이다. AST 검사에 걸리지 않는 look-ahead라
차단을 여기에 명시적으로 둔다. Phase 3에서 point-in-time 스냅샷이 생기면
그때 backtest 경로가 열린다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.market.instrument import InstrumentRef
from app.nodes.base import BaseNode, NodeError
from app.nodes.registry import register
from app.providers.base import UniverseNotSupportedError
from app.providers.registry import AUTO, NoProviderError
from app.schemas.pipeline import MAIN

#: 하류 노드가 유니버스를 읽는 `Bundle.context` 키.
UNIVERSE_KEY = "universe"

#: 유니버스 산출 근거. node_runs에 남아 "그날 왜 이 종목들이었나"를 되짚게 한다.
UNIVERSE_META_KEY = "universe_meta"


class SymbolUniverseParams(BaseModel):
    instruments: list[str] = Field(
        default_factory=list,
        description="고정 목록. venue:symbol 형식. 동적 조회 결과에 더해집니다.",
    )
    venue: str | None = Field(
        default=None,
        description="이 venue의 종목을 거래소에서 조회합니다. 예: upbit",
    )
    quote_currency: str | None = Field(
        default=None,
        description="결제 통화로 마켓을 제한합니다. 예: KRW (업비트 원화 마켓만)",
    )
    top_by_turnover: int | None = Field(
        default=None,
        ge=1,
        description="24시간 거래대금 상위 N개만 남깁니다. 유동성 컷.",
    )
    exclude: list[str] = Field(
        default_factory=list, description="제외할 종목. venue:symbol 형식."
    )
    source: str = Field(default=AUTO, description="'auto'면 라우팅 표를 따름")


@register
class SymbolUniverseNode(BaseNode):
    type = "symbolUniverse"
    display_name = "Symbol Universe"
    category = "input"
    description = "무엇을 훑을지 정합니다. 고정 목록 + 거래소 조회(거래대금 상위 N)."
    ParamsModel = SymbolUniverseParams
    inputs = (MAIN,)
    outputs = (MAIN,)
    requires_input = False  # 트리거 뒤에도, 단독 루트로도 놓을 수 있다

    async def run(
        self,
        inputs: dict[str, Bundle],
        params: SymbolUniverseParams,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        dynamic = params.venue is not None
        if dynamic and ctx.is_backtest:
            # 모듈 docstring 참조. 조용히 고정 목록으로 물러서지 않는다 — 그러면
            # 백테스트가 사용자가 적지 않은 유니버스로 돌아간다.
            raise NodeError(
                "동적 유니버스는 백테스트에서 쓸 수 없습니다 — 거래소 목록은 항상 "
                "'지금'이라 과거를 리플레이하면 유니버스가 미래를 봅니다(서바이버십 편향). "
                "backtest 모드에서는 instruments에 종목을 직접 적으세요."
            )

        fixed = [InstrumentRef.parse(raw) for raw in params.instruments]
        discovered, source_id = await self._discover(params, ctx) if dynamic else ([], None)

        excluded = {InstrumentRef.parse(raw).key for raw in params.exclude}
        merged: dict[str, InstrumentRef] = {}
        for ref in [*fixed, *discovered]:
            if ref.key not in excluded:
                merged.setdefault(ref.key, ref)

        if not merged:
            # 빈 유니버스는 실패가 아니라 정상 출력이다(4.1). 다만 조용하면 안 된다 —
            # 필터가 과했는지 소스가 빈 목록을 줬는지 구분되어야 한다.
            ctx.log.warning(
                "유니버스가 0종목입니다. quote_currency·exclude 조건이 과했는지, "
                "또는 소스가 빈 목록을 줬는지 확인하세요."
            )

        keys = list(merged)
        ctx.log.info(f"유니버스 {len(keys)}종목 (고정 {len(fixed)} · 조회 {len(discovered)})")

        meta = {
            "size": len(keys),
            "fixed": len(fixed),
            "discovered": len(discovered),
            "venue": params.venue,
            "quote_currency": params.quote_currency,
            "top_by_turnover": params.top_by_turnover,
            "source": source_id,
            "point_in_time": not dynamic,
        }
        context = {**inputs.get(MAIN, Bundle.empty()).context}
        context[UNIVERSE_KEY] = keys
        context[UNIVERSE_META_KEY] = meta
        return {MAIN: Bundle(items=[], context=context)}

    # ------------------------------------------------------------------- 내부
    async def _discover(
        self, params: SymbolUniverseParams, ctx: RunContext
    ) -> tuple[list[InstrumentRef], str]:
        venue = params.venue or ""
        try:
            entries, source_id = await ctx.providers.list_instruments(venue, source=params.source)
        except (UniverseNotSupportedError, NoProviderError) as exc:
            raise NodeError(str(exc)) from exc

        if params.quote_currency:
            wanted = params.quote_currency.upper()
            entries = [e for e in entries if e.instrument.quote_currency == wanted]

        if params.top_by_turnover is not None:
            entries = self._top_by_turnover(entries, params.top_by_turnover, ctx)

        return [e.instrument for e in entries], source_id

    @staticmethod
    def _top_by_turnover(entries: list, keep: int, ctx: RunContext) -> list:
        """거래대금 상위 N개. **절삭은 반드시 로그로 남긴다** (조용한 절삭 금지)."""
        missing = [e.instrument.key for e in entries if e.quote_volume_24h is None]
        if missing:
            # 거래대금이 없는 종목을 0으로 취급하면 목록 맨 뒤로 밀려 조용히
            # 사라진다. "거래가 없었다"와 "소스가 값을 안 줬다"는 다르다.
            ctx.log.warning(
                f"24시간 거래대금을 받지 못해 유동성 컷에서 제외: "
                f"{', '.join(missing[:20])}"
                f"{f' 외 {len(missing) - 20}건' if len(missing) > 20 else ''}"
            )
        ranked = sorted(
            (e for e in entries if e.quote_volume_24h is not None),
            key=lambda e: e.quote_volume_24h,
            reverse=True,
        )
        if len(ranked) > keep:
            ctx.log.info(
                f"거래대금 상위 {keep}종목만 통과 ({len(ranked)}종목 중 "
                f"{len(ranked) - keep}종목 컷)"
            )
        return ranked[:keep]
