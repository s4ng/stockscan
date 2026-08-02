"""거래소별 예외 처리 (ARCHITECTURE.md 3.3).

CCXT를 쓰는 이유가 거래소 통합이므로 `UpbitProvider` / `BinanceProvider`처럼
파일을 쪼개지 않는다. 대신 **통합 표기로 흡수되지 않는 차이만** 여기로 뺀다.
"""

from __future__ import annotations

from app.market.instrument import VENUES, QuoteStyle
from app.providers.ccxt_quirks.base import ExchangeQuirk
from app.providers.ccxt_quirks.upbit import UpbitQuirk

_QUIRKS: dict[str, ExchangeQuirk] = {
    "upbit": UpbitQuirk(),
}


def quirk_for(exchange_id: str) -> ExchangeQuirk:
    """거래소 id에 맞는 quirk. 등록되지 않았으면 기본 동작을 쓴다."""
    quirk = _QUIRKS.get(exchange_id)
    if quirk is not None:
        return quirk

    # 등록되지 않은 거래소가 dash 표기를 쓰면 심볼이 **조용히** 어긋난다 — 잘못된
    # 심볼로 조회하면 빈 결과가 오고, 빈 결과는 "그 종목은 봉이 없다"와 구분되지
    # 않는다. 통합 표기를 쓰는 거래소만 기본 동작으로 통과시킨다.
    spec = VENUES.get(exchange_id)
    if spec is not None and spec.quote_style is not QuoteStyle.SLASH_SUFFIX:
        raise NotImplementedError(
            f"{exchange_id}의 심볼 표기({spec.quote_style})는 CCXT 통합 표기와 다릅니다. "
            f"app/providers/ccxt_quirks/에 quirk를 추가하세요 (upbit.py 참조)."
        )
    return ExchangeQuirk()


__all__ = ["ExchangeQuirk", "UpbitQuirk", "quirk_for"]
