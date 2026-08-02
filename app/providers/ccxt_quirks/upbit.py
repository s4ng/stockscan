"""업비트 예외 처리.

두 가지가 CCXT 통합 표기로 흡수되지 않는다.

1. **심볼 표기** — `KRW-BTC`(결제통화 앞) ↔ `BTC/KRW`(CCXT 통합).
2. **일봉 경계** — 업비트 일봉은 UTC 00:00에 마감한다. 이 저장소의 기본값
   `daily_boundary: "UTC00"`과 같으므로 지금은 어긋나지 않지만, `KST00`으로
   바꾸면 캘린더가 판정한 `as_of`와 거래소가 주는 봉의 경계가 **하루의 15시간만큼
   어긋난다.** 11장 1번이 미결정으로 남겨 둔 지점이라 여기에 적어 둔다.
"""

from __future__ import annotations

from app.market.instrument import InstrumentRef
from app.providers.ccxt_quirks.base import ExchangeQuirk

#: 이 거래소가 실제로 마감하는 일봉 경계. `daily_boundary` 설정과 어긋나면 경고한다.
NATIVE_DAILY_BOUNDARY = "UTC00"


class UpbitQuirk(ExchangeQuirk):
    """`market['id']`가 정확히 우리 표기(`KRW-BTC`)라 역방향은 그대로 읽으면 된다.

    정방향은 조립한다 — `markets_by_id`를 쓰려면 `load_markets()`가 선행돼야 하는데,
    심볼 변환이 네트워크 상태에 의존하게 만들 이유가 없다.
    """

    def to_exchange_symbol(self, instrument: InstrumentRef) -> str:
        quote, sep, base = instrument.symbol.partition("-")
        if not (sep and quote and base):
            raise ValueError(
                f"업비트 심볼은 '결제통화-기초자산' 형식이어야 합니다 "
                f"(받은 값: {instrument.symbol!r}). 예: 'KRW-BTC'"
            )
        return f"{base.upper()}/{quote.upper()}"

    def to_venue_symbol(self, market: dict[str, object]) -> str:
        return str(market["id"])
