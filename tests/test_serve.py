"""스케줄·하트비트 (ARCHITECTURE.md 8장).

여기서 지키는 것은 넷이다.

  1. ★ **같은 슬롯을 두 번 부르지 않는다** — 겹친 `--commit` 둘은 같은 봉을 두 번 소비한다
  2. ★ **신호가 0건이어도 하루 1회 하트비트를 보낸다** — 없으면 죽은 것과 구분되지 않는다
  3. ★ **알림은 스케줄 실행에서만 열린다** (`allow_alerts=True`는 여기 하나뿐)
  4. **하루치 실패로 프로세스가 죽지 않는다** — 다음 슬롯은 계속 돌아야 한다
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app import service
from app.alerts import LogChannel
from app.config import AppConfig, ScheduleConfig
from app.schedule import Schedule, ScheduleEntry
from app.serve import Scheduler, _scorecard_due
from tests.conftest import make_config

KST = ZoneInfo("Asia/Seoul")

#: ⚠️ 슬롯이 시장을 갖지 않는다 (2026-08-06). Fresh Bar Gate가 새로 마감된 봉이
#: 없는 시장을 어차피 제외하므로 두 번 거를 이유가 없었다.
CONFIG = make_config(
    schedule=ScheduleConfig(at=[time(15, 40), time(9, 10)], heartbeat=time(9, 0))
)


class FakeOutcome:
    def __init__(self, written: int) -> None:
        self.written = written
        self.signals: list[dict[str, Any]] = [
            {
                "instrument": "krx:005930",
                "display_name": "삼성전자",
                "close": 80000,
                "change_pct": 0.0234,
                "strategy_id": "trend_breakout_55",
                "features": {"rank": 1, "rank_pool": "krx", "trend_strength": 3.409},
            }
        ] * written
        self.signal_ids = list(range(1, written + 1))
        self.result = None
        self.committed = True


def make_scheduler(*, written: int = 0, fail: bool = False, config: AppConfig = CONFIG):
    calls: list[dict[str, Any]] = []

    async def fake_run(loaded_config, **kwargs):
        calls.append(kwargs)
        if fail:
            raise RuntimeError("소스가 죽었습니다")
        return FakeOutcome(written)

    channel = LogChannel()
    scheduler = Scheduler(channel, load_config=lambda: config, run=fake_run)
    return scheduler, channel, calls


@pytest.fixture(autouse=True)
def _no_report(monkeypatch: pytest.MonkeyPatch):
    """리포트 쓰기는 여기서 볼 것이 아니다 (파일 시스템을 건드리지 않는다)."""
    monkeypatch.setattr(service, "write_report", lambda *a, **k: None)


# ------------------------------------------------------------------- 스케줄 계산
def test_next_fire_picks_the_closest_upcoming_slot():
    schedule = Schedule.from_config(CONFIG)
    now = datetime(2026, 8, 4, 3, 0, tzinfo=UTC)  # 12:00 KST

    moment, entry = schedule.next_fire(now)

    assert entry.at == time(15, 40)
    assert moment.astimezone(KST).hour == 15


def test_next_fire_rolls_over_to_tomorrow_after_the_last_slot():
    schedule = Schedule.from_config(CONFIG)
    now = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)  # 18:00 KST — 오늘 슬롯은 끝났다

    moment, entry = schedule.next_fire(now)

    assert entry.at == time(9, 10)
    assert moment.astimezone(KST).date() == datetime(2026, 8, 5).date()


def test_schedule_survives_dst_by_rebuilding_each_day():
    """★ 하루를 24시간으로 더하면 서머타임 날 한 시간 어긋난다.

    어긋나면 마감 **전에** 돌아 어제 봉으로 판정한다.
    """
    schedule = Schedule(
        entries=(ScheduleEntry(at=time(16, 10)),),
        timezone="America/New_York",
    )
    before = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)  # 전환 전 금요일
    after = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)  # 전환 뒤 월요일

    first, _ = schedule.next_fire(before)
    second, _ = schedule.next_fire(after)

    # 로컬 시각은 그대로 16:10이어야 한다 (UTC 오프셋이 바뀐다).
    assert first.astimezone(ZoneInfo("America/New_York")).hour == 16
    assert second.astimezone(ZoneInfo("America/New_York")).hour == 16
    assert first.hour != second.hour  # UTC로는 옮겨졌다


def test_next_fire_jumps_over_the_weekend_to_monday():
    """주말을 건너뛰므로 금요일 밤의 '다음 발화'는 **월요일**이다.

    훑는 날 범위가 +1까지면 여기서 "다음 발화 없음"이 되고, 화면이 스케줄을
    잃어버린 것처럼 보인다.
    """
    schedule = Schedule.from_config(CONFIG)
    friday_evening = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)  # 금 18:00 KST — 오늘 슬롯 끝

    moment, _ = schedule.next_fire(friday_evening)

    assert moment.astimezone(KST).date() == datetime(2026, 8, 10).date()  # 월요일


def test_empty_schedule_yields_nothing_to_run():
    """시각이 하나도 없으면 스케줄이 없다. `serve`가 시작할 때 그 사실을 알린다."""
    empty = make_config(schedule=ScheduleConfig(at=[], heartbeat=None))
    assert Schedule.from_config(empty) is None


# ----------------------------------------------------------------------- 발화
@pytest.mark.asyncio
async def test_slot_fires_exactly_once_when_crossed():
    """★ 같은 슬롯을 두 번 부르면 같은 봉을 두 번 소비한다 (3.5)."""
    scheduler, _, calls = make_scheduler()
    before = datetime(2026, 8, 4, 6, 30, tzinfo=UTC)  # 15:30 KST — 아직 전
    after = datetime(2026, 8, 4, 6, 45, tzinfo=UTC)  # 15:45 KST — 15:40을 지났다

    await scheduler.tick(before)  # 기준선
    await scheduler.tick(after)  # 여기서 발화
    await scheduler.tick(after.replace(minute=50))
    await scheduler.tick(after.replace(hour=7))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_starting_late_does_not_replay_the_days_slots():
    """★ 저녁에 서버를 켜면 그날 지난 슬롯이 **한꺼번에** 돌면 안 된다.

    각각이 `--commit`이라 봉을 몰아서 소비하고, 그건 되돌릴 수 없다.
    """
    scheduler, _, calls = make_scheduler()
    evening = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)  # 21:00 KST — 두 슬롯 다 지났다

    await scheduler.tick(evening)
    await scheduler.tick(evening.replace(minute=1))

    assert calls == []
    assert scheduler.state.skipped_on_start == ["09:10", "15:40"]


@pytest.mark.asyncio
async def test_scheduled_run_commits_and_opens_alerts():
    """★ 알림이 열리는 유일한 자리다 (12.2). 화면·터미널은 열지 않는다."""
    scheduler, _, calls = make_scheduler()

    await scheduler.tick(datetime(2026, 8, 4, 6, 30, tzinfo=UTC))
    await scheduler.tick(datetime(2026, 8, 4, 6, 45, tzinfo=UTC))

    assert calls[0]["commit"] is True
    assert calls[0]["allow_alerts"] is True


@pytest.mark.asyncio
async def test_signals_are_sent_but_zero_signals_are_not():
    """0건마다 알림이 오면 알림을 보지 않게 된다. 그 자리는 하트비트가 맡는다."""
    quiet, quiet_channel, _ = make_scheduler(written=0)
    loud, loud_channel, _ = make_scheduler(written=3)
    before = datetime(2026, 8, 4, 6, 30, tzinfo=UTC)
    now = datetime(2026, 8, 4, 6, 45, tzinfo=UTC)

    for scheduler in (quiet, loud):
        await scheduler.tick(before)
    await quiet.tick(now)
    await loud.tick(now)

    assert quiet_channel.sent == []
    assert "신호 3건" in loud_channel.sent[0].text


@pytest.mark.asyncio
async def test_the_alert_carries_price_and_reason_not_just_a_rank():
    """★ 종목명과 등수만으로는 행동으로 이어지지 않는다.

    이 알림을 받고 할 수 있는 다음 행동이 있어야 한다 — 값(종가·등락)과 근거,
    그리고 되짚을 수단(`explain`)이 함께 실려야 한다.
    """
    scheduler, channel, _ = make_scheduler(written=1)

    await scheduler.tick(datetime(2026, 8, 4, 6, 30, tzinfo=UTC))
    await scheduler.tick(datetime(2026, 8, 4, 6, 45, tzinfo=UTC))

    text = channel.sent[0].text
    assert "삼성전자" in text
    assert "80,000" in text  # 값
    assert "trend_strength" in text  # 근거
    assert "krx 1위" in text
    assert "stockscan explain 1" in text  # 되짚을 수단


@pytest.mark.asyncio
async def test_a_failed_run_alerts_and_keeps_the_loop_alive():
    scheduler, channel, _ = make_scheduler(fail=True)

    await scheduler.tick(datetime(2026, 8, 4, 6, 30, tzinfo=UTC))
    await scheduler.tick(datetime(2026, 8, 4, 6, 45, tzinfo=UTC))

    assert scheduler.state.last_fire.ok is False
    assert "실행 실패" in channel.sent[0].text
    # 다음 슬롯은 여전히 잡혀 있다 — 하루치 실패로 죽지 않는다.
    assert scheduler.state.next_fire is not None


# --------------------------------------------------------------------- 하트비트
@pytest.mark.asyncio
async def test_heartbeat_is_sent_even_with_zero_signals():
    """★ 이게 없으면 '신호가 없는 것'과 '프로세스가 죽은 것'이 구분되지 않는다."""
    scheduler, channel, _ = make_scheduler(written=0)
    await scheduler.tick(datetime(2026, 8, 3, 23, 50, tzinfo=UTC))  # 08:50 KST
    await scheduler.tick(datetime(2026, 8, 4, 0, 5, tzinfo=UTC))  # 09:05 KST

    assert any("살아 있습니다" in d.text for d in channel.sent)


@pytest.mark.asyncio
async def test_heartbeat_is_sent_once_a_day():
    scheduler, channel, _ = make_scheduler()
    await scheduler.tick(datetime(2026, 8, 3, 23, 50, tzinfo=UTC))
    await scheduler.tick(datetime(2026, 8, 4, 0, 5, tzinfo=UTC))
    await scheduler.tick(datetime(2026, 8, 4, 1, 0, tzinfo=UTC))

    assert sum(1 for d in channel.sent if "살아 있습니다" in d.text) == 1


# ------------------------------------------------------------------------ 주말
@pytest.mark.asyncio
async def test_weekend_slots_do_not_fire():
    """장이 서지 않는 날에는 돌지 않는다 (설정이 아니라 기본값)."""
    scheduler, _, calls = make_scheduler(written=3)
    before = datetime(2026, 8, 8, 6, 30, tzinfo=UTC)  # 토 15:30 KST
    after = datetime(2026, 8, 8, 6, 45, tzinfo=UTC)  # 토 15:40을 지났다

    await scheduler.tick(before)
    await scheduler.tick(after)

    assert calls == []


@pytest.mark.asyncio
async def test_no_heartbeat_on_the_weekend():
    """★ 주말 하트비트를 흘려보게 되면 **평일 하트비트도 같이** 흘려보게 된다."""
    scheduler, channel, _ = make_scheduler()
    await scheduler.tick(datetime(2026, 8, 7, 23, 50, tzinfo=UTC))  # 토 08:50 KST
    await scheduler.tick(datetime(2026, 8, 8, 0, 5, tzinfo=UTC))  # 토 09:05 KST

    assert channel.sent == []


@pytest.mark.asyncio
async def test_saturday_dawn_slot_still_fires_because_it_is_fridays_us_session():
    """★ 주말 판정이 **UTC 기준**인 이유.

    한국에서 가장 흔한 슬롯인 "토요일 06:10"은 미국장 **금요일** 마감 직후다.
    로컬 요일로 잘랐다면 금요일 미국 신호가 통째로 조용히 사라진다.
    """
    config = make_config(schedule=ScheduleConfig(at=[time(6, 10)], heartbeat=None))
    scheduler, _, calls = make_scheduler(written=2, config=config)
    # 토 06:10 KST = 금 21:10 UTC — UTC로는 아직 거래 주 안이다.
    await scheduler.tick(datetime(2026, 8, 7, 21, 0, tzinfo=UTC))
    await scheduler.tick(datetime(2026, 8, 7, 21, 15, tzinfo=UTC))

    assert len(calls) == 1


def test_a_weekend_scorecard_day_slides_to_the_next_weekday():
    """★ 성적표 날이 주말이면 그 달치가 사라진다 — 다음 발송일로 민다.

    2026-08-01은 토요일이다. 하트비트가 건너뛰므로 그날은 못 나가고, 월요일(3일)에
    나가야 한다. 그 뒤(4일)에 또 나가면 안 된다.
    """
    schedule = Schedule(
        entries=(ScheduleEntry(at=time(15, 40)),),
        timezone="Asia/Seoul",
        heartbeat=time(9, 0),
        scorecard_day=1,
    )
    at_kst = lambda day: datetime(2026, 8, day, 0, 5, tzinfo=UTC)  # noqa: E731 - 09:05 KST

    assert _scorecard_due(schedule, at_kst(3)) is True  # 월요일 — 밀려서 여기
    assert _scorecard_due(schedule, at_kst(4)) is False  # 이미 나갔다
    assert _scorecard_due(schedule, at_kst(31)) is False


# ------------------------------------------------------------------------ 상태
@pytest.mark.asyncio
async def test_broken_config_is_reported_but_does_not_crash():
    def broken() -> AppConfig:
        raise ValueError("설정 파일이 깨졌습니다")

    scheduler = Scheduler(LogChannel(), load_config=broken)

    await scheduler.tick(datetime(2026, 8, 4, 6, 45, tzinfo=UTC))

    assert "설정을 읽지 못했습니다" in scheduler.state.error


@pytest.mark.asyncio
async def test_a_config_without_a_schedule_says_so():
    """조용히 넘어가면 하루 종일 아무것도 안 돈다."""
    empty = make_config(schedule=ScheduleConfig(at=[], heartbeat=None))
    scheduler = Scheduler(LogChannel(), load_config=lambda: empty)

    await scheduler.tick(datetime(2026, 8, 4, 6, 45, tzinfo=UTC))

    assert "schedule.at" in scheduler.state.error
