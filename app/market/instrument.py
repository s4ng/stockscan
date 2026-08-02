"""InstrumentRef — 통일 심볼 식별자 (ARCHITECTURE.md 3.1).

거래소마다 표기가 다르고(KRW-BTC / BTC/USDT / 005930 / AAPL) 티커가 시장 간
충돌할 수 있으므로, 내부에서는 항상 venue를 붙인 정규 문자열로 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    EQUITY = "equity"


class QuoteStyle(StrEnum):
    """결제 통화를 어디서 읽어내는가.

    거래소를 venue 하나로 묶고 통화를 상수로 박으면 조용히 틀린다 — 업비트에는
    KRW 말고 BTC·USDT 마켓이 있고, 바이낸스도 USDT 외 페어가 많다. 통화가 틀리면
    3.7(통화 보존)과 알림 템플릿의 통화 기호가 함께 어긋난다.
    """

    FIXED = "fixed"
    """주식 — venue의 통화가 곧 결제 통화다."""

    DASH_PREFIX = "dash_prefix"
    """업비트 원본 표기 `KRW-BTC` → 앞이 결제 통화."""

    SLASH_SUFFIX = "slash_suffix"
    """CCXT 통합 표기 `BTC/USDT` → 뒤가 결제 통화."""


@dataclass(frozen=True)
class VenueSpec:
    """venue 하나가 갖는 고정 속성."""

    venue: str
    asset_class: AssetClass
    calendar_id: str
    quote_style: QuoteStyle
    quote_currency: str = ""
    """QuoteStyle.FIXED일 때만 의미가 있다. 나머지는 symbol에서 유도한다."""

    def resolve_quote_currency(self, symbol: str) -> str:
        """symbol에서 결제 통화를 뽑아낸다. 못 뽑으면 그대로 터뜨린다."""
        match self.quote_style:
            case QuoteStyle.FIXED:
                return self.quote_currency
            case QuoteStyle.DASH_PREFIX:
                quote, sep, base = symbol.partition("-")
                if not (sep and quote and base):
                    raise ValueError(
                        f"{self.venue}의 symbol은 '결제통화-기초자산' 형식이어야 합니다 "
                        f"(받은 값: {symbol!r}). 예: 'KRW-BTC', 'BTC-ETH'"
                    )
                return quote.upper()
            case QuoteStyle.SLASH_SUFFIX:
                base, sep, quote = symbol.partition("/")
                if not (sep and quote and base):
                    raise ValueError(
                        f"{self.venue}의 symbol은 '기초자산/결제통화' 형식이어야 합니다 "
                        f"(받은 값: {symbol!r}). 예: 'BTC/USDT', 'ETH/BTC'"
                    )
                return quote.upper()


#: 알려진 venue 목록. 새 거래소를 지원하면 여기에 한 줄 추가한다.
VENUES: dict[str, VenueSpec] = {
    "upbit": VenueSpec("upbit", AssetClass.CRYPTO, "crypto24x7", QuoteStyle.DASH_PREFIX),
    "binance": VenueSpec("binance", AssetClass.CRYPTO, "crypto24x7", QuoteStyle.SLASH_SUFFIX),
    "krx": VenueSpec("krx", AssetClass.EQUITY, "krx", QuoteStyle.FIXED, "KRW"),
    "nasdaq": VenueSpec("nasdaq", AssetClass.EQUITY, "us_equity", QuoteStyle.FIXED, "USD"),
    "nyse": VenueSpec("nyse", AssetClass.EQUITY, "us_equity", QuoteStyle.FIXED, "USD"),
}


class UnknownVenueError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentRef:
    """하나의 거래 대상. 엔진과 노드는 이 타입만 알면 된다."""

    venue: str
    symbol: str
    asset_class: AssetClass
    quote_currency: str
    display_name: str = ""

    @property
    def key(self) -> str:
        """정규 식별자. 캐시 키·로그·UI에서 이 문자열을 쓴다."""
        return f"{self.venue}:{self.symbol}"

    @property
    def calendar_id(self) -> str:
        return VENUES[self.venue].calendar_id

    @classmethod
    def parse(cls, raw: str) -> InstrumentRef:
        """`"upbit:KRW-BTC"` 형태의 문자열을 InstrumentRef로 변환한다."""
        if ":" not in raw:
            raise ValueError(
                f"instrument는 'venue:symbol' 형식이어야 합니다 (받은 값: {raw!r}). "
                f"예: 'upbit:KRW-BTC', 'krx:005930', 'nasdaq:AAPL'"
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
            asset_class=spec.asset_class,
            quote_currency=spec.resolve_quote_currency(symbol),
            display_name=symbol,
        )

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return self.key
