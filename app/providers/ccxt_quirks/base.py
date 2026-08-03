"""quirk 기본 동작.

CCXT 통합 표기를 그대로 쓰는 거래소(binance 등)는 이 클래스로 충분하다.
"""

from __future__ import annotations

from app.market.instrument import InstrumentRef


class ExchangeQuirk:
    """거래소 하나의 예외 처리.

    venue의 `QuoteStyle`이 `SLASH_SUFFIX`면 우리 표기와 CCXT 표기가 같으므로
    변환이 필요 없다.
    """

    def to_exchange_symbol(self, instrument: InstrumentRef) -> str:
        """`InstrumentRef` → CCXT 통합 심볼."""
        return instrument.symbol

    def to_venue_symbol(self, market: dict[str, object]) -> str:
        """CCXT market 정보 → 이 저장소의 venue 심볼 표기."""
        return str(market["symbol"])

    def display_name(self, market: dict[str, object]) -> str:
        """사람이 읽는 이름. 거래소가 주지 않으면 빈 문자열.

        CCXT 통합 스키마에는 이름이 없다. 거래소 원본 응답(`market["info"]`)에
        있는 경우가 있어 quirk가 꺼내 준다.
        """
        return ""
