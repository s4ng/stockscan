"""RunContext — 실행 컨텍스트 (ARCHITECTURE.md 4.2).

노드는 `datetime.now()`를 직접 호출하지 않는다. 모든 시각은 여기서 주입된다.
이 규칙이 백테스트와 실거래를 같은 코드 경로로 묶는 유일한 장치다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.engine.state import BarStateStore, InMemoryBarState
from app.market.calendar import MarketCalendar, build_calendars
from app.market.instrument import InstrumentRef
from app.providers.registry import ProviderRegistry, default_registry
from app.schemas.pipeline import ExecutionMode, PipelineSettings

UTC = ZoneInfo("UTC")


@dataclass
class LogRecord:
    level: str
    message: str
    node_id: str | None = None


@dataclass
class NodeLogger:
    """노드가 남긴 메시지. node_runs에 저장되어 사후 재현에 쓰인다."""

    records: list[LogRecord] = field(default_factory=list)
    node_id: str | None = None

    def bind(self, node_id: str) -> NodeLogger:
        return NodeLogger(records=self.records, node_id=node_id)

    def info(self, message: str) -> None:
        self.records.append(LogRecord("info", message, self.node_id))

    def warning(self, message: str) -> None:
        self.records.append(LogRecord("warning", message, self.node_id))

    def error(self, message: str) -> None:
        self.records.append(LogRecord("error", message, self.node_id))

    def for_node(self, node_id: str) -> list[LogRecord]:
        return [r for r in self.records if r.node_id == node_id]


class FutureAccessError(RuntimeError):
    """백테스트 중 `ctx.now` 이후 데이터에 접근하려 했을 때."""


@dataclass
class RunContext:
    run_id: str
    mode: ExecutionMode
    now: datetime
    """이 실행이 기준으로 삼는 시각 (tz-aware UTC). 노드는 이 값만 써야 한다."""

    settings: PipelineSettings
    providers: ProviderRegistry
    calendars: dict[str, MarketCalendar]
    bar_state: BarStateStore = field(default_factory=InMemoryBarState)
    """Fresh Bar Gate가 참조하는 직전 as_of 저장소."""

    log: NodeLogger = field(default_factory=NodeLogger)
    node_id: str | None = None
    """현재 실행 중인 노드. bind()로 주입된다."""

    @property
    def user_tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.user_timezone)

    @property
    def is_backtest(self) -> bool:
        return self.mode is ExecutionMode.BACKTEST

    @property
    def sends_alerts(self) -> bool:
        """shadow 모드는 signals에만 기록하고 외부로 알림을 보내지 않는다."""
        return self.mode not in (ExecutionMode.BACKTEST, ExecutionMode.SHADOW)

    def calendar_for(self, instrument: InstrumentRef) -> MarketCalendar:
        return self.calendars[instrument.calendar_id]

    def assert_not_future(self, ts: datetime, what: str = "데이터") -> None:
        """미래 참조(look-ahead) 방어선. Provider와 노드 양쪽에서 호출한다."""
        if ts > self.now:
            raise FutureAccessError(
                f"{what}이(가) 현재 실행 시각을 넘어섰습니다: "
                f"{ts.isoformat()} > now={self.now.isoformat()}"
            )

    def to_user_tz(self, ts: datetime) -> datetime:
        return ts.astimezone(self.user_tz)

    def bind(self, node_id: str) -> RunContext:
        """노드 전용 로거를 붙인 사본. 상태 저장소는 실행 전체가 공유한다."""
        return RunContext(
            run_id=self.run_id,
            mode=self.mode,
            now=self.now,
            settings=self.settings,
            providers=self.providers,
            calendars=self.calendars,
            bar_state=self.bar_state,
            log=self.log.bind(node_id),
            node_id=node_id,
        )

    @classmethod
    def create(
        cls,
        settings: PipelineSettings | None = None,
        mode: ExecutionMode | None = None,
        now: datetime | None = None,
        providers: ProviderRegistry | None = None,
        run_id: str | None = None,
        bar_state: BarStateStore | None = None,
    ) -> RunContext:
        settings = settings or PipelineSettings()
        resolved_now = now or datetime.now(UTC)
        if resolved_now.tzinfo is None:
            raise ValueError("now는 tz-aware여야 합니다 (UTC 권장)")
        return cls(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            mode=mode or settings.default_mode,
            now=resolved_now.astimezone(UTC),
            settings=settings,
            providers=providers or default_registry(),
            calendars=build_calendars(settings.daily_boundary),
            bar_state=bar_state or InMemoryBarState(),
        )
