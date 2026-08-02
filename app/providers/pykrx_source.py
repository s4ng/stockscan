"""PykrxProvider — 한국거래소 일봉 (ARCHITECTURE.md 3.3).

무인증이다. KRX가 공개하는 데이터를 그대로 읽는다.

**이 어댑터가 조심하는 것 셋**

1. ★ **pykrx는 날짜만 준다.** 이 저장소의 인덱스 규약은 **마감 시각**이므로
   (규칙 15) 세션 폐장 시각으로 옮긴다. 그 시각의 단일 출처는 캘린더다 —
   여기서 `15:30 KST`를 상수로 박으면 조기폐장·지연개장 날 조용히 어긋난다.
2. **수정주가를 끌 수 없다.** 이 버전의 pykrx는 `adjusted=False`에 빈 프레임을
   돌려준다. 그래서 `capabilities.adjusted = "always"`로 **정직하게 선언**한다 —
   "옵션"이라고 적어 두면 라우팅이 비조정가를 줄 수 있다고 착각한다 (3.8).
3. **동기 라이브러리다.** HTTP를 블로킹으로 부르므로 `to_thread`로 감싼다.
   안 감싸면 이벤트 루프가 멈춰 같은 레벨의 다른 노드까지 함께 선다.

종목 마스터는 여기 없다 — `get_market_ticker_list`가 빈 목록을 돌려주는 상태라
`FdrProvider`가 맡는다. 설계 표(3.3)가 나눠 둔 역할 그대로다.
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

#: pykrx 컬럼 → 내부 표기.
_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
}

#: 요청 구간을 봉 개수에서 날짜로 환산할 때의 여유. 휴장일 때문에 달력일이
#: 거래일보다 항상 많다. 넉넉히 잡고 뒤에서 자른다.
_CALENDAR_DAY_RATIO = 1.6
_MIN_PADDING_DAYS = 14


class PykrxProvider(MarketDataProvider):
    id = "pykrx"
    display_name = "PyKRX (한국거래소 공개 일봉)"
    venues = ("krx",)
    credential_schema = None
    capabilities = ProviderCapabilities(
        timeframes=("1d",),
        # ⚠️ 이 버전의 pykrx는 비조정가를 주지 못한다. 위 docstring 2번 참조.
        adjusted="always",
        supports_orders=False,
        # KRX 공개 엔드포인트를 긁는 구조라 보수적으로 잡는다.
        rate_limit=RateLimitSpec(requests_per_second=2.0, burst=4),
    )

    def __init__(self) -> None:
        self._calendar = ExchangeSessionCalendar("krx", "XKRX")

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
        raw = await asyncio.to_thread(
            self._fetch_sync,
            instrument.symbol,
            start.astimezone(self._calendar.tz).strftime("%Y%m%d"),
            end_utc.astimezone(self._calendar.tz).strftime("%Y%m%d"),
        )
        df = self._to_frame(raw)
        df = df[df.index <= end_utc]
        if limit and len(df) > limit:
            df = df.iloc[-limit:]
        return self.assert_no_future(df, end_utc, self.id)

    @staticmethod
    def _fetch_sync(ticker: str, start: str, end: str) -> pd.DataFrame:
        from pykrx import stock

        return stock.get_market_ohlcv(start, end, ticker)

    def _to_frame(self, raw: pd.DataFrame) -> pd.DataFrame:
        return daily_frame(raw, _COLUMNS, self._calendar, source="pykrx")

    async def health_check(self) -> HealthStatus:
        try:
            frame = await asyncio.to_thread(self._fetch_sync, "005930", "20260101", "20260110")
        except Exception as exc:  # noqa: BLE001 - 연결 테스트는 실패 사유가 목적이다
            return HealthStatus(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return HealthStatus(ok=not frame.empty, detail=f"삼성전자 {len(frame)}봉")
