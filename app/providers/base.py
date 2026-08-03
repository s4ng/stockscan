"""Provider 플러그인 인터페이스 (ARCHITECTURE.md 3.3).

각 Provider가 자기 인증 스키마(credential_schema)를 스스로 선언하므로,
새 소스를 추가할 때 프론트엔드를 건드릴 필요가 없다 —
Connections 화면의 폼이 이 스키마에서 자동 생성된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from app.market.instrument import InstrumentRef


@dataclass(frozen=True)
class RateLimitSpec:
    requests_per_second: float = 5.0
    burst: int = 10


@dataclass(frozen=True)
class ProviderCapabilities:
    """UI가 이 값을 읽어 선택 불가한 조합을 회색 처리한다."""

    timeframes: tuple[str, ...]
    max_lookback: dict[str, int] = field(default_factory=dict)
    """timeframe별 과거 조회 한계(봉 개수). 없으면 무제한으로 본다."""

    adjusted: Literal["always", "optional", "never"] = "never"
    supports_orders: bool = False
    supports_fractional: bool = False

    provides_universe: bool = False
    """`list_instruments`를 실제로 구현하는가.

    라우팅 표는 **시세**의 우선순위다. 목록 조회는 별개 능력이라, 이 값을 보지
    않으면 `krx: pykrx→fdr`에서 pykrx가 목록을 못 준다는 이유로 유니버스 조회가
    통째로 실패한다 — fdr이 줄 수 있는데도 그렇다. 능력으로 고르는 것은 폴백이
    아니다: 실패해서 넘어가는 것이 아니라 애초에 못 하는 소스를 빼는 것이고,
    매번 같은 소스가 뽑히므로 "그날의 후보 집합이 달라진다"는 걱정이 생기지 않는다.
    """

    rate_limit: RateLimitSpec = field(default_factory=RateLimitSpec)


@dataclass
class HealthStatus:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class UniverseEntry:
    """소스가 아는 거래 대상 하나. Symbol Universe 노드가 이 목록을 컷한다.

    ⚠️ **이 값은 "지금"의 스냅샷이다.** 거래대금은 호출 시점의 24시간 값이고,
    상장폐지된 종목은 애초에 목록에 없다. 그래서 이 목록으로 과거를 리플레이하면
    **유니버스가 미래를 본다** — 전략 코드는 완전히 인과적인데도 그렇다(4.8
    서바이버십). `strategy check`의 AST 검사에 걸리지 않는 경로이므로, 백테스트
    차단은 Symbol Universe 노드가 명시적으로 맡는다.
    """

    instrument: InstrumentRef
    quote_volume_24h: float | None = None
    """24시간 거래대금 (결제 통화 기준). 소스가 주지 않으면 None."""


class LookAheadError(RuntimeError):
    """`end` 이후의 캔들을 반환하려 했을 때. 백테스트 신뢰성의 마지막 방어선."""


class UniverseNotSupportedError(RuntimeError):
    """소스가 종목 목록 조회를 지원하지 않을 때."""


class MarketDataProvider(ABC):
    """시세 소스. 주문 기능과 분리되어 있어 시세/주문을 다른 소스로 조합할 수 있다."""

    id: str
    display_name: str
    venues: tuple[str, ...]
    credential_schema: type[BaseModel] | None = None
    """None이면 무인증 내장 소스 (PyKRX, yfinance 등)."""

    capabilities: ProviderCapabilities

    @abstractmethod
    async def fetch_ohlcv(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
    ) -> pd.DataFrame:
        """`end`(포함) 이전의 마감된 캔들을 최대 limit개 반환한다.

        **`end` 이후 데이터는 절대 반환하지 않는다.** 이 규칙이 깨지면 백테스트가
        미래를 참조하게 되어 전략 성과가 조작된다 (ARCHITECTURE.md 4.8).
        """

    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        """이 venue에서 거래 가능한 종목 목록 (ARCHITECTURE.md 3.3).

        기본 구현은 거부한다 — 목록을 **모르는 것과 비어 있는 것은 다르다.** 빈
        리스트를 돌려주면 Symbol Universe가 "유니버스가 0종목"이라고 조용히
        결론지어 그날 신호가 통째로 사라진다.
        """
        raise UniverseNotSupportedError(
            f"{self.id}는 종목 목록 조회를 지원하지 않습니다. "
            f"Symbol Universe 노드에 instruments를 직접 적거나 source를 바꾸세요."
        )

    async def health_check(self) -> HealthStatus:
        """Connections 화면의 [연결 테스트] 버튼이 호출한다."""
        return HealthStatus(ok=True, detail="구현되지 않음 (기본 통과)")

    def supports(self, instrument: InstrumentRef, timeframe: str) -> bool:
        return instrument.venue in self.venues and timeframe in self.capabilities.timeframes

    @staticmethod
    def assert_no_future(df: pd.DataFrame, end: datetime, provider_id: str) -> pd.DataFrame:
        """반환 직전에 호출한다. Provider 구현 실수를 조기에 잡는다."""
        if df.empty:
            return df
        last = df.index[-1]
        if last.to_pydatetime() > end:
            raise LookAheadError(
                f"{provider_id}가 end({end.isoformat()}) 이후 캔들을 반환했습니다: "
                f"{last.isoformat()}"
            )
        return df
