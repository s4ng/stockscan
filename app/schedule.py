"""스케줄 — 언제 실행할 것인가 (ARCHITECTURE.md 8장).

**시각은 로컬 기준으로 적고, 시장 마감과의 관계를 함께 적는다.** 마감 시각에서
자동으로 유도하지 않는 이유는 서머타임이다 — 미국장 마감은 한국 시각으로 1년에
두 번 한 시간씩 움직이는데, 유도한 값은 그 사실을 **화면 어디에도 남기지 않는다.**
사람이 적고 이유를 적어 두면 전환 때 확인할 수 있다.

여기 있는 것은 **계산뿐이고 잠들지 않는다.** 루프(`app/serve.py`)와 갈라 놓아야
"다음 발화가 언제인가"를 시간을 흘려보내지 않고 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.schemas.pipeline import PipelineSpec

UTC = ZoneInfo("UTC")

#: 스케줄을 선언하는 노드 타입. 파이프라인이 스케줄을 갖는다 — `serve`의 설정이
#: 아니라 파이프라인의 성질이라, 파일을 복사하면 스케줄도 함께 간다.
TRIGGER_TYPE = "scheduleTrigger"


class ScheduleError(ValueError):
    """스케줄 선언이 잘못됐을 때. 조용히 넘어가면 **하루 종일 아무것도 안 돈다.**"""


@dataclass(frozen=True)
class ScheduleEntry:
    at: time
    market: str | None = None
    note: str = ""

    def label(self) -> str:
        market = f" [{self.market}]" if self.market else ""
        return f"{self.at.strftime('%H:%M')}{market}"


@dataclass(frozen=True)
class Schedule:
    entries: tuple[ScheduleEntry, ...]
    timezone: str
    heartbeat: time | None = None
    """하루 1회 생존 신고 시각. **없으면 죽은 것과 신호 0건이 구분되지 않는다** (8장)."""

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
            for moment in _next_two(entry.at, after, self.tz)
            if moment > after
        ]
        return min(candidates, default=None, key=lambda pair: pair[0])

    def next_heartbeat(self, after: datetime) -> datetime | None:
        if self.heartbeat is None:
            return None
        moments = [m for m in _next_two(self.heartbeat, after, self.tz) if m > after]
        return min(moments, default=None)

    def describe(self) -> list[str]:
        rows = [f"{e.label()} — {e.note}" if e.note else e.label() for e in self.entries]
        if self.heartbeat:
            rows.append(f"{self.heartbeat.strftime('%H:%M')} [하트비트] 신호 0건이어도 보냅니다")
        return rows

    @classmethod
    def from_spec(cls, spec: PipelineSpec) -> Schedule | None:
        """파이프라인의 `scheduleTrigger` 노드에서 읽는다. 없으면 None."""
        nodes = [n for n in spec.nodes if n.type == TRIGGER_TYPE]
        if not nodes:
            return None
        if len(nodes) > 1:
            raise ScheduleError(
                f"{TRIGGER_TYPE} 노드가 {len(nodes)}개입니다. 하나만 두세요 — "
                f"여럿이면 어느 것이 도는지 알 수 없습니다."
            )
        return cls.from_params(nodes[0].params, default_timezone=spec.settings.user_timezone)

    @classmethod
    def from_params(cls, params: dict[str, Any], *, default_timezone: str) -> Schedule:
        raw_entries = params.get("at") or []
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ScheduleError(
                "스케줄에 `at`이 비어 있습니다. "
                '예: at: [{ time: "15:40", market: krx, note: "KRX 마감 뒤" }]'
            )
        entries = tuple(_parse_entry(item) for item in raw_entries)
        heartbeat = params.get("heartbeat")
        return cls(
            entries=entries,
            timezone=str(params.get("timezone") or default_timezone),
            heartbeat=_parse_time(heartbeat, "heartbeat") if heartbeat else None,
        )


# --------------------------------------------------------------------------- 내부
def _parse_entry(item: Any) -> ScheduleEntry:
    if isinstance(item, str):
        return ScheduleEntry(at=_parse_time(item, "at"))
    if not isinstance(item, dict):
        raise ScheduleError(f"스케줄 항목이 문자열도 매핑도 아닙니다: {item!r}")
    return ScheduleEntry(
        at=_parse_time(item.get("time"), "time"),
        market=str(item["market"]) if item.get("market") else None,
        note=str(item.get("note") or ""),
    )


def _parse_time(raw: Any, field: str) -> time:
    try:
        hour, _, minute = str(raw).partition(":")
        return time(int(hour), int(minute or 0))
    except (ValueError, AttributeError) as exc:
        raise ScheduleError(
            f"{field}의 시각 형식이 잘못됐습니다: {raw!r}. 24시간제 'HH:MM'으로 적으세요."
        ) from exc


def moments_around(at: time, near: datetime, tz: ZoneInfo) -> list[datetime]:
    """어제·오늘·내일의 해당 시각(UTC). 자정을 넘는 경계 판정에 쓴다."""
    return _next_two(at, near, tz)


def _next_two(at: time, after: datetime, tz: ZoneInfo) -> list[datetime]:
    """오늘과 내일의 해당 시각(UTC).

    ★ **날마다 새로 만든다.** 하루를 24시간으로 더하면 서머타임 전환 날에 한 시간
    어긋나고, 그 어긋남이 "마감 전에 돌아 어제 봉으로 판정하는" 사고가 된다.
    """
    local_day = after.astimezone(tz).date()
    return [
        datetime.combine(day, at, tzinfo=tz).astimezone(UTC)
        for day in (local_day - timedelta(days=1), local_day, local_day + timedelta(days=1))
    ]


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()
