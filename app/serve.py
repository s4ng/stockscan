"""스케줄 루프 — `serve`의 상주 부분 (ARCHITECTURE.md 8장).

**새 동작은 없다.** 손으로 치던 명령을 시각에 맞춰 부를 뿐이고, 부르는 것은
`app/service.py`다 — 스케줄이 부른 실행과 사람이 부른 실행이 같아야 한다.

⚠️ **상주 프로세스의 값은 하트비트로 치른다.** APScheduler를 기각했던 이유
("프로세스가 죽으면 스케줄도 같이 죽는다")가 여기에 그대로 적용된다. 조용히
죽으면 알림이 안 오는데, **알림이 안 오는 것과 신호가 0건인 것이 구분되지 않는다.**
그래서 신호가 0건이어도 하루 1회 생존 신고를 보낸다.

**여기가 `allow_alerts`를 켜는 유일한 자리다** (12.2). 사람이 손으로 부른 실행은
터미널이든 화면의 버튼이든 알림을 보내지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app import service
from app.alerts import AlertChannel, Delivery
from app.cli import pipeline_file
from app.schedule import Schedule, ScheduleEntry, moments_around
from app.schemas.pipeline import PipelineSpec

#: 다음 발화까지 남았어도 이만큼마다 깨어난다.
#:
#: 설정 파일이 바뀌었을 수도, 서머타임이 넘어갔을 수도 있다. 계산한 시각까지
#: 통째로 잠들면 그 변화를 **다음 발화 뒤에야** 알게 된다.
POLL_SECONDS = 60


@dataclass
class Fire:
    """한 번의 발화 기록. 화면과 하트비트가 이걸 읽는다."""

    at: datetime
    label: str
    ok: bool
    detail: str
    signals: int = 0


@dataclass
class SchedulerState:
    """지금 무엇을 기다리고 있는가. 대시보드가 그대로 띄운다."""

    schedule: Schedule | None = None
    error: str | None = None
    next_fire: datetime | None = None
    next_heartbeat: datetime | None = None
    history: list[Fire] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)
    started_at: datetime | None = None
    skipped_on_start: list[str] = field(default_factory=list)
    """시작 시각보다 앞서 있던 오늘의 슬롯. **몰아서 부르지 않고 건너뛴다.**"""

    @property
    def last_fire(self) -> Fire | None:
        return self.history[-1] if self.history else None

    def record(self, fire: Fire, keep: int = 50) -> None:
        self.history.append(fire)
        del self.history[:-keep]


RunFn = Callable[..., Awaitable[service.RunOutcome]]


class Scheduler:
    """시각을 재고 실행을 부른다. **잠드는 것과 판단하는 것을 갈라 놓았다** —
    `due_*`는 순수 계산이라 시간을 흘려보내지 않고 테스트할 수 있다."""

    def __init__(
        self,
        channel: AlertChannel,
        state: SchedulerState | None = None,
        *,
        load_spec: Callable[[], PipelineSpec] = pipeline_file.load,
        run: RunFn = service.execute_run,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.channel = channel
        self.state = state or SchedulerState()
        self._load_spec = load_spec
        self._run = run
        self._clock = clock
        #: 마지막으로 판단한 시각. 발화 조건은 **"이 시각과 지금 사이를 지났는가"**다.
        #:
        #: ★ "지금이 슬롯 시각을 지났는가"로 두면 **저녁에 서버를 켠 순간 그날의 슬롯이
        #: 전부 한꺼번에 발화한다.** 각각이 `--commit`이라 봉을 몰아서 소비하고,
        #: 그건 되돌릴 수 없다 (3.5 / 규칙 11).
        self._since: datetime | None = None

    # ------------------------------------------------------------------ 계산
    def refresh(self, now: datetime) -> Schedule | None:
        """설정을 다시 읽어 스케줄을 갱신한다. 실패는 상태에 남기고 루프는 계속 돈다."""
        try:
            spec = self._load_spec()
            schedule = Schedule.from_spec(spec)
        except Exception as exc:  # noqa: BLE001 - 설정이 깨져도 프로세스는 살아 있어야 한다
            self.state.error = f"설정을 읽지 못했습니다 — {exc}"
            self.state.schedule = None
            return None

        self.state.error = None if schedule else "설정에 scheduleTrigger 노드가 없습니다."
        self.state.schedule = schedule
        if schedule:
            upcoming = schedule.next_fire(now)
            self.state.next_fire = upcoming[0] if upcoming else None
            self.state.next_heartbeat = schedule.next_heartbeat(now)
        return schedule

    def due(self, schedule: Schedule, now: datetime) -> list[ScheduleEntry]:
        """직전 판단 이후 **지금까지 사이에** 지나온 슬롯.

        첫 판단에서는 아무것도 돌려주지 않는다 — 켜자마자 오늘 지난 슬롯을 몰아서
        부르면 봉을 한꺼번에 소비한다. 건너뛴 것은 `skipped_on_start`에 남긴다.
        """
        if self._since is None:
            return []
        return [e for e in schedule.entries if self._crossed(e.at, schedule.tz, now)]

    def heartbeat_due(self, schedule: Schedule, now: datetime) -> bool:
        if schedule.heartbeat is None or self._since is None:
            return False
        return self._crossed(schedule.heartbeat, schedule.tz, now)

    def _crossed(self, at: Any, tz: Any, now: datetime) -> bool:
        assert self._since is not None
        return any(self._since < moment <= now for moment in moments_around(at, now, tz))

    # ------------------------------------------------------------------ 실행
    async def tick(self, now: datetime | None = None) -> None:
        """한 번의 판단. 루프가 이것을 반복해서 부른다."""
        now = now or self._clock()
        schedule = self.refresh(now)
        if schedule is None:
            return

        if self._since is None:
            # 첫 판단: 기준선만 잡고 오늘 이미 지난 슬롯은 건너뛴다는 사실을 남긴다.
            self.state.skipped_on_start = [
                e.label()
                for e in schedule.entries
                if now.astimezone(schedule.tz).time() >= e.at
            ]

        for entry in self.due(schedule, now):
            await self._fire(entry, now)

        if self.heartbeat_due(schedule, now):
            await self._heartbeat(now)

        self._since = now

    async def _fire(self, entry: ScheduleEntry, now: datetime) -> None:
        try:
            spec = self._load_spec()
            if entry.market:
                spec, _ = pipeline_file.filter_by_market(spec, entry.market)
        except Exception as exc:  # noqa: BLE001
            self.state.record(Fire(now, entry.label(), ok=False, detail=f"설정 오류 — {exc}"))
            return

        warnings: list[str] = []
        # ★ 화면의 버튼과 **같은 잠금**을 쓴다. 사람이 누른 실행과 스케줄이 겹치면
        #   같은 봉을 두 번 소비한다 (3.5).
        async with service.run_lock():
            try:
                outcome = await self._run(
                    spec,
                    commit=True,  # 스케줄 실행은 기록이 목적이다
                    allow_alerts=True,  # ★ 알림이 열리는 유일한 자리 (12.2)
                    warn=warnings.append,
                )
            except Exception as exc:  # noqa: BLE001 - 하루치 실패로 프로세스를 죽이지 않는다
                self.state.record(Fire(now, entry.label(), ok=False, detail=str(exc)))
                await self._send(f"⚠️ 실행 실패 [{entry.label()}] — {exc}")
                return
            service.write_report(outcome, spec, warnings.append)

        fire = Fire(
            now,
            entry.label(),
            ok=True,
            detail=f"신호 {outcome.written}건" + (f" · 경고 {len(warnings)}건" if warnings else ""),
            signals=outcome.written,
        )
        self.state.record(fire)
        if outcome.written:
            await self._send(_signal_message(entry, outcome))

    async def _heartbeat(self, now: datetime) -> None:
        """★ 신호가 0건이어도 보낸다.

        이게 없으면 어느 날부터 시스템이 죽어 있었는지 알 수 없다. 12.3이
        "신호 0건과 실패를 구분한다"고 정한 것이 프로세스 수준에서 반복되는 것이다.
        """
        last = self.state.last_fire
        today = [f for f in self.state.history if f.at.date() == now.date()]
        signals = sum(f.signals for f in today)
        stamp = now.astimezone(_tz(self.state)).strftime("%m-%d %H:%M")
        tail = f"{last.label} {last.detail}" if last else "(아직 없음)"
        await self._send(
            f"✅ marketscan 살아 있습니다 ({stamp})\n"
            f"오늘 실행 {len(today)}회 · 신호 {signals}건\n"
            f"마지막: {tail}"
        )

    async def _send(self, text: str) -> None:
        delivery = await self.channel.send(text)
        self.state.deliveries.append(delivery)
        del self.state.deliveries[:-50]

    # ------------------------------------------------------------------ 루프
    async def run_forever(self, poll_seconds: int = POLL_SECONDS) -> None:
        self.state.started_at = self._clock()
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 루프는 어떤 이유로도 죽지 않는다
                self.state.error = f"스케줄 루프 오류 — {exc}"
            await asyncio.sleep(poll_seconds)


def _tz(state: SchedulerState) -> Any:
    return state.schedule.tz if state.schedule else UTC


def _signal_message(entry: ScheduleEntry, outcome: service.RunOutcome) -> str:
    lines = [f"📈 신호 {outcome.written}건 [{entry.label()}]"]
    for signal in outcome.signals[:10]:
        features = signal.get("features") or {}
        lines.append(
            f"· {signal['instrument']} {signal.get('display_name') or ''}"
            f" — {features.get('rank_pool') or '-'} {features.get('rank') or '-'}위"
        )
    if outcome.written > 10:
        lines.append(f"… 외 {outcome.written - 10}건")
    return "\n".join(lines)
