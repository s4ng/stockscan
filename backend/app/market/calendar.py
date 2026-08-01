"""MarketCalendar — 거래 시간 (ARCHITECTURE.md 3.2).

코인은 24/7, KRX는 09:00-15:30 KST, 미국은 09:30-16:00 America/New_York.
미국 시장은 서머타임 때문에 고정 오프셋으로 계산하면 안 되므로 항상 ZoneInfo를 쓴다.

TODO(Phase 2): 휴장일·조기폐장은 `exchange_calendars` 패키지에서 주입한다.
              지금은 주말만 제외하므로 공휴일에도 세션이 열린 것으로 계산된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.market.timeframe import duration, is_intraday, normalize

UTC = ZoneInfo("UTC")


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


class Crypto24x7Calendar(MarketCalendar):
    """암호화폐 — 항상 열려 있다. 일봉 경계만 정하면 된다.

    주의: KST 09:00은 UTC 00:00과 같은 순간이다. 따라서 의미 있는 선택지는
    'UTC00'(= KST 09:00, 업비트 일봉 기준)과 'KST00'(한국 자정 = UTC 15:00)이다.
    """

    id = "crypto24x7"
    tz = UTC

    #: 일봉 경계 → UTC 기준 시각(hour)
    BOUNDARIES = {"UTC00": 0, "KST00": 15}

    def __init__(self, daily_boundary: str = "UTC00") -> None:
        if daily_boundary not in self.BOUNDARIES:
            raise ValueError(
                f"daily_boundary는 {list(self.BOUNDARIES)} 중 하나여야 합니다 "
                f"(받은 값: {daily_boundary!r})"
            )
        self.daily_boundary = daily_boundary
        self._boundary_hour = self.BOUNDARIES[daily_boundary]

    def is_open(self, t: datetime) -> bool:
        return True

    def last_closed_bar(self, now: datetime, timeframe: str) -> datetime | None:
        tf = normalize(timeframe)
        now = _as_utc(now)
        if is_intraday(tf):
            step = duration(tf)
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            elapsed = (now - epoch) // step
            return epoch + elapsed * step
        # 일봉 이상: 경계 시각마다 마감
        boundary_today = now.replace(
            hour=self._boundary_hour, minute=0, second=0, microsecond=0
        )
        if boundary_today > now:
            boundary_today -= timedelta(days=1)
        if tf == "1w":
            # 주봉은 경계가 지난 첫 월요일에 마감
            back = (boundary_today.weekday() - 0) % 7
            boundary_today -= timedelta(days=back)
        return boundary_today


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


def krx_calendar(holidays: frozenset[date] = frozenset()) -> SessionCalendar:
    """한국거래소 — 09:00~15:30 KST."""
    return SessionCalendar("krx", "Asia/Seoul", time(9, 0), time(15, 30), holidays)


def us_equity_calendar(holidays: frozenset[date] = frozenset()) -> SessionCalendar:
    """미국 정규장 — 09:30~16:00 America/New_York (서머타임 자동 반영)."""
    return SessionCalendar(
        "us_equity", "America/New_York", time(9, 30), time(16, 0), holidays
    )


def build_calendars(daily_boundary: str = "UTC00") -> dict[str, MarketCalendar]:
    """calendar_id → 구현체."""
    return {
        "crypto24x7": Crypto24x7Calendar(daily_boundary),
        "krx": krx_calendar(),
        "us_equity": us_equity_calendar(),
    }


def _as_utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않습니다. 모든 시각은 tz-aware UTC여야 합니다.")
    return t.astimezone(UTC)
