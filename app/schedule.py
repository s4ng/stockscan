"""스케줄 — 언제 실행할 것인가 (ARCHITECTURE.md 8장).

**시각은 로컬 기준으로 적는다.** 마감 시각에서 자동으로 유도하지 않는 이유는
서머타임이다 — 미국장 마감은 한국 시각으로 1년에 두 번 한 시간씩 움직이는데,
유도한 값은 그 사실을 **어디에도 남기지 않는다.** 사람이 적어 두면 전환 때
확인할 수 있다.

⚠️ **2026-08-06부터 슬롯이 시장을 갖지 않는다.** 예전에는 `market: krx`를 달아
그 시장만 돌렸는데, Fresh Bar Gate(3.5)가 어차피 새로 마감된 봉이 없는 시장을
제외하므로 **두 번 거르는 것**이었다. 시각만 적으면 그 시각에 봉이 있는 시장이
알아서 판정된다.

★ **주말에는 발화도 하트비트도 하지 않는다** (2026-08-07, 설정 항목이 아니라 기본값).
장이 서지 않는 날의 실행은 Fresh Bar Gate에 걸려 어차피 0건인데, 하트비트만은
그대로 나가 이틀에 한 번꼴로 "볼 것 없는 알림"이 된다. 그걸 흘려보내기 시작하면
정작 봐야 할 평일 알림도 같이 흘려보게 된다.

여기 있는 것은 **계산뿐이고 잠들지 않는다.** 루프(`app/serve.py`)와 갈라 놓아야
"다음 발화가 언제인가"를 시간을 흘려보내지 않고 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import AppConfig

UTC = ZoneInfo("UTC")

#: 다음 발화를 찾을 때 훑는 날 범위(오늘 기준). 주말을 건너뛰므로 **금요일 밤에는
#: 월요일까지(+3) 봐야** 한다. `+1`까지만 보면 그 자리에서 "다음 발화 없음"이 된다.
_AHEAD = range(-1, 5)

#: 발화 판정(어제·오늘·내일)에 쓰는 범위. 자정을 넘는 경계만 보면 되므로 좁다.
_AROUND = range(-1, 2)


def is_weekend(moment: datetime) -> bool:
    """이 시각이 주말인가. 그렇다면 발화도 하트비트도 건너뛴다.

    ★ **판정은 로컬이 아니라 UTC로 한다.** 설정의 `timezone`(예: Asia/Seoul) 요일로
    자르면 한국에서 가장 흔한 슬롯인 **"토요일 06:10"** — 미국장 **금요일** 마감
    직후 — 이 통째로 사라진다. 금요일 미국 신호가 조용히 안 나가는 것이라
    "주말엔 조용하다"가 아니라 "주중 하루치를 잃는다"가 된다.

    UTC가 자연스러운 자인 이유는 **세계 시장의 한 주가 UTC 월~금에 담기기**
    때문이다 — KRX 금요일장은 금 00:00~06:30 UTC, 미국 금요일장은 금 13:30~21:00 UTC다.
    """
    return moment.astimezone(UTC).weekday() >= 5


class ScheduleError(ValueError):
    """스케줄 선언이 잘못됐을 때. 조용히 넘어가면 **하루 종일 아무것도 안 돈다.**"""


@dataclass(frozen=True)
class ScheduleEntry:
    at: time

    def label(self) -> str:
        return self.at.strftime("%H:%M")


@dataclass(frozen=True)
class Schedule:
    entries: tuple[ScheduleEntry, ...]
    timezone: str
    heartbeat: time | None = None
    """하루 1회 생존 신고 시각. **없으면 죽은 것과 신호 0건이 구분되지 않는다** (8장)."""

    scorecard_day: int | None = None
    """★ 성적표를 보낼 날 (매월 N일). 하트비트 시각에 함께 나간다."""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def next_fire(self, after: datetime) -> tuple[datetime, ScheduleEntry] | None:
        """`after` **이후**의 가장 가까운 실행 시각과 그 항목.

        경계를 `>`로 두는 것이 중요하다 — `>=`면 방금 실행한 시각이 다시 잡혀
        같은 슬롯이 무한히 반복된다.
        """
        candidates = [
            (moment, entry)
            for entry in self.entries
            for moment in _moments(entry.at, after, self.tz, _AHEAD)
            if moment > after and not is_weekend(moment)
        ]
        return min(candidates, default=None, key=lambda pair: pair[0])

    def next_heartbeat(self, after: datetime) -> datetime | None:
        if self.heartbeat is None:
            return None
        moments = [
            m
            for m in _moments(self.heartbeat, after, self.tz, _AHEAD)
            if m > after and not is_weekend(m)
        ]
        return min(moments, default=None)

    def describe(self) -> list[str]:
        rows = [e.label() for e in self.entries]
        if self.heartbeat:
            rows.append(f"{self.heartbeat.strftime('%H:%M')} [하트비트] 신호 0건이어도 보냅니다")
        if self.scorecard_day:
            rows.append(f"매월 {self.scorecard_day}일 [성적표] 사후 수익률·승률·기저율")
        # 조용히 건너뛰면 "주말에 왜 안 돌았지"가 된다. 여기 적어 두면 `describe`가 답한다.
        rows.append("주말(UTC 토·일)에는 건너뜁니다 — 토요일 새벽 미국장 슬롯은 그대로 돕니다")
        return rows

    @classmethod
    def from_config(cls, config: AppConfig) -> Schedule | None:
        """설정의 `schedule:`에서 읽는다. 시각이 하나도 없으면 None."""
        if not config.schedule.at:
            return None
        return cls(
            entries=tuple(ScheduleEntry(at=t) for t in config.schedule.at),
            timezone=config.timezone,
            heartbeat=config.schedule.heartbeat,
            scorecard_day=config.schedule.scorecard_day,
        )


def moments_around(at: time, near: datetime, tz: ZoneInfo) -> list[datetime]:
    """어제·오늘·내일의 해당 시각(UTC). 자정을 넘는 경계 판정에 쓴다."""
    return _moments(at, near, tz, _AROUND)


def _moments(at: time, after: datetime, tz: ZoneInfo, span: range) -> list[datetime]:
    """`after`의 로컬 날짜를 기준으로 `span`일만큼의 해당 시각(UTC).

    ★ **날마다 새로 만든다.** 하루를 24시간으로 더하면 서머타임 전환 날에 한 시간
    어긋나고, 그 어긋남이 "마감 전에 돌아 어제 봉으로 판정하는" 사고가 된다.
    """
    local_day = after.astimezone(tz).date()
    return [
        datetime.combine(local_day + timedelta(days=offset), at, tzinfo=tz).astimezone(UTC)
        for offset in span
    ]


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()
