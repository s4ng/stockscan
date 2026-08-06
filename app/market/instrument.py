"""InstrumentRef — 통일 심볼 식별자 (ARCHITECTURE.md 3.1).

거래소마다 표기가 다르고(005930 / AAPL) 티커가 시장 간 충돌할 수 있으므로,
내부에서는 항상 venue를 붙인 정규 문자열로 다룬다.

**대상은 주식뿐이다** (2026-08-06 결정). 코인을 걷어내면서 결제 통화를 심볼에서
파싱하던 `QuoteStyle`도 함께 사라졌다 — 주식은 venue가 통화를 고정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 벤치마크 지수가 속하는 "시장". 거래 대상이 아니므로 랭킹 풀이 아니다 —
#: 이름을 따로 두어 `venues_of("krx")`에 섞이지 않게 한다 (규칙 17).
BENCHMARK = "benchmark"


@dataclass(frozen=True)
class VenueSpec:
    """venue 하나가 갖는 고정 속성."""

    venue: str
    calendar_id: str
    market: str
    """★ **횡단면을 비교할 수 있는 단위** (CLAUDE.md 규칙 17).

    수익률 분포의 스케일이 비슷해서 **한 줄로 세워도 되는 집단**을 뜻한다.
    한국 대형주와 미국 성장주는 분산이 달라, 섞어 정렬하면 분산이 넓은 쪽이 위를
    쓸어가 **모멘텀이 아니라 변동성으로 줄 세운 것**이 된다. 랭킹·백분위·컷이
    전부 이 키로 나뉜다.

    `calendar_id`와 값이 겹치지만 뜻이 다르다 — 그쪽은 "언제 거래되는가"고
    이쪽은 "무엇과 비교할 수 있는가"다. 지금 일치하는 것은 우연이므로
    한 필드로 합치지 않는다. `nasdaq`과 `nyse`는 같은 `us`다.
    """

    quote_currency: str
    """결제 통화. 주식은 venue가 정한다 — 심볼에서 유도하지 않는다."""


#: 알려진 venue 목록. 새 거래소를 지원하면 여기에 한 줄 추가한다.
#: `market`은 `--market` 필터와 랭킹 풀이 함께 쓰는 어휘다 — 두 곳에 표를 두면 어긋난다.
VENUES: dict[str, VenueSpec] = {
    "krx": VenueSpec("krx", "krx", market="krx", quote_currency="KRW"),
    "nasdaq": VenueSpec("nasdaq", "us_equity", market="us", quote_currency="USD"),
    "nyse": VenueSpec("nyse", "us_equity", market="us", quote_currency="USD"),
    # ★ 벤치마크 지수 (4.8). **market이 `benchmark`인 것이 핵심이다** — 랭킹 풀에
    #   섞이면 지수가 종목과 함께 줄 세워진다. `venues_of("krx")`가 이걸 돌려주지
    #   않으므로 파이프라인에는 절대 들어가지 않는다.
    "krx_index": VenueSpec("krx_index", "krx", market=BENCHMARK, quote_currency="KRW"),
    "us_index": VenueSpec("us_index", "us_equity", market=BENCHMARK, quote_currency="USD"),
}


def venues_of(market: str) -> tuple[str, ...]:
    """이 시장에 속한 venue들. `--market` 필터가 쓴다."""
    return tuple(v for v, spec in VENUES.items() if spec.market == market)


def markets() -> tuple[str, ...]:
    """훑을 수 있는 시장 목록. 표를 두 곳에 두지 않으려고 VENUES에서 유도한다.

    **벤치마크는 뺀다** — 지수는 판정 대상이 아니라 비교 대상이다.
    """
    return tuple(
        dict.fromkeys(
            spec.market for spec in VENUES.values() if spec.market != BENCHMARK
        )
    )


def is_benchmark(venue: str) -> bool:
    spec = VENUES.get(venue)
    return spec is not None and spec.market == BENCHMARK


class UnknownVenueError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentRef:
    """하나의 거래 대상. 엔진과 노드는 이 타입만 알면 된다."""

    venue: str
    symbol: str
    quote_currency: str
    display_name: str = ""

    @property
    def key(self) -> str:
        """정규 식별자. 캐시 키·로그·UI에서 이 문자열을 쓴다."""
        return f"{self.venue}:{self.symbol}"

    @property
    def calendar_id(self) -> str:
        return VENUES[self.venue].calendar_id

    @property
    def market(self) -> str:
        """횡단면을 비교할 수 있는 단위 (규칙 17). `VenueSpec.market` 참조."""
        return VENUES[self.venue].market

    @classmethod
    def parse(cls, raw: str) -> InstrumentRef:
        """`"krx:005930"` 형태의 문자열을 InstrumentRef로 변환한다."""
        if ":" not in raw:
            raise ValueError(
                f"instrument는 'venue:symbol' 형식이어야 합니다 (받은 값: {raw!r}). "
                f"예: 'krx:005930', 'nasdaq:AAPL'"
            )
        venue, symbol = raw.split(":", 1)
        venue = venue.strip().lower()
        symbol = symbol.strip()
        spec = VENUES.get(venue)
        if spec is None:
            raise UnknownVenueError(
                f"알 수 없는 venue: {venue!r}. 지원 목록: {', '.join(sorted(VENUES))}"
            )
        if not symbol:
            raise ValueError(f"symbol이 비어 있습니다: {raw!r}")
        return cls(
            venue=venue,
            symbol=symbol,
            quote_currency=spec.quote_currency,
            display_name=symbol,
        )

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return self.key
