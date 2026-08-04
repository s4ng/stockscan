"""RunContext — 실행 컨텍스트 (ARCHITECTURE.md 4.2).

노드는 `datetime.now()`를 직접 호출하지 않는다. 모든 시각은 여기서 주입된다.
이 규칙이 백테스트와 실거래를 같은 코드 경로로 묶는 유일한 장치다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.engine.signals import CollectingSink, SignalSink
from app.engine.state import BarStateStore, InMemoryBarState
from app.market.calendar import MarketCalendar, build_calendars
from app.market.instrument import InstrumentRef
from app.providers.ohlcv_source import DirectSource, OhlcvSource
from app.providers.registry import ProviderRegistry, default_registry
from app.providers.universe_source import DirectUniverse, UniverseSource
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
    pipeline_id: str
    now: datetime
    """이 실행이 기준으로 삼는 시각 (tz-aware UTC). 노드는 이 값만 써야 한다."""

    settings: PipelineSettings
    providers: ProviderRegistry
    calendars: dict[str, MarketCalendar]
    ohlcv: OhlcvSource | None = None
    """봉을 얻는 창구 (3.9). None이면 소스를 직접 부르는 구현이 들어간다.

    **노드는 `ctx.providers`가 아니라 이것을 쓴다.** 뒤에 `ohlcv_cache`가 있는지
    없는지는 노드의 관심사가 아니어야 캐시 계층을 갈아 끼울 수 있다.
    """

    universe: UniverseSource | None = None
    """종목 목록을 얻는 창구 (4.7). 위와 같은 이유로 노드에서 분리한다."""

    bar_state: BarStateStore = field(default_factory=InMemoryBarState)
    """Fresh Bar Gate가 참조하는 직전 as_of 저장소."""

    signals: SignalSink = field(default_factory=CollectingSink)
    """신호 배출구. 기본은 메모리에만 담는 dry-run용 배출구다."""

    commit: bool = False
    """부작용 허용 여부. **기본은 False(dry-run)다.**

    False면 알림을 보내지 않고, signals를 기록하지 않고, 봉도 소비하지 않는다.
    에이전트가 실수로 텔레그램을 쏘거나 봉을 삼키면 그 신호는 재실행에서 stale로
    걸러져 **영영 사라지기** 때문이다. 기본값이 안전한 쪽이어야 사고가 안 난다
    (ARCHITECTURE.md 12.2 / CLAUDE.md 규칙 11).
    """

    allow_alerts: bool = False
    """외부 알림 채널을 열어도 되는가. **CLI `run`은 절대 켜지 않는다.**

    단일 실행의 산출물은 **stdout과 정적 HTML 리포트**다. 텔레그램 같은 바깥으로
    나가는 전송은 상주 실행(`serve`)의 몫으로 미뤘다 — 사람이 손으로 돌려 보는
    실행과 자동으로 도는 실행은 오발송의 무게가 다르고, 손으로 돌릴 때마다
    채널로 메시지가 나가면 알림 자체를 신뢰하지 않게 된다.

    ⚠️ **이 값을 켜는 자리는 `app/serve.py`의 스케줄 발화 하나뿐이다.** 웹 UI의
    버튼도 켜지 않는다 — 화면에서 누른 것도 사람이 손으로 부른 실행이다.
    """

    log: NodeLogger = field(default_factory=NodeLogger)
    node_id: str | None = None
    """현재 실행 중인 노드. bind()로 주입된다."""

    def __post_init__(self) -> None:
        if self.ohlcv is None:
            self.ohlcv = DirectSource(self.providers, default_adjusted=self.settings.adjusted)
        if self.universe is None:
            self.universe = DirectUniverse(self.providers)

    @property
    def user_tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.user_timezone)

    @property
    def is_backtest(self) -> bool:
        return self.mode is ExecutionMode.BACKTEST

    @property
    def sends_alerts(self) -> bool:
        """외부로 알림을 내보내도 되는가.

        세 조건을 **모두** 만족해야 한다.
          1. `allow_alerts` — 상주 실행(`serve`)만 켠다. CLI `run`은 항상 False
          2. `--commit` — 부작용 옵트인 (규칙 11)
          3. 모드가 backtest·shadow가 아닐 것 — shadow는 signals에만 기록한다
        """
        return (
            self.allow_alerts
            and self.commit
            and self.mode not in (ExecutionMode.BACKTEST, ExecutionMode.SHADOW)
        )

    @property
    def persists_signals(self) -> bool:
        """signals 테이블에 기록해도 되는가. shadow는 기록하되 알림만 끈다."""
        return self.commit and self.mode is not ExecutionMode.BACKTEST

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
            pipeline_id=self.pipeline_id,
            now=self.now,
            settings=self.settings,
            providers=self.providers,
            calendars=self.calendars,
            ohlcv=self.ohlcv,
            universe=self.universe,
            bar_state=self.bar_state,
            signals=self.signals,
            commit=self.commit,
            allow_alerts=self.allow_alerts,
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
        ohlcv: OhlcvSource | None = None,
        universe: UniverseSource | None = None,
        run_id: str | None = None,
        pipeline_id: str = "",
        bar_state: BarStateStore | None = None,
        signals: SignalSink | None = None,
        commit: bool = False,
        allow_alerts: bool = False,
    ) -> RunContext:
        settings = settings or PipelineSettings()
        resolved_now = now or datetime.now(UTC)
        if resolved_now.tzinfo is None:
            raise ValueError("now는 tz-aware여야 합니다 (UTC 권장)")
        return cls(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            mode=mode or settings.default_mode,
            pipeline_id=pipeline_id,
            now=resolved_now.astimezone(UTC),
            settings=settings,
            providers=providers or default_registry(),
            calendars=build_calendars(settings.daily_boundary),
            ohlcv=ohlcv,
            universe=universe,
            bar_state=bar_state or InMemoryBarState(),
            signals=signals or CollectingSink(),
            commit=commit,
            allow_alerts=allow_alerts,
        )
