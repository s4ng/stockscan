"""캘린더 회귀 테스트 (ARCHITECTURE.md 3.2 / Phase 2).

**서머타임과 휴장일이 이 프로젝트에서 조용히 틀리는 대표 경로다.** 규칙 6이
고정 오프셋(UTC-5)을 금지하는 이유가 여기 있고, 손으로 관리하는 휴장일 목록은
언젠가 어긋나서 **없는 세션의 신호**를 만든다.

네트워크를 타지 않는다 — exchange_calendars는 패키지에 데이터를 들고 있다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.market.calendar import (
    CalendarRangeError,
    Crypto24x7Calendar,
    ExchangeSessionCalendar,
    build_calendars,
)

UTC = ZoneInfo("UTC")


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


@pytest.fixture(scope="module")
def krx() -> ExchangeSessionCalendar:
    return ExchangeSessionCalendar("krx", "XKRX")


@pytest.fixture(scope="module")
def us() -> ExchangeSessionCalendar:
    return ExchangeSessionCalendar("us_equity", "XNYS")


# --------------------------------------------------------------------- 서머타임
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 2026 DST 시작은 3/8. 직전 금요일은 EST(-5) → 21:00Z 마감
        ("2026-03-07T00:00:00+00:00", "2026-03-06T21:00:00+00:00"),
        # 직후 월요일은 EDT(-4) → 20:00Z 마감. **한국 기준 개장 시각이 1시간 움직인다**
        ("2026-03-10T00:00:00+00:00", "2026-03-09T20:00:00+00:00"),
        # 2026 DST 종료는 11/1
        ("2026-10-31T00:00:00+00:00", "2026-10-30T20:00:00+00:00"),
        ("2026-11-03T00:00:00+00:00", "2026-11-02T21:00:00+00:00"),
    ],
)
def test_us_close_follows_daylight_saving(us: ExchangeSessionCalendar, now: str, expected: str):
    """규칙 6 — 고정 오프셋을 쓰면 이 테스트가 깨진다."""
    assert us.last_closed_bar(at(now), "1d") == at(expected)


def test_us_early_close_is_honoured(us: ExchangeSessionCalendar):
    """추수감사절 다음 날은 13:00 ET에 닫는다. 고정 시각으로는 표현할 수 없다."""
    assert us.last_closed_bar(at("2026-11-27T19:00:00+00:00"), "1d") == at(
        "2026-11-27T18:00:00+00:00"
    )


# ----------------------------------------------------------------------- 휴장일
@pytest.mark.parametrize("holiday", ["2026-01-01", "2026-02-17", "2025-08-15"])
def test_krx_holidays_are_not_sessions(krx: ExchangeSessionCalendar, holiday: str):
    """신정·설날·광복절. 주말만 거르는 캘린더는 이 날을 세션으로 본다."""
    assert krx.session_close(datetime.fromisoformat(holiday).date()) is None


def test_holiday_pushes_as_of_back_to_the_previous_session(krx: ExchangeSessionCalendar):
    """광복절(2025-08-15 금) 휴장 → 다음 월요일 장중의 마지막 마감은 8/14 목요일이다."""
    assert krx.last_closed_bar(at("2025-08-18T01:00:00+00:00"), "1d") == at(
        "2025-08-14T06:30:00+00:00"
    )


def test_krx_closes_at_1530_kst(krx: ExchangeSessionCalendar):
    assert krx.last_closed_bar(at("2026-07-31T23:00:00+00:00"), "1d") == at(
        "2026-07-31T06:30:00+00:00"
    )


def test_session_not_closed_yet_returns_the_previous_one(krx: ExchangeSessionCalendar):
    """KST 8/4 08:00은 개장 전이다. 8/4 봉은 아직 없다."""
    assert krx.last_closed_bar(at("2026-08-03T23:00:00+00:00"), "1d") == at(
        "2026-08-03T06:30:00+00:00"
    )


def test_intraday_bars_stay_inside_the_session(krx: ExchangeSessionCalendar):
    """분봉은 판단에 못 쓰지만(규칙 12) 캘린더 분기는 살아 있어야 한다 (3.6)."""
    bar = krx.last_closed_bar(at("2026-07-31T02:30:00+00:00"), "1h")

    assert bar == at("2026-07-31T02:00:00+00:00")


# ------------------------------------------------------------------------ 범위
def test_out_of_range_is_an_error_not_a_holiday(krx: ExchangeSessionCalendar):
    """조용히 None을 주면 "패키지가 낡았다"가 "오늘은 장이 없다"로 오해된다."""
    with pytest.raises(CalendarRangeError, match="exchange_calendars"):
        krx.last_closed_bar(at("2099-01-01T00:00:00+00:00"), "1d")


# ------------------------------------------------------------------------ 코인
def test_crypto_never_closes():
    """24/7이라 캘린더 분기가 없다 — Phase 1이 업비트로 좁힌 이유다."""
    crypto = Crypto24x7Calendar("UTC00")

    assert crypto.is_open(at("2026-01-01T03:00:00+00:00"))
    assert crypto.last_closed_bar(at("2026-08-02T13:00:00+00:00"), "1d") == at(
        "2026-08-02T00:00:00+00:00"
    )


def test_crypto_daily_boundary_is_configurable():
    """KST00은 UTC 15:00이다. 코인 신호 전체가 이 경계에 좌우된다 (11장 1번)."""
    kst = Crypto24x7Calendar("KST00")

    assert kst.last_closed_bar(at("2026-08-02T13:00:00+00:00"), "1d") == at(
        "2026-08-01T15:00:00+00:00"
    )


# ------------------------------------------------------------ 혼합 파이프라인 (3.5)
def test_one_now_yields_a_different_as_of_per_market():
    """★ Fresh Bar Gate가 시장별 마감을 알아서 걸러 주는 근거다.

    같은 `ctx.now`에 코인·한국·미국이 서로 다른 봉을 가리킨다. 사용자는 캘린더를
    신경 쓸 필요가 없다.
    """
    calendars = build_calendars("UTC00")
    now = at("2026-08-02T13:00:00+00:00")  # 일요일

    bars = {cid: cal.last_closed_bar(now, "1d") for cid, cal in calendars.items()}

    assert bars["crypto24x7"] == at("2026-08-02T00:00:00+00:00")  # 코인은 오늘 봉
    assert bars["krx"] == at("2026-07-31T06:30:00+00:00")  # 금요일 마감
    assert bars["us_equity"] == at("2026-07-31T20:00:00+00:00")  # 금요일 마감
    assert len(set(bars.values())) == 3
