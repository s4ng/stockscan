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
from app.providers.base import LookAheadError, MarketDataProvider, UniverseEntry

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

    failed_sources: tuple[str, ...] = ()
    """폴백이 발동했다면 그 앞에서 실패한 소스들.

    폴백은 조용히 넘어가면 안 된다 — 소스가 바뀌면 수정주가 정책 차이로 지표가
    불연속해지고(3.8), 같은 `ctx.now`에 다른 결과가 나와 백테스트 동치성이 깨진다.
    호출자가 이 값을 `ctx.log`와 `Item.meta`로 올려 실행 이력에 남긴다.
    """

    @property
    def used_fallback(self) -> bool:
        return bool(self.failed_sources)


@dataclass
class ProviderRegistry:
    _providers: dict[str, MarketDataProvider] = field(default_factory=dict)
    _routes: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    """(venue, timeframe) → 우선순위 소스 id 목록. timeframe '*'는 와일드카드."""

    def register(self, provider: MarketDataProvider) -> None:
        self._providers[provider.id] = provider

    async def close(self) -> None:
        """소스가 연 커넥션을 닫는다. CLI가 실행 끝에 부른다.

        CCXT는 aiohttp 세션을 들고 있어서 닫지 않으면 프로세스 종료 시
        "Unclosed client session" 경고가 남는다.
        """
        for provider in self._providers.values():
            closer = getattr(provider, "close", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception as exc:  # noqa: BLE001 - 정리 실패가 실행을 실패로 만들면 안 된다
                log.warning("소스 %s 정리 실패: %s", provider.id, exc)

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

    async def list_instruments(
        self, venue: str, source: str = AUTO, timeframe: str = "1d"
    ) -> tuple[list[UniverseEntry], str]:
        """venue의 종목 목록과 그것을 준 소스 id.

        폴백하지 않는다 — 유니버스를 다른 소스에서 받으면 그날의 후보 집합이 통째로
        달라진다. 시세 한 종목이 폴백되는 것과 무게가 다르므로 그대로 터뜨린다.
        """
        if source != AUTO:
            provider = self.get(source)
        else:
            candidates = [
                p
                for p in self._providers.values()
                if venue in p.venues and timeframe in p.capabilities.timeframes
            ]
            routed = self._routes.get((venue, timeframe)) or self._routes.get((venue, "*"))
            if routed:
                candidates = [self.get(pid) for pid in routed]
            if not candidates:
                raise NoProviderError(
                    f"{venue}의 종목 목록을 줄 소스가 없습니다. "
                    f"Symbol Universe 노드에 instruments를 직접 적으세요."
                )
            provider = candidates[0]
        return await provider.list_instruments(venue), provider.id

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
        failed_ids: list[str] = []
        for provider in candidates:
            try:
                df = await provider.fetch_ohlcv(instrument, timeframe, end, limit)
                return FetchResult(df=df, provider_id=provider.id, failed_sources=tuple(failed_ids))
            except LookAheadError:
                raise  # 미래 참조는 폴백으로 감출 문제가 아니다
            except Exception as exc:  # noqa: BLE001 - 폴백을 위해 광범위하게 잡는다
                log.warning("소스 %s 실패 (%s %s): %s", provider.id, instrument.key, timeframe, exc)
                failures.append(f"{provider.id}: {exc}")
                failed_ids.append(provider.id)

        raise AllProvidersFailedError(
            f"{instrument.key} · {timeframe} 조회에 모든 소스가 실패했습니다 — "
            + " | ".join(failures)
        )


#: venue → 기본 소스 우선순위. Phase 2에서 주식 쪽이 실제 소스로 바뀐다 (3.4).
DEFAULT_ROUTES: dict[str, tuple[str, ...]] = {
    "upbit": ("ccxt.upbit",),
    "binance": ("ccxt.binance",),
    # ⚠️ 아직 더미다. PykrxProvider·YFinanceProvider·FdrProvider는 Phase 2다.
    "krx": ("synthetic",),
    "nasdaq": ("synthetic",),
    "nyse": ("synthetic",),
}


def default_registry() -> ProviderRegistry:
    """기본 소스 구성.

    코인은 실제 시세(CCXT 공개 OHLCV), 주식은 아직 `synthetic` 더미다. Phase 1이
    업비트 하나로 좁힌 이유가 여기 그대로 드러난다 — 무인증·24/7이라 캘린더 분기도
    주말 공백도 없어서, 미지수를 "전략 경로가 도는가" 하나로 좁힐 수 있다.

    소스 생성은 네트워크를 타지 않는다. CCXT 인스턴스는 첫 조회 때 만들어진다.
    """
    from app.providers.ccxt_base import CcxtProvider
    from app.providers.synthetic import SyntheticProvider

    registry = ProviderRegistry()
    registry.register(SyntheticProvider())
    for exchange_id in ("upbit", "binance"):
        registry.register(CcxtProvider(exchange_id))

    for venue, provider_ids in DEFAULT_ROUTES.items():
        registry.set_route(venue, "*", list(provider_ids))
    return registry
