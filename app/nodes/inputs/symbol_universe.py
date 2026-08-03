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
차단을 여기에 명시적으로 둔다. Phase 3.5(백테스트)에서 point-in-time 스냅샷이
생기면 그때 backtest 경로가 열린다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.market.instrument import VENUES, InstrumentRef
from app.nodes.base import BaseNode, NodeError
from app.nodes.registry import register
from app.providers.base import UniverseNotSupportedError
from app.providers.registry import AUTO, NoProviderError
from app.schemas.pipeline import MAIN

#: 하류 노드가 유니버스를 읽는 `Bundle.context` 키.
UNIVERSE_KEY = "universe"

#: 유니버스 산출 근거. node_runs에 남아 "그날 왜 이 종목들이었나"를 되짚게 한다.
UNIVERSE_META_KEY = "universe_meta"

#: `venue:symbol` → 사람이 읽는 이름 (`krx:005930` → `삼성전자`).
#:
#: 유니버스는 문자열 목록으로 하류에 넘어가므로 `InstrumentRef.display_name`이
#: 그 경계에서 사라진다. 이름을 다시 얻으려면 종목마다 목록을 재조회해야 하는데,
#: 방금 받아 놓고 버린 값이라 그 호출이 통째로 낭비다. 그래서 함께 실어 보낸다.
UNIVERSE_NAMES_KEY = "universe_names"

class VenueQuery(BaseModel):
    """venue 하나를 어떻게 훑을 것인가.

    **원소가 문자열이 아니라 조건 묶음인 이유**는 venue마다 필요한 컷이 다르기
    때문이다 — 코인은 KRW 마켓 제한이 필요하고 주식은 아니며, 미국 목록에는
    거래대금이 아예 없다. 조건을 노드 수준에 하나만 두면 어느 시장엔가 안 맞는다.
    """

    venue: str = Field(description="예: upbit, krx, nasdaq")
    quote_currency: str | None = Field(
        default=None, description="결제 통화로 마켓 제한. 예: KRW (업비트 원화 마켓만)"
    )
    top_by_turnover: int | None = Field(
        default=None, ge=1, description="거래대금 상위 N개만. 소스가 거래대금을 줘야 합니다"
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "목록 앞에서 N개만. 소스가 거래대금을 주지 않는 venue(미국)의 대안입니다. "
            "⚠️ 소스가 정렬해 준 순서에 기댑니다."
        ),
    )
    exclude: list[str] = Field(default_factory=list, description="제외할 종목")

    def cut(self) -> str:
        return (
            f"거래대금 상위 {self.top_by_turnover}"
            if self.top_by_turnover
            else (f"목록 상위 {self.limit}" if self.limit else "전량")
        )


