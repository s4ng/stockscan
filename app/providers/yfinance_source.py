"""YFinanceProvider — 미국 주식 일봉 (ARCHITECTURE.md 3.3).

무인증이지만 **비공식 API다.** 언제든 막힐 수 있다는 전제로 다룬다 — 그게
`ohlcv_cache`를 "성능 최적화"가 아니라 영구 보관하는 데이터 자산으로 보는
이유다 (3.9).

**수정주가가 기본이다** (`auto_adjust=True`). PyKRX와 함께 쓰면 3.8이 경고한
"소스마다 조정 방식이 다르다"에 정면으로 걸리므로, `capabilities.adjusted`를
정직하게 선언해 라우팅이 판단하게 둔다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.market.calendar import ExchangeSessionCalendar
from app.market.instrument import InstrumentRef
from app.market.timeframe import normalize
from app.providers.base import (
    HealthStatus,
    MarketDataProvider,
    ProviderCapabilities,
    RateLimitSpec,
)
from app.providers.daily_frame import daily_frame

UTC = ZoneInfo("UTC")

_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}

_CALENDAR_DAY_RATIO = 1.6
_MIN_PADDING_DAYS = 14


class YFinanceProvider(MarketDataProvider):
    id = "yfinance"
    display_name = "yfinance (미국 주식 일봉 · 비공식)"
    venues = ("nasdaq", "nyse")
    credential_schema = None
    capabilities = ProviderCapabilities(
        timeframes=("1d",),
        # auto_adjust=True로 고정한다. 배당·분할이 소급 반영된 가격이다 (3.8).
        adjusted="always",
        supports_orders=False,
        rate_limit=RateLimitSpec(requests_per_second=2.0, burst=5),
    )

    def __init__(self) -> None:
        self._calendar = ExchangeSessionCalendar("us_equity", "XNYS")

    async def fetch_ohlcv(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
    ) -> pd.DataFrame:
        tf = normalize(timeframe)
        if tf not in self.capabilities.timeframes:
            raise ValueError(
                f"{self.id}는 {tf} 봉을 주지 않습니다. 사용 가능: "
                f"{', '.join(self.capabilities.timeframes)}"
            )

        end_utc = end.astimezone(UTC)
        start = end_utc - timedelta(
            days=max(int(limit * _CALENDAR_DAY_RATIO), limit + _MIN_PADDING_DAYS)
        )
        # yfinance의 end는 **배타적**이다. 마감일 봉을 받으려면 하루 더 준다.
        raw = await asyncio.to_thread(
            self._fetch_sync,
            instrument.symbol,
            start.date().isoformat(),
            (end_utc.date() + timedelta(days=1)).isoformat(),
        )
        df = daily_frame(raw, _COLUMNS, self._calendar, source="yfinance")
        df = df[df.index <= end_utc]
        if limit and len(df) > limit:
            df = df.iloc[-limit:]
        return self.assert_no_future(df, end_utc, self.id)

    @staticmethod
    def _fetch_sync(symbol: str, start: str, end: str) -> pd.DataFrame:
        import yfinance as yf

        return yf.Ticker(symbol).history(
            start=start, end=end, interval="1d", auto_adjust=True, raise_errors=False
        )

    async def health_check(self) -> HealthStatus:
        try:
            frame = await asyncio.to_thread(self._fetch_sync, "AAPL", "2026-01-02", "2026-01-10")
        except Exception as exc:  # noqa: BLE001 - 연결 테스트는 실패 사유가 목적이다
            return HealthStatus(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return HealthStatus(ok=not frame.empty, detail=f"AAPL {len(frame)}봉")
