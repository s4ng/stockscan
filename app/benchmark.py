"""벤치마크 지수 — "시장보다 나았나" (ARCHITECTURE.md 4.8).

★ **이것이 없으면 hit rate가 거짓말을 한다.** 상승장에서는 아무 종목이나 찍어도
승률이 60%를 넘는다. 그 숫자를 보고 "전략이 좋다"고 결론내면 **시장이 좋았던 것을
전략의 공으로 돌린 것**이다.

**시장마다 짝이 다르다** — 코인 신호를 KOSPI와 비교하는 것이 의미 없었듯(그건 이제
없다) 미국 신호를 KOSPI와 비교하면 안 된다. 규칙 17이 랭킹에 대해 하는 말을 여기서는
비교에 대해 한다.

⚠️ **지수는 랭킹 풀에 들어가지 않는다.** venue의 `market`이 `benchmark`라
`venues_of("krx")`가 돌려주지 않고, 따라서 파이프라인이 절대 집지 않는다.
"""

from __future__ import annotations

from app.market.instrument import InstrumentRef

#: 시장 → 그 시장의 벤치마크. FDR 심볼을 그대로 쓴다.
#:
#: KS11 = KOSPI · US500 = S&P 500. **거래 가능한 ETF가 아니라 지수**를 쓰는 이유는
#: 비교 대상이 "시장이 어떻게 움직였나"이지 "그 ETF를 샀으면"이 아니기 때문이다 —
#: 후자는 체결·수수료 가정을 끌어들이고, 그건 Phase 5다.
BENCHMARKS: dict[str, str] = {
    "krx": "krx_index:KS11",
    "us": "us_index:US500",
}


def for_market(market: str) -> InstrumentRef | None:
    """이 시장의 벤치마크. 없으면 None (비교를 생략하지 **말고** 없다고 말한다)."""
    raw = BENCHMARKS.get(market)
    return InstrumentRef.parse(raw) if raw else None


def all_refs() -> list[InstrumentRef]:
    """수집 대상에 더할 지수 전부."""
    return [InstrumentRef.parse(raw) for raw in BENCHMARKS.values()]


def market_of(venue: str) -> str | None:
    """venue가 속한 시장 — 신호를 어느 지수와 비교할지 정한다."""
    from app.market.instrument import VENUES

    spec = VENUES.get(venue)
    return spec.market if spec else None
