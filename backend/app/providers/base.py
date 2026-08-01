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
    rate_limit: RateLimitSpec = field(default_factory=RateLimitSpec)


@dataclass
class HealthStatus:
    ok: bool
    detail: str = ""


class LookAheadError(RuntimeError):
    """`end` 이후의 캔들을 반환하려 했을 때. 백테스트 신뢰성의 마지막 방어선."""


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
