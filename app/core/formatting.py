"""사람이 읽는 숫자 표기.

CLI 표·HTML 리포트가 함께 쓴다. 두 곳에 각각 두면 같은 종목이 화면마다 다른
자릿수로 보이고, 그 차이가 값이 다른 것처럼 읽힌다.

★ **자릿수를 시장별로 박지 않고 크기에서 유도한다.** 한 유니버스에 삼성전자
239,500원과 알트코인 0.00000123 BTC가 함께 들어오는데(3.7 통화 보존), 소수점
2자리로 고정하면 코인이 전부 `0.00`으로 찌그러지고 정수로 고정하면 주식이
읽히지 않는다. `backtest`의 차트도 같은 문제를 만나므로(lightweight-charts의
`priceFormat`) 규칙은 한 곳에 둔다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

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


# --------------------------------------------------------------------------- 시장
#: 시장별 국기. **키가 venue가 아니라 market이다** — `nasdaq`과 `nyse`를 나눌 이유가
#: 없는 것은 랭킹 풀과 같은 논리다 (규칙 17). venue로 키를 잡으면 거래소가 늘 때마다
#: 같은 국기를 한 줄씩 더 적게 되고, 언젠가 한쪽만 빠진다.
MARKET_FLAGS = {"krx": "🇰🇷", "us": "🇺🇸"}


def market_flag(venue: str | None) -> str:
    """venue가 속한 시장의 국기. 모르는 venue면 **빈 문자열**.

    ⚠️ **대체 기호(`🏳️`·`?`)를 넣지 않는다.** 국기는 목록을 한 눈에 시장별로 가르는
    장치인데, 정체 모를 기호가 섞이면 그 기능이 사라진다 — 없는 편이 낫다.
    벤치마크 지수(`market == "benchmark"`)가 여기 걸리는데, 알림에 실릴 일이
    없으므로 국기를 주지 않는 것이 맞다.
    """
    from app.market.instrument import VENUES

    spec = VENUES.get(venue or "")
    return MARKET_FLAGS.get(spec.market, "") if spec else ""


# --------------------------------------------------------------------------- 시각
DEFAULT_TIMEZONE = "Asia/Seoul"


def format_time(value: datetime | str | None, tz: str = DEFAULT_TIMEZONE) -> str:
    """UTC로 저장된 시각을 **표시용 지역 시각**으로 (`2026-08-03 15:30`).

    ★ **저장은 언제나 tz-aware UTC이고 변환은 표시할 때만 한다** (규칙 5).
    여기가 그 "표시할 때"의 유일한 지점이라, 화면마다 다른 시각이 보이는 일이 없다.

    오프셋(`+09:00`)은 붙이지 않는다 — 개인용 단일 사용자 도구라 표시 타임존이
    하나뿐이고, 매 행에 같은 접미사가 붙으면 표만 넓어진다. 대신 **열 이름에
    타임존을 적어** 어느 기준인지 한 번만 밝힌다.
    """
    if value is None:
        return ""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")


def timezone_label(tz: str = DEFAULT_TIMEZONE) -> str:
    """열 이름에 붙일 짧은 표기 (`KST`). 모르면 타임존 이름 그대로."""
    abbreviation = datetime.now(ZoneInfo(tz)).strftime("%Z")
    return abbreviation if abbreviation and not abbreviation.startswith("+") else tz
