"""FdrProvider — 종목 마스터·상장폐지 목록·폴백 일봉 (ARCHITECTURE.md 3.3).

역할이 셋인데, 이 저장소에서 가장 값있는 것은 **세 번째**다.

1. **종목 마스터** — KRX 2,800여 종목. `Amount`(거래대금)가 함께 오므로 유니버스
   유동성 컷을 여기서 만들 수 있다. PyKRX의 `get_market_ticker_list`가 빈 목록을
   돌려주는 상태라 마스터는 이쪽이 맡는다.
2. **폴백 일봉** — PyKRX·yfinance가 죽었을 때의 두 번째 소스.
3. ★ **상장폐지 목록** — 4.8의 서바이버십 편향을 데이터 레이어에서 막는 유일한
   경로다. 살아 있는 종목만 쌓으면 백테스트가 구조적으로 부풀려진다.

⚠️ **`adjusted`가 소스별로 다르다는 것이 3.8의 1순위 위험이다.** FDR의 KRX 일봉은
수정주가 기준이라 PyKRX와 같은 축이지만, 두 소스가 같은 날 종가를 다르게 주면
지표가 불연속해진다. 그래서 폴백은 조용히 넘어가지 않고 `failed_sources`로 남는다.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
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
    UniverseEntry,
    UniverseNotSupportedError,
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

#: venue → (FDR 상장 목록 키, 캘린더 코드, 심볼 컬럼)
_LISTINGS: dict[str, tuple[str, str, str]] = {
    "krx": ("KRX", "XKRX", "Code"),
    "nasdaq": ("NASDAQ", "XNYS", "Symbol"),
    "nyse": ("NYSE", "XNYS", "Symbol"),
}

_CALENDAR_DAY_RATIO = 1.6
_MIN_PADDING_DAYS = 14


class FdrProvider(MarketDataProvider):
    id = "fdr"
    display_name = "FinanceDataReader (마스터·폐지목록·폴백 일봉)"
    venues = ("krx", "nasdaq", "nyse")
    credential_schema = None
    capabilities = ProviderCapabilities(
        timeframes=("1d",),
        adjusted="always",
        provides_universe=True,  # 종목 마스터 + 상장폐지 목록의 유일한 출처 (3.3)
        supports_orders=False,
        rate_limit=RateLimitSpec(requests_per_second=2.0, burst=5),
    )

    def __init__(self) -> None:
        self._calendars = {
            "krx": ExchangeSessionCalendar("krx", "XKRX"),
            "us": ExchangeSessionCalendar("us_equity", "XNYS"),
        }

    def _calendar_for(self, venue: str) -> ExchangeSessionCalendar:
        return self._calendars["krx"] if venue == "krx" else self._calendars["us"]

    # --------------------------------------------------------------------- 시세
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
            start.date().isoformat(),
            end_utc.date().isoformat(),
        )
        calendar = self._calendar_for(instrument.venue)
        df = daily_frame(raw, _COLUMNS, calendar, source="fdr")
        df = df[df.index <= end_utc]
        if limit and len(df) > limit:
            df = df.iloc[-limit:]
        return self.assert_no_future(df, end_utc, self.id)

    @staticmethod
    def _fetch_sync(symbol: str, start: str, end: str) -> pd.DataFrame:
        import FinanceDataReader as fdr

        return fdr.DataReader(symbol, start, end)

    # ------------------------------------------------------------------ 유니버스
    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        spec = _LISTINGS.get(venue)
        if spec is None:
            raise UniverseNotSupportedError(
                f"{self.id}는 {venue}의 종목 목록을 주지 않습니다. "
                f"지원 venue: {', '.join(_LISTINGS)}"
            )
        listing_key, _, symbol_column = spec
        frame = await asyncio.to_thread(self._listing_sync, listing_key)
        if frame is None or frame.empty or symbol_column not in frame.columns:
            raise UniverseNotSupportedError(
                f"{self.id}가 {venue} 종목 목록을 주지 못했습니다 "
                f"(받은 컬럼: {list(frame.columns) if frame is not None else None}). "
                f"Symbol Universe에 instruments를 직접 적으세요."
            )

        # `Amount`(거래대금)는 KRX 목록에만 있다. 미국 목록은 심볼·이름뿐이라
        # 거래대금 컷을 걸 수 없고, 그 사실이 None으로 드러나야 한다 —
        # 0으로 채우면 유동성 컷이 조용히 무의미해진다.
        has_amount = "Amount" in frame.columns
        entries: list[UniverseEntry] = []
        for row in frame.itertuples(index=False):
            symbol = str(getattr(row, symbol_column, "") or "").strip()
            if not symbol:
                continue
            try:
                ref = InstrumentRef.parse(f"{venue}:{symbol}")
            except ValueError:
                continue
            amount = getattr(row, "Amount", None) if has_amount else None
            entries.append(
                UniverseEntry(
                    instrument=ref,
                    quote_volume_24h=float(amount)
                    if amount is not None and pd.notna(amount)
                    else None,
                )
            )
        return entries

    @staticmethod
    def _listing_sync(key: str) -> pd.DataFrame:
        import FinanceDataReader as fdr

        return fdr.StockListing(key)

    # --------------------------------------------------------------- 상장폐지 ★
    async def list_delisted(self, venue: str = "krx", since: date | None = None) -> pd.DataFrame:
        """폐지 종목과 폐지일. **서바이버십 편향을 막는 원자료다** (3.9 / 4.8).

        살아 있는 종목만 수집하면 "10년간 살아남은 종목들"로 백테스트를 돌리게 되고,
        그 성과는 구조적으로 부풀려진다. 폐지 종목은 **지우지 않고 보관한다.**

        `SecuGroup == '주권'`만 남긴다 — 신주인수권·수익증권 등은 심볼 체계가 달라
        가격 조회가 되지 않는다(확인함).
        """
        if venue != "krx":
            raise UniverseNotSupportedError(
                f"{self.id}의 상장폐지 목록은 krx만 지원합니다 (받은 값: {venue!r})."
            )
        frame = await asyncio.to_thread(self._listing_sync, "KRX-DELISTING")
        frame = frame.copy()
        frame["DelistingDate"] = pd.to_datetime(frame["DelistingDate"], errors="coerce")
        frame = frame[frame["SecuGroup"] == "주권"]
        frame = frame[frame["DelistingDate"].notna()]
        if since is not None:
            frame = frame[frame["DelistingDate"] >= pd.Timestamp(since)]
        return frame.sort_values("DelistingDate", ascending=False).reset_index(drop=True)

    async def health_check(self) -> HealthStatus:
        try:
            frame = await asyncio.to_thread(self._listing_sync, "KRX")
        except Exception as exc:  # noqa: BLE001 - 연결 테스트는 실패 사유가 목적이다
            return HealthStatus(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return HealthStatus(ok=not frame.empty, detail=f"KRX {len(frame)}종목")