class SymbolUniverseParams(BaseModel):
    instruments: list[str] = Field(
        default_factory=list,
        description="고정 목록. venue:symbol 형식. 동적 조회 결과에 더해집니다.",
    )
    venues: list[VenueQuery] = Field(
        default_factory=list,
        description="훑을 venue들. venue마다 컷 조건을 따로 답니다.",
    )

    # --- 아래 넷은 단수 표기의 하위 호환. `venues` 원소 하나로 접힌다 -------------
    venue: str | None = Field(default=None, description="(구) 단일 venue. venues로 접힙니다")
    quote_currency: str | None = Field(default=None, description="(구) venue와 함께 씁니다")
    top_by_turnover: int | None = Field(default=None, ge=1, description="(구) venue와 함께")
    exclude: list[str] = Field(
        default_factory=list, description="제외할 종목. venue:symbol 형식."
    )

    source: str = Field(default=AUTO, description="'auto'면 라우팅 표를 따름")

    @model_validator(mode="after")
    def _fold_singular(self) -> SymbolUniverseParams:
        """단수 표기를 `venues` 원소 하나로 접는다.

        기존 파이프라인 파일이 그대로 돌아야 한다 — 정의는 버전으로 보존되므로
        (4.7) 과거 버전을 리플레이할 때 옛 표기를 읽지 못하면 이력이 끊긴다.
        """
        if self.venue is None:
            return self
        if self.venues:
            raise ValueError("venue와 venues를 함께 쓸 수 없습니다. venues 하나로 적으세요.")
        object.__setattr__(
            self,
            "venues",
            [
                VenueQuery(
                    venue=self.venue,
                    quote_currency=self.quote_currency,
                    top_by_turnover=self.top_by_turnover,
                    exclude=list(self.exclude),
                )
            ],
        )
        return self


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
        dynamic = bool(params.venues)
        if dynamic and ctx.is_backtest:
            # 모듈 docstring 참조. 조용히 고정 목록으로 물러서지 않는다 — 그러면
            # 백테스트가 사용자가 적지 않은 유니버스로 돌아간다.
            raise NodeError(
                "동적 유니버스는 백테스트에서 쓸 수 없습니다 — 거래소 목록은 항상 "
                "'지금'이라 과거를 리플레이하면 유니버스가 미래를 봅니다(서바이버십 편향). "
                "backtest 모드에서는 instruments에 종목을 직접 적으세요."
            )

        fixed = [InstrumentRef.parse(raw) for raw in params.instruments]
        excluded = {InstrumentRef.parse(raw).key for raw in params.exclude}
        merged: dict[str, InstrumentRef] = {
            ref.key: ref for ref in fixed if ref.key not in excluded
        }

        # venue를 **하나씩 따로** 훑는다. 유동성 컷을 시장 간에 섞으면 거래대금
        # 단위가 달라(원 vs 달러) 비교 자체가 성립하지 않는다 (3.7).
        per_venue: list[dict[str, Any]] = []
        for query in params.venues:
            refs, source_id = await self._discover(query, params.source, ctx)
            added = 0
            for ref in refs:
                if ref.key not in excluded and ref.key not in merged:
                    merged[ref.key] = ref
                    added += 1
            per_venue.append(
                {
                    "venue": query.venue,
                    "market": VENUES[query.venue].market if query.venue in VENUES else None,
                    "quote_currency": query.quote_currency,
                    "cut": query.cut(),
                    "count": added,
                    "source": source_id,
                }
            )
            ctx.log.info(f"{query.venue}: {added}종목 ({query.cut()}, 소스 {source_id})")

        if not merged:
            # 빈 유니버스는 실패가 아니라 정상 출력이다(4.1). 다만 조용하면 안 된다 —
            # 필터가 과했는지 소스가 빈 목록을 줬는지 구분되어야 한다.
            ctx.log.warning(
                "유니버스가 0종목입니다. quote_currency·exclude 조건이 과했는지, "
                "또는 소스가 빈 목록을 줬는지 확인하세요."
            )

        keys = list(merged)
        discovered = sum(v["count"] for v in per_venue)
        ctx.log.info(f"유니버스 {len(keys)}종목 (고정 {len(fixed)} · 조회 {discovered})")

        meta = {
            "size": len(keys),
            "fixed": len(fixed),
            "discovered": discovered,
            "venues": per_venue,
            "point_in_time": not dynamic,
        }
        context = {**inputs.get(MAIN, Bundle.empty()).context}
        context[UNIVERSE_KEY] = keys
        context[UNIVERSE_META_KEY] = meta
        context[UNIVERSE_NAMES_KEY] = {
            key: ref.display_name
            for key, ref in merged.items()
            if ref.display_name and ref.display_name != ref.symbol
        }
        return {MAIN: Bundle(items=[], context=context)}

    # ------------------------------------------------------------------- 내부
    async def _discover(
        self, query: VenueQuery, source: str, ctx: RunContext
    ) -> tuple[list[InstrumentRef], str]:
        if query.top_by_turnover is not None and query.limit is not None:
            raise NodeError(
                f"{query.venue}: top_by_turnover와 limit을 함께 쓸 수 없습니다. "
                f"거래대금을 주는 소스면 top_by_turnover를, 아니면 limit을 쓰세요."
            )
        assert ctx.universe is not None  # RunContext.__post_init__이 채운다
        try:
            # 거래대금이 필요하면 캐시를 건너뛴다 — 어제 값으로 유동성 컷을 걸면
            # 그날의 후보 집합이 통째로 달라진다 (4.7 `instruments`).
            result = await ctx.universe.list_instruments(
                query.venue,
                source=source,
                needs_turnover=query.top_by_turnover is not None,
            )
        except (UniverseNotSupportedError, NoProviderError) as exc:
            raise NodeError(str(exc)) from exc

        entries, source_id = result.entries, result.source_id
        for note in result.notes:
            ctx.log.info(note)
        for warning in result.warnings:
            # 캐시 쓰기 실패가 조용하면 "좀 느리네"로 보이는데, 실제로는 캐시가
            # 영영 안 채워지고 있다.
            ctx.log.warning(warning)

        if query.quote_currency:
            wanted = query.quote_currency.upper()
            entries = [e for e in entries if e.instrument.quote_currency == wanted]

        if query.top_by_turnover is not None:
            entries = self._top_by_turnover(entries, query, source_id, ctx)
        elif query.limit is not None:
            entries = self._head(entries, query, ctx)

        return [e.instrument for e in entries], source_id

    @staticmethod
    def _top_by_turnover(
        entries: list, query: VenueQuery, source_id: str, ctx: RunContext
    ) -> list:
        """거래대금 상위 N개. **절삭은 반드시 로그로 남긴다** (조용한 절삭 금지)."""
        keep = query.top_by_turnover or 0
        priced = [e for e in entries if e.quote_volume_24h is not None]

        if entries and not priced:
            # ★ 전량이 거래대금 없음 = 소스가 아예 안 주는 것이다. 경고만 남기고
            # 빈 목록을 돌려주면 **그 시장이 통째로 사라진 채 실행이 성공**한다.
            raise NodeError(
                f"{query.venue}: 소스 {source_id}가 거래대금을 주지 않아 "
                f"top_by_turnover를 걸 수 없습니다({len(entries)}종목 전량 탈락). "
                f"이 venue에는 limit을 쓰거나 instruments를 직접 적으세요."
            )

        missing = [e.instrument.key for e in entries if e.quote_volume_24h is None]
        if missing:
            # 거래대금이 없는 종목을 0으로 취급하면 목록 맨 뒤로 밀려 조용히
            # 사라진다. "거래가 없었다"와 "소스가 값을 안 줬다"는 다르다.
            ctx.log.warning(
                f"{query.venue}: 거래대금을 받지 못해 유동성 컷에서 제외 {len(missing)}종목 — "
                f"{', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}"
            )

        ranked = sorted(priced, key=lambda e: e.quote_volume_24h, reverse=True)
        if len(ranked) > keep:
            ctx.log.info(
                f"{query.venue}: 거래대금 상위 {keep}종목만 통과 "
                f"({len(ranked)}종목 중 {len(ranked) - keep}종목 컷)"
            )
        return ranked[:keep]

    @staticmethod
    def _head(entries: list, query: VenueQuery, ctx: RunContext) -> list:
        """목록 앞에서 N개. 거래대금을 주지 않는 venue의 대안이다.

        ⚠️ **소스가 정렬해 준 순서에 기댄다.** FDR의 미국 목록은 시총 순으로
        보이지만 문서화된 계약이 아니므로, 순서가 바뀌면 유니버스가 조용히
        달라진다. 거래대금을 주는 소스가 생기면 그쪽으로 옮긴다.
        """
        keep = query.limit or 0
        if len(entries) > keep:
            ctx.log.warning(
                f"{query.venue}: 목록 앞 {keep}종목만 통과 "
                f"({len(entries)}종목 중 {len(entries) - keep}종목 컷). "
                f"거래대금 컷이 아니라 **소스가 준 순서**에 기댄 절삭입니다."
            )
        return entries[:keep]
