"""심볼·캘린더·Bundle 계약 테스트.

네트워크 없이 synthetic 소스로만 돈다. 파이프라인 자체는 `test_pipeline.py`가 본다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.engine.types import Bundle, Item, empty_ohlcv
from app.market.calendar import krx_calendar, us_equity_calendar
from app.market.instrument import InstrumentRef
from app.market.timeframe import TIMEFRAMES

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- 심볼
def test_instrument_parse_and_key():
    inst = InstrumentRef.parse("krx:005930")
    assert inst.venue == "krx"
    assert inst.quote_currency == "KRW"
    assert inst.key == "krx:005930"


def test_instrument_rejects_bare_symbol():
    with pytest.raises(ValueError, match="venue:symbol"):
        InstrumentRef.parse("005930")


def test_instrument_rejects_unknown_venue():
    """코인을 걷어낸 뒤에도 옛 표기가 조용히 통과하면 안 된다."""
    with pytest.raises(ValueError, match="upbit"):
        InstrumentRef.parse("upbit:KRW-BTC")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("krx:005930", "KRW"), ("nasdaq:AAPL", "USD"), ("nyse:KO", "USD")],
)
def test_quote_currency_comes_from_venue(raw: str, expected: str):
    """주식은 venue가 통화를 고정한다 (3.7). 통화가 틀리면 표기와 비교가 함께 어긋난다."""
    assert InstrumentRef.parse(raw).quote_currency == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("krx:005930", "krx"), ("nasdaq:AAPL", "us"), ("nyse:KO", "us")],
)
def test_market_groups_nasdaq_and_nyse_together(raw: str, expected: str):
    """랭킹 풀의 어휘다 (규칙 17). nasdaq과 nyse를 나눌 이유가 없다."""
    assert InstrumentRef.parse(raw).market == expected


# ------------------------------------------------------------------------- 캘린더
def test_krx_calendar_closed_on_weekend():
    cal = krx_calendar()
    saturday = datetime(2026, 3, 14, 5, 0, tzinfo=UTC)  # KST 14:00 토요일
    assert not cal.is_open(saturday)


def test_us_calendar_handles_dst():
    """서머타임 전후로 개장 시각(UTC)이 한 시간 달라져야 한다 (규칙 6)."""
    cal = us_equity_calendar()
    before = cal.session(datetime(2026, 3, 5, tzinfo=UTC).date())
    after = cal.session(datetime(2026, 3, 12, tzinfo=UTC).date())
    assert before and after
    assert before[0].hour == 14 and after[0].hour == 13


def test_timeframe_types_are_not_frozen():
    """정책 계층에서만 막는다 — 타입을 Literal["1d"]로 굳히면 되돌릴 수 없다 (규칙 12).

    설정에서 타임프레임이 사라졌어도(`config.TIMEFRAME` 상수) 캘린더의 분봉 계산은
    그대로 살아 있어야 한다. 지우면 Phase 5에서 되돌리는 것이 재설계가 된다.
    """
    assert "1h" in TIMEFRAMES
    assert krx_calendar().last_closed_bar(NOW, "1h") is not None


# -------------------------------------------------------------------------- Bundle
def _item(symbol: str, timeframe: str = "1d") -> Item:
    return Item(
        instrument=InstrumentRef.parse(symbol),
        timeframe=timeframe,
        as_of=NOW,
        ohlcv=empty_ohlcv(),
    )


def test_bundle_merge_keeps_timeframes_apart():
    daily = _item("krx:005930", "1d")
    hourly = replace(daily, timeframe="1h")

    merged = Bundle.merge([Bundle([daily]), Bundle([hourly])])

    assert len(merged) == 2
    assert {it.timeframe for it in merged.items} == {"1d", "1h"}


def test_bundle_filter_preserves_ohlcv():
    """필터는 `items`만 걸러내고 DataFrame을 버리지 않는다 (규칙 4)."""
    bundle = Bundle([_item("krx:005930"), _item("krx:000660")])

    kept = bundle.filter(lambda it: it.instrument.symbol == "005930")

    assert len(kept) == 1
    assert kept.items[0].ohlcv is not None


def test_empty_bundle_is_not_an_error():
    """빈 Bundle은 정상 출력이다 (4.1)."""
    assert Bundle([]).is_empty
    assert len(Bundle([])) == 0


def test_as_of_is_tz_aware():
    """저장은 항상 tz-aware UTC다 (규칙 5)."""
    assert _item("krx:005930").as_of.tzinfo is not None
