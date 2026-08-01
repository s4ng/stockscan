"""ProviderRegistry — Connection 라우팅과 폴백 (ARCHITECTURE.md 3.4).

노드는 어떤 증권사를 쓰는지 알지 못한다. `(venue, timeframe)`에 대한 우선순위
목록을 보고 앞 소스부터 시도하며, 실패하면 다음 소스로 넘어간다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.market.instrument import InstrumentRef
from app.providers.base import LookAheadError, MarketDataProvider

log = logging.getLogger(__name__)

AUTO = "auto"


class NoProviderError(RuntimeError):
    """요청한 (venue, timeframe)을 처리할 소스가 하나도 없을 때."""


class AllProvidersFailedError(RuntimeError):
    """등록된 소스를 모두 시도했지만 전부 실패했을 때."""


@dataclass
class FetchResult:
    df: pd.DataFrame
    provider_id: str
    """어느 소스에서 받았는지. ohlcv_cache에 함께 저장해 정합성을 추적한다."""


@dataclass
class ProviderRegistry:
    _providers: dict[str, MarketDataProvider] = field(default_factory=dict)
    _routes: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    """(venue, timeframe) → 우선순위 소스 id 목록. timeframe '*'는 와일드카드."""

    def register(self, provider: MarketDataProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> MarketDataProvider:
        if provider_id not in self._providers:
            raise NoProviderError(
                f"등록되지 않은 소스: {provider_id!r}. "
                f"등록된 소스: {', '.join(sorted(self._providers)) or '(없음)'}"
            )
        return self._providers[provider_id]

    def set_route(self, venue: str, timeframe: str, provider_ids: list[str]) -> None:
        for pid in provider_ids:
            self.get(pid)  # 존재 검증
        self._routes[(venue, timeframe)] = list(provider_ids)

    def resolve(self, instrument: InstrumentRef, timeframe: str) -> list[MarketDataProvider]:
        """이 조합을 처리할 소스를 우선순위 순으로 돌려준다."""
        for key in ((instrument.venue, timeframe), (instrument.venue, "*")):
            if key in self._routes:
                return [self.get(pid) for pid in self._routes[key]]
        # 라우팅 표에 없으면 capability로 판단한다
        return [p for p in self._providers.values() if p.supports(instrument, timeframe)]

    async def fetch_ohlcv(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
        source: str = AUTO,
    ) -> FetchResult:
        """`source`가 'auto'면 라우팅 표를 따르고, 특정 id면 그 소스만 쓴다."""
        if source != AUTO:
            candidates = [self.get(source)]
        else:
            candidates = self.resolve(instrument, timeframe)

        if not candidates:
            raise NoProviderError(
                f"{instrument.key} · {timeframe}을(를) 처리할 소스가 없습니다. "
                f"Connections에서 소스를 등록하거나 라우팅을 설정하세요."
            )

        failures: list[str] = []
        for provider in candidates:
            try:
                df = await provider.fetch_ohlcv(instrument, timeframe, end, limit)
                return FetchResult(df=df, provider_id=provider.id)
            except LookAheadError:
                raise  # 미래 참조는 폴백으로 감출 문제가 아니다
            except Exception as exc:  # noqa: BLE001 - 폴백을 위해 광범위하게 잡는다
                log.warning("소스 %s 실패 (%s %s): %s", provider.id, instrument.key, timeframe, exc)
                failures.append(f"{provider.id}: {exc}")

        raise AllProvidersFailedError(
            f"{instrument.key} · {timeframe} 조회에 모든 소스가 실패했습니다 — "
            + " | ".join(failures)
        )


def default_registry() -> ProviderRegistry:
    """뼈대 기본값 — synthetic 소스 하나만 등록한다 (네트워크·키 불필요)."""
    from app.providers.synthetic import SyntheticProvider

    registry = ProviderRegistry()
    registry.register(SyntheticProvider())
    return registry
