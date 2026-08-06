"""MarketCalendar — 거래 시간 (ARCHITECTURE.md 3.2).

KRX는 09:00-15:30 KST, 미국은 09:30-16:00 America/New_York.
미국 시장은 서머타임 때문에 고정 오프셋으로 계산하면 안 되므로 항상 ZoneInfo를 쓴다.

**휴장일·조기폐장은 `exchange_calendars`에서 온다** (Phase 2). 손으로 관리하면
언젠가 어긋나고, 어긋난 날 `as_of`가 틀린 봉을 가리켜 **없는 세션의 신호가 난다.**

두 구현이 공존한다.

| 구현 | 휴장일 | 쓰는 곳 |
| :--- | :--- | :--- |
| `ExchangeSessionCalendar` | 실제 값 (조기폐장 포함) | **운영 기본값** (`build_calendars`) |
| `SessionCalendar` | 주말만 | 테스트·오프라인 — 네트워크도 패키지 데이터도 안 탄다 |
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.market.timeframe import duration, is_intraday, normalize

UTC = ZoneInfo("UTC")


class CalendarRangeError(ValueError):
    """캘린더가 아는 범위 밖의 시각을 물었을 때.

    조용히 None을 돌려주면 "오늘은 장이 없다"와 구분되지 않아서, 패키지가 낡아
    미래를 모르는 상황이 **휴장으로 오해**된다.
    """


class MarketCalendar(ABC):
    """거래 시간 판정. as_of 계산의 단일 출처."""

    id: str
    tz: ZoneInfo

    @abstractmethod
    def is_open(self, t: datetime) -> bool:
        """t(UTC) 시점에 시장이 열려 있는가."""

    @abstractmethod
    def last_closed_bar(self, now: datetime, timeframe: str) -> datetime | None:
        """가장 최근에 **마감된** 봉의 종료 시각(UTC).

        미완성 봉으로 신호를 판단하면 지표가 흔들려 신호가 생겼다 사라지므로,
        Item.as_of는 반드시 이 값을 쓴다 (ARCHITECTURE.md 4.4).
        """


class SessionCalendar(MarketCalendar):
    """장 운영시간이 있는 시장(주식)의 공통 구현."""

    def __init__(
        self,
        cal_id: str,
        tz: str,
        open_time: time,
        close_time: time,
        holidays: frozenset[date] = frozenset(),
    ) -> None:
        self.id = cal_id
        self.tz = ZoneInfo(tz)
        self.open_time = open_time
        self.close_time = close_time
        self.holidays = holidays

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def session(self, d: date) -> tuple[datetime, datetime] | None:
        """해당 날짜의 (개장, 폐장) UTC 구간. 휴장일이면 None."""
        if not self.is_trading_day(d):
            return None
        start = datetime.combine(d, self.open_time, tzinfo=self.tz)
        end = datetime.combine(d, self.close_time, tzinfo=self.tz)
        return start.astimezone(UTC), end.astimezone(UTC)

    def is_open(self, t: datetime) -> bool:
        t = _as_utc(t)
        local_date = t.astimezone(self.tz).date()
        # 자정 근처를 넘나드는 경우를 대비해 전후 하루를 함께 본다
        for offset in (-1, 0, 1):
            sess = self.session(local_date + timedelta(days=offset))
            if sess and sess[0] <= t < sess[1]:
                return True
        return False

    def last_closed_bar(self, now: datetime, timeframe: str) -> datetime | None:
        tf = normalize(timeframe)
        now = _as_utc(now)
        step = duration(tf)

        if not is_intraday(tf):
            # 일/주봉: 마지막으로 폐장한 세션의 종료 시각
            local_date = now.astimezone(self.tz).date()
            for offset in range(0, 30):
                sess = self.session(local_date - timedelta(days=offset))
                if sess and sess[1] <= now:
                    return sess[1]
            return None

        # 분/시간봉: 세션 안에서 개장 시각부터 step 간격으로 마감된다
        local_date = now.astimezone(self.tz).date()
        for offset in range(0, 30):
            sess = self.session(local_date - timedelta(days=offset))
            if sess is None:
                continue
            session_open, session_close = sess
            if now < session_open:
                continue  # 아직 열리기 전 → 더 과거 세션으로
            limit = min(now, session_close)
            elapsed = (limit - session_open) // step
            if elapsed >= 1:
                return session_open + elapsed * step
            # 세션이 열렸지만 첫 봉도 아직 안 닫혔다면 직전 세션의 마지막 봉
        return None


class ExchangeSessionCalendar(MarketCalendar):
    """`exchange_calendars` 백엔드. 휴장일·조기폐장이 실제 값으로 들어온다.

    `SessionCalendar`와 달리 개·폐장 시각을 **세션마다** 묻는다. 미국의 조기폐장
    (추수감사절 다음 날 13:00)이나 한국의 수능일 지연 개장처럼, 고정 시각으로는
    표현할 수 없는 날이 실제로 있기 때문이다.
    """

    def __init__(self, cal_id: str, code: str) -> None:
        self.id = cal_id
        self.code = code
        self._cal = _load_calendar(code)
        self.tz = ZoneInfo(str(self._cal.tz))

    def _guard(self, t: datetime) -> pd.Timestamp:
        ts = pd.Timestamp(_as_utc(t))
        first, last = self._cal.first_session, self._cal.last_session
        day = ts.tz_convert(None).normalize()
        if day < first or day > last:
            raise CalendarRangeError(
                f"{self.code} 캘린더가 아는 범위 밖입니다: {ts.date()} "
                f"(범위 {first.date()} ~ {last.date()}). "
                f"미래 쪽이면 `uv sync`로 exchange_calendars를 갱신하세요."
            )
        return ts

    def is_open(self, t: datetime) -> bool:
        ts = self._guard(t)
        return bool(self._cal.is_open_on_minute(ts))

    def _sessions_around(self, ts: pd.Timestamp, days: int) -> pd.DatetimeIndex:
        """`ts` 언저리의 세션들. exchange_calendars는 tz-naive 날짜만 받는다.

        끝을 이틀 넉넉히 잡는 이유는 세션 **날짜**가 UTC 날짜와 다를 수 있어서다 —
        한국 세션 8/4는 UTC로 8/3 밤에 시작한 것처럼 보인다. 넘치는 세션은 아래에서
        `close <= ts`로 걸러지므로 미래를 보지 않는다.
        """
        day = ts.tz_convert(None).normalize()
        first, last = self._cal.first_session, self._cal.last_session
        start = max(day - pd.Timedelta(days=days), first)
        end = min(day + pd.Timedelta(days=2), last)
        return self._cal.sessions_in_range(start, end)

    def last_closed_bar(self, now: datetime, timeframe: str) -> datetime | None:
        tf = normalize(timeframe)
        ts = self._guard(now)

        if not is_intraday(tf):
            # 가장 최근에 **폐장한** 세션. 장중이면 어제 세션이 답이다.
            for session in reversed(self._sessions_around(ts, 40)):
                close = self._cal.session_close(session)
                if close <= ts:
                    return close.to_pydatetime().astimezone(UTC)
            return None

        step = duration(tf)
        for session in reversed(self._sessions_around(ts, 10)):
            opened = self._cal.session_open(session)
            closed = self._cal.session_close(session)
            if ts < opened:
                continue
            elapsed = (min(ts, closed) - opened) // step
            if elapsed >= 1:
                return (opened + elapsed * step).to_pydatetime().astimezone(UTC)
        return None

    def session_close(self, day: date) -> datetime | None:
        """그 날짜 세션의 폐장 시각(UTC). 휴장일이면 None.

        Provider가 일봉의 **마감 시각**을 만들 때 쓴다. 소스는 대개 날짜만 주는데
        이 저장소의 인덱스 규약은 마감 시각이라(규칙 15) 변환이 필요하고, 그
        변환의 단일 출처가 여기여야 한다.
        """
        ts = pd.Timestamp(day)
        first, last = self._cal.first_session, self._cal.last_session
        if ts < first or ts > last or not self._cal.is_session(ts):
            return None
        return self._cal.session_close(ts).to_pydatetime().astimezone(UTC)

    def regular_close(self, day: date) -> pd.Timestamp:
        """정규 폐장 시각. 캘린더가 휴장이라고 보는 날의 **대체값**이다.

        소스가 봉을 줬는데 우리 캘린더가 휴장이라고 하면 둘 중 하나가 틀린 것이다.
        어느 쪽이든 **가격은 실재했으므로** 봉을 버리지 않는다 — 시각이 조금
        어긋나는 것보다 지표에 구멍이 뚫리는 쪽이 훨씬 나쁘다.
        """
        close_time = self._cal.close_times[-1][1]
        naive = pd.Timestamp(datetime.combine(day, close_time))
        return naive.tz_localize(self.tz).tz_convert("UTC")


@lru_cache(maxsize=8)
def _load_calendar(code: str):  # noqa: ANN202 - exchange_calendars 내부 타입
    """캘린더 생성은 수백 ms 걸린다. 프로세스당 한 번만 만든다."""
    return xcals.get_calendar(code)


def krx_calendar(holidays: frozenset[date] = frozenset()) -> SessionCalendar:
    """한국거래소 — 09:00~15:30 KST."""
    return SessionCalendar("krx", "Asia/Seoul", time(9, 0), time(15, 30), holidays)


def us_equity_calendar(holidays: frozenset[date] = frozenset()) -> SessionCalendar:
    """미국 정규장 — 09:30~16:00 America/New_York (서머타임 자동 반영)."""
    return SessionCalendar(
        "us_equity", "America/New_York", time(9, 30), time(16, 0), holidays
    )


#: calendar_id → exchange_calendars 코드.
EXCHANGE_CODES = {"krx": "XKRX", "us_equity": "XNYS"}


def build_calendars() -> dict[str, MarketCalendar]:
    """calendar_id → 구현체. 실제 휴장일이 든 캘린더를 쓴다."""
    return {
        "krx": ExchangeSessionCalendar("krx", "XKRX"),
        "us_equity": ExchangeSessionCalendar("us_equity", "XNYS"),
    }


def build_offline_calendars() -> dict[str, MarketCalendar]:
    """휴장일을 모르는 캘린더 구성. **테스트 전용.**

    주말만 제외하므로 공휴일에도 세션이 열린 것으로 계산된다. 운영에 쓰면
    없는 세션의 신호가 난다.
    """
    return {
        "krx": krx_calendar(),
        "us_equity": us_equity_calendar(),
    }


def session_date(bar_time: datetime, calendar: MarketCalendar) -> date:
    """봉의 **세션 날짜**. 차트의 x축과 마커가 함께 쓰는 단일 출처다.

    ★ `bar_time`은 세션의 **마감** 시각이라(규칙 15) 그대로 날짜를 떼면 하루
    어긋날 수 있다 — 미국 8/3 세션의 마감은 UTC 8/3 20:00인데, 이것을 KST로
    옮기면 8/4 새벽이라 한국 시간대로 날짜를 떼면 하루 밀린다. 1초를 빼서
    세션 **안쪽**으로 들어간 뒤 그 시장의 시간대로 옮기면 두 경우가 함께 맞는다.

    차트의 x축과 마커가 이 함수 하나를 공유해야 봉과 마커가 같은 날에 앉는다.
    """
    return (_as_utc(bar_time) - timedelta(seconds=1)).astimezone(calendar.tz).date()


def _as_utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않습니다. 모든 시각은 tz-aware UTC여야 합니다.")
    return t.astimezone(UTC)
