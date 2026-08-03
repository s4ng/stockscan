"""사람이 읽는 숫자 표기.

CLI 표·HTML 리포트가 함께 쓴다. 두 곳에 각각 두면 같은 종목이 화면마다 다른
자릿수로 보이고, 그 차이가 값이 다른 것처럼 읽힌다.

★ **자릿수를 시장별로 박지 않고 크기에서 유도한다.** 한 유니버스에 삼성전자
239,500원과 알트코인 0.00000123 BTC가 함께 들어오는데(3.7 통화 보존), 소수점
2자리로 고정하면 코인이 전부 `0.00`으로 찌그러지고 정수로 고정하면 주식이
읽히지 않는다. `review`의 차트도 같은 문제를 만나므로(lightweight-charts의
`priceFormat`) 규칙은 한 곳에 둔다.
"""

from __future__ import annotations

#: 이 값 이상이면 소수점을 버린다. 주식·원화 코인이 여기 걸린다.
_INTEGER_FROM = 1000

#: 1 이상이면 두 자리. 미국 주식(308.91)이 여기다.
_DECIMAL_FROM = 1


def format_price(value: float | int | None) -> str:
    """가격 한 개. `239,500` · `308.91` · `0.00000123`."""
    if value is None:
        return ""
    magnitude = abs(float(value))
    if magnitude >= _INTEGER_FROM:
        return f"{value:,.0f}"
    if magnitude >= _DECIMAL_FROM:
        return f"{value:,.2f}"
    if magnitude == 0:
        return "0"
    # 1 미만은 유효숫자가 소수점 한참 뒤에 있다. 지수 표기(1.23e-06)는 시세로
    # 읽히지 않으므로 펴서 쓰고 꼬리 0만 턴다.
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def format_change(ratio: float | None) -> str:
    """등락률. `+2.34%` · `-0.51%`. 부호를 항상 붙인다."""
    if ratio is None:
        return ""
    return f"{ratio:+.2%}"


def format_price_change(value: float | int | None, ratio: float | None) -> str:
    """`239,500 (+2.34%)`. 등락률을 모르면 가격만."""
    price = format_price(value)
    if not price:
        return ""
    change = format_change(ratio)
    return f"{price} ({change})" if change else price
