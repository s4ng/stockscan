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
from datetime import UTC, datetime, time
from itertools import zip_longest
from typing import Any

from app import config as app_config
from app import service
from app.alerts import AlertChannel, Delivery
from app.config import AppConfig
from app.core.formatting import format_price_change
from app.schedule import Schedule, ScheduleEntry, is_weekend, moments_around
from app.storage import db

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

    last_evaluation: Any | None = None
    """마지막 사후 수익률 평가 결과. 하트비트가 이걸 함께 보고한다."""

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
        load_config: Callable[[], AppConfig] = app_config.load,
        run: RunFn = service.execute_run,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.channel = channel
        self.state = state or SchedulerState()
        self._load_config = load_config
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
            config = self._load_config()
            schedule = Schedule.from_config(config)
        except Exception as exc:  # noqa: BLE001 - 설정이 깨져도 프로세스는 살아 있어야 한다
            self.state.error = f"설정을 읽지 못했습니다 — {exc}"
            self.state.schedule = None
            return None

        self.state.error = None if schedule else "설정에 schedule.at이 비어 있습니다."
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
        """직전 판단과 지금 사이에 이 시각을 지났는가. **주말 슬롯은 지나도 안 친다.**

        ⚠️ 주말 판정을 `now`가 아니라 **슬롯 시각(`moment`)**으로 한다. 판단은 슬롯보다
        최대 `POLL_SECONDS`만큼 늦게 오므로, 자정 직전 슬롯이면 `now`가 이미 다음 날이다.
        """
        assert self._since is not None
        return any(
            self._since < moment <= now and not is_weekend(moment)
            for moment in moments_around(at, now, tz)
        )

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
            # ★ 성적표는 하트비트 시각에 함께 나간다 — 발화 시각을 하나 더 두면
            #   "그날 몇 시에 뭐가 오는지"를 사람이 또 외워야 한다.
            if _scorecard_due(schedule, now):
                await self._send_scorecard(now)

        self._since = now

    async def _fire(self, entry: ScheduleEntry, now: datetime) -> None:
        try:
            config = self._load_config()
        except Exception as exc:  # noqa: BLE001
            self.state.record(Fire(now, entry.label(), ok=False, detail=f"설정 오류 — {exc}"))
            return

        warnings: list[str] = []
        # ★ 화면의 버튼과 **같은 잠금**을 쓴다. 사람이 누른 실행과 스케줄이 겹치면
        #   같은 봉을 두 번 소비한다 (3.5).
        async with service.run_lock():
            try:
                outcome = await self._run(
                    config,
                    commit=True,  # 스케줄 실행은 기록이 목적이다
                    allow_alerts=True,  # ★ 알림이 열리는 유일한 자리 (12.2)
                    warn=warnings.append,
                )
            except Exception as exc:  # noqa: BLE001 - 하루치 실패로 프로세스를 죽이지 않는다
                self.state.record(Fire(now, entry.label(), ok=False, detail=str(exc)))
                await self._send(f"⚠️ 실행 실패 [{entry.label()}] — {exc}")
                return
            service.write_report(outcome, config, warnings.append)
            # ★ 봉을 새로 받은 직후가 사후 수익률을 채우기 가장 좋은 시점이다 —
            #   외부 호출이 없고(캐시만 읽는다) 이 실행이 방금 캐시를 넓혔다 (4.8).
            await self._fill_forward_returns()

        fire = Fire(
            now,
            entry.label(),
            ok=True,
            detail=f"신호 {outcome.written}건" + (f" · 경고 {len(warnings)}건" if warnings else ""),
            signals=outcome.written,
        )
        self.state.record(fire)
        if outcome.written:
            # ★ 알림이 자기 성적을 달고 나간다 — 받는 순간 "이걸 얼마나 믿어야
            #   하나"가 같이 와야 한다. 근거 없는 명령만 오면 알림을 보지 않게 된다.
            record = await self._recent_record(outcome)
            await self._send(_signal_message(entry, outcome, record))

    async def _recent_record(self, outcome: service.RunOutcome) -> str:
        """이 전략의 최근 성적 한 줄. 표본이 적으면 **빈 문자열**(지어내지 않는다)."""
        from app import scorecard as sc

        strategy = (outcome.signals[0].get("strategy_id") if outcome.signals else None) or ""
        if not strategy:
            return ""
        try:
            async with db.session_scope() as session:
                return sc.render_inline(await sc.signal_count(session, strategy))
        except Exception:  # noqa: BLE001 - 성적을 못 붙여도 알림은 나가야 한다
            return ""

    async def _send_scorecard(self, now: datetime) -> None:
        """한 달에 한 번 오는 성적표 (4.8).

        일일 알림이 답하지 못하는 것 — "내가 정한 규칙이 실제로 어땠는가" — 에 답한다.
        """
        from app import scorecard as sc

        try:
            async with db.session_scope() as session:
                card = await sc.build(session, now=now)
        except Exception as exc:  # noqa: BLE001 - 집계 실패로 하루치를 버리지 않는다
            self.state.error = f"성적표 생성 실패 — {exc}"
            return
        await self._send(sc.render(card))

    async def _fill_forward_returns(self) -> None:
        """신호의 사후 수익률을 채운다 (4.8). **실패해도 실행을 실패로 만들지 않는다.**

        채점은 판단이 아니라 사후 집계다 — 여기서 터뜨리면 이미 끝난 실행(되돌릴 수
        없는 봉 소비까지 포함해)이 실패로 기록된다.
        """
        from app.evaluate import evaluate as run_evaluate

        try:
            async with db.session_scope() as session:
                report = await run_evaluate(session)
        except Exception as exc:  # noqa: BLE001 - 집계 실패로 하루치를 버리지 않는다
            self.state.error = f"사후 수익률 평가 실패 — {exc}"
            return
        self.state.last_evaluation = report

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

        lines = [
            f"✅ stockscan 살아 있습니다 ({stamp})",
            f"오늘 실행 {len(today)}회 · 신호 {signals}건",
            f"마지막: {tail}",
        ]
        # ⚠️ **봉이 끊겨 채우지 못한 종목은 하트비트에 싣는다.** 조용히 두면
        #    성적표의 분모가 손실 쪽만 빠진 채로 굳는다 (규칙 18 / 4.8).
        missing = getattr(self.state.last_evaluation, "missing_bars", None)
        if missing:
            lines.append(
                f"⚠️ 봉이 끊겨 사후 수익률을 못 채운 종목 {len(missing)}개 — "
                f"`stockscan ingest --commit`"
            )
        await self._send("\n".join(lines))

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


