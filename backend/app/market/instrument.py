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


@dataclass(frozen=True)
class VenueSpec:
    """venue 하나가 갖는 고정 속성."""

    venue: str
    asset_class: AssetClass
    quote_currency: str
    calendar_id: str


#: 알려진 venue 목록. 새 거래소를 지원하면 여기에 한 줄 추가한다.
VENUES: dict[str, VenueSpec] = {
    "upbit": VenueSpec("upbit", AssetClass.CRYPTO, "KRW", "crypto24x7"),
    "binance": VenueSpec("binance", AssetClass.CRYPTO, "USDT", "crypto24x7"),
    "krx": VenueSpec("krx", AssetClass.EQUITY, "KRW", "krx"),
    "nasdaq": VenueSpec("nasdaq", AssetClass.EQUITY, "USD", "us_equity"),
    "nyse": VenueSpec("nyse", AssetClass.EQUITY, "USD", "us_equity"),
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
            quote_currency=spec.quote_currency,
            display_name=symbol,
        )

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return self.key
