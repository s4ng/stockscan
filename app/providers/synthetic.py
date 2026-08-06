"""SyntheticProvider — 네트워크·API 키 없이 뼈대를 돌리기 위한 시세 소스.

심볼과 타임프레임으로 시드를 고정하므로 **몇 번을 실행해도 같은 캔들**이 나온다.
엔진의 결정성(ARCHITECTURE.md 1.2)을 테스트에서 그대로 검증할 수 있다.

TODO(Phase 2): PyKRX / yfinance / KIS / Alpaca 어댑터로 교체한다.
              이 파일은 그 뒤에도 테스트용으로 남긴다.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime

import pandas as pd

from app.market.instrument import InstrumentRef
from app.market.timeframe import duration
from app.providers.base import (
    HealthStatus,
    MarketDataProvider,
    ProviderCapabilities,
    UniverseEntry,
)

#: venue별 가짜 종목 목록. **결정적이어야 한다** — 테스트가 유니버스 크기에 기댄다.
SYNTHETIC_LISTING: dict[str, tuple[str, ...]] = {
    "krx": ("005930", "000660", "035720", "051910", "005380", "068270", "207940"),
    "nasdaq": ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"),
    "nyse": ("KO", "JPM", "XOM", "PG", "JNJ", "WMT", "V"),
}


class SyntheticProvider(MarketDataProvider):
    id = "synthetic"
    display_name = "Synthetic (개발용 더미 시세)"
    venues = ("krx", "nasdaq", "nyse")
    credential_schema = None
    capabilities = ProviderCapabilities(
        timeframes=("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"),
        adjusted="always",
        provides_universe=True,
        # ⚠️ 합성 봉이다. 캐시에 섞이면 이후 실제 실행이 가짜 시세로 돈다.
        cacheable=False,
        supports_orders=False,
    )

    def __init__(self, volatility: float = 0.015) -> None:
        self.volatility = volatility

    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        """가짜 종목 목록. 거래대금은 **목록 순서대로 줄어드는 결정적 값**이다.

        순서를 값에 실어 두면 "거래대금 상위 N"이 실제로 정렬을 하는지 테스트가
        확인할 수 있다. 값 자체에는 뜻이 없다 — 합성 시세이므로.
        """
        symbols = SYNTHETIC_LISTING.get(venue, ())
        return [
            UniverseEntry(
                InstrumentRef.parse(f"{venue}:{symbol}"),
                float((len(symbols) - index) * 1_000_000),
            )
            for index, symbol in enumerate(symbols)
        ]

    async def fetch_ohlcv(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
    ) -> pd.DataFrame:
        step = duration(timeframe)
        # end를 마지막 봉으로 삼아 limit개를 과거로 생성한다.
        times = [end - step * i for i in range(limit - 1, -1, -1)]
        rng = random.Random(self._seed(instrument, timeframe))

        price = self._base_price(instrument, rng)
        rows = []
        for _ in times:
            drift = rng.gauss(0, self.volatility)
            open_ = price
            close = max(price * (1 + drift), 1e-9)
            high = max(open_, close) * (1 + abs(rng.gauss(0, self.volatility / 3)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, self.volatility / 3)))
            volume = abs(rng.gauss(1_000, 250))
            rows.append((open_, high, low, close, volume))
            price = close

        df = pd.DataFrame(
            rows,
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex(times, tz="UTC", name="time"),
        )
        return self.assert_no_future(df, end, self.id)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, detail="synthetic 소스는 항상 사용 가능합니다")

    @staticmethod
    def _seed(instrument: InstrumentRef, timeframe: str) -> int:
        raw = f"{instrument.key}|{timeframe}".encode()
        return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")

    @staticmethod
    def _base_price(instrument: InstrumentRef, rng: random.Random) -> float:
        """통화권에 맞는 자릿수의 시작가를 만든다 (KRW는 크게, USD는 작게)."""
        if instrument.quote_currency == "KRW":
            return rng.uniform(10_000, 100_000_000)
        return rng.uniform(10, 500)