def _scorecard_due(schedule: Schedule, now: datetime) -> bool:
    """오늘이 성적표 보내는 날인가.

    ⚠️ 하트비트와 함께 나가므로 **하루에 한 번만** 판정된다 — 하트비트가 이미
    "직전 판단과 지금 사이를 지났는가"로 걸러져 있어 중복 발송이 없다.

    ★ **성적표 날이 주말이면 다음 발송일로 민다.** 주말에는 하트비트가 건너뛰므로
    `day == 오늘`로 두면 **그 달치가 통째로 사라진다.** 성적표는 이 프로젝트의
    제품이고(§4.8), 한 달에 한 번뿐이라 한 번 놓치면 그만큼이 그냥 없어진다.
    """
    day = schedule.scorecard_day
    if not day:
        return False
    local = now.astimezone(schedule.tz)
    if local.day < day:
        return False
    # 성적표 날부터 어제까지가 **전부 건너뛴 날**일 때만 오늘이 그 자리다.
    # (평일이 하루라도 끼어 있었다면 그날 이미 나갔다.)
    at = schedule.heartbeat or time(0, 0)
    return all(
        is_weekend(datetime.combine(local.date().replace(day=d), at, tzinfo=schedule.tz))
        for d in range(day, local.day)
    )


def _tz(state: SchedulerState) -> Any:
    return state.schedule.tz if state.schedule else UTC


def _signal_message(
    entry: ScheduleEntry, outcome: service.RunOutcome, record: str = ""
) -> str:
    """알림 본문.

    ★ **종목명과 순위만으로는 행동으로 이어지지 않는다.** 모르는 종목의 등수를 받으면
    할 수 있는 다음 행동이 없다. 그래서 값(종가·등락)과 근거(전략이 남긴 feature)를
    함께 싣고, 되짚을 수 있게 `explain` 명령을 그대로 적어 준다.
    """
    strategy = (outcome.signals[0].get("strategy_id") if outcome.signals else None) or "-"
    lines = [f"📈 {strategy} — 신호 {outcome.written}건 [{entry.label()}]", ""]

    for signal, signal_id in zip_longest(outcome.signals[:10], outcome.signal_ids[:10]):
        if signal is None:
            break
        name = signal.get("display_name") or signal["instrument"]
        price = format_price_change(signal.get("close"), signal.get("change_pct"))
        head = f"· {name} ({signal['instrument']})"
        lines.append(f"{head}  {price}" if price.strip() else head)

        reason = _reason(signal.get("features") or {})
        if reason:
            lines.append(f"   {reason}")
        if signal_id is not None:
            lines.append(f"   stockscan explain {signal_id}")

    if outcome.written > 10:
        lines.append(f"… 외 {outcome.written - 10}건")
    return "\n".join(lines)


#: 알림에 싣지 않는 feature. 순위 관련 값은 아래에서 따로 한 줄로 만든다.
_RANK_KEYS = {"rank", "rank_pool", "universe_size", "percentile"}


def _reason(features: dict[str, Any]) -> str:
    """"왜 떴는가"를 한 줄로. 순위 + 전략이 남긴 값 두어 개.

    전략마다 feature 이름이 다르므로 **여기서 이름을 알지 못한다** — 알려고 들면
    전략이 늘 때마다 이 함수를 고쳐야 하고, 고치는 것을 잊으면 알림이 조용히
    빈약해진다. 그래서 순위만 이름으로 집고 나머지는 앞에서부터 싣는다.
    """
    parts: list[str] = []
    rank = features.get("rank")
    pool, size = features.get("rank_pool"), features.get("universe_size")
    if rank is not None:
        parts.append(f"{pool or '?'} {rank}위" + (f"/{size}" if size else ""))

    for key, value in features.items():
        if key in _RANK_KEYS or len(parts) >= 4:
            continue
        if isinstance(value, float):
            parts.append(f"{key}={value:.4g}")
        elif isinstance(value, (int, str, bool)):
            parts.append(f"{key}={value}")
    return " · ".join(parts)
