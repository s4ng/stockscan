"""CcxtProvider — 코인 거래소 시세 (ARCHITECTURE.md 3.3).

**거래소당 파일을 만들지 않는다.** CCXT를 쓰는 이유가 정확히 그 통합이므로,
거래소별 차이는 `ccxt_quirks/`로 빼고 여기는 한 벌만 둔다.

공개 OHLCV는 **키가 필요 없다.** 시세를 무인증으로 고정한 3.3의 결과라, 이
어댑터에는 `credential_schema`가 없다.

이 파일이 실제로 조심하는 것은 두 가지다.

1. ★ **CCXT는 봉의 시가 시각을 주고, 이 저장소는 마감 시각을 쓴다.**
   그대로 인덱스로 쓰면 `closed_only`가 **진행 중인 봉을 통과시킨다** —
   `as_of`가 8/2 00:00Z(8/1 봉의 마감)일 때 시가 기준 8/2 봉도 `<= as_of`가
   되기 때문이다. 4.4가 경고한 "신호가 생겼다 사라지는" 버그의 정확한 발생
   경로다. 그래서 **읽자마자 마감 시각으로 옮긴다.**
2. **거래소당 인스턴스는 하나만 만들어 재사용한다.** `enableRateLimit`이 인스턴스
   단위라, 인스턴스를 매번 만들면 프로세스 전역 쿼터가 깨진다.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import ccxt.async_support as ccxt_async
import pandas as pd

from app.engine.types import OHLCV_COLUMNS
from app.market.instrument import InstrumentRef
from app.market.timeframe import TIMEFRAMES, duration, normalize
from app.providers.base import (
    HealthStatus,
    MarketDataProvider,
    ProviderCapabilities,
    RateLimitSpec,
    UniverseEntry,
)
from app.providers.ccxt_quirks import quirk_for

UTC = ZoneInfo("UTC")

#: 한 번의 fetch_ohlcv로 받을 봉 개수. 거래소 대부분이 200을 상한으로 둔다.
#: 12-1 모멘텀은 273봉이 필요하므로 **페이지네이션은 선택이 아니다.**
DEFAULT_PAGE_SIZE = 200


def _system_dns_session() -> aiohttp.ClientSession:
    """OS 해석기로 DNS를 도는 aiohttp 세션. 이유는 `CcxtProvider._RESOLVER_NOTE`."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    )

#: 페이지네이션 최대 왕복. 거래소가 진전 없는 응답을 계속 주는 경우의 안전판이다.
MAX_PAGES = 40


class CcxtProvider(MarketDataProvider):
    """CCXT가 지원하는 거래소 하나. `CcxtProvider("upbit")`처럼 쓴다."""

    credential_schema = None  # 공개 OHLCV는 무인증 (3.3)

    #: ★ **DNS는 OS 해석기를 쓴다.**
    #:
    #: aiohttp는 `aiodns`(c-ares)가 설치돼 있으면 그것을 기본 해석기로 쓰는데,
    #: c-ares는 `/etc/resolv.conf`나 윈도우 레지스트리에서 **DNS 서버 목록을 스스로
    #: 읽는다.** VPN·회사 네트워크·일부 어댑터 구성에서 이 목록이 비어 나오면
    #: `Could not contact DNS servers`로 실패하고, CCXT가 그것을
    #: `ExchangeNotAvailable`로 감싸 **"거래소가 죽었다"처럼 보인다.**
    #: 같은 순간 curl·requests는 멀쩡히 붙는다 — 그쪽은 OS 해석기를 쓰기 때문이다.
    #:
    #: 진단이 매우 어려운 종류라(네트워크는 되는데 이 프로그램만 안 된다) 환경에
    #: 기대지 않고 여기서 못박는다. 하루 몇 번 도는 배치라 비동기 DNS로 얻을 것도 없다.
    _RESOLVER_NOTE = "ThreadedResolver — OS 해석기 사용 (aiodns 우회)"

    def __init__(self, exchange_id: str, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        factory = getattr(ccxt_async, exchange_id, None)
        if factory is None:
            raise ValueError(
                f"CCXT가 모르는 거래소입니다: {exchange_id!r}. "
                f"`python -c \"import ccxt; print(ccxt.exchanges)\"`로 목록을 확인하세요."
            )
        self.exchange_id = exchange_id
        self.id = f"ccxt.{exchange_id}"
        self.display_name = f"{exchange_id} (CCXT 공개 시세)"
        self.venues = (exchange_id,)
        self.page_size = page_size
        self._quirk = quirk_for(exchange_id)
        self._factory = factory
        self._exchange: Any | None = None
        self._session: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # capabilities는 손으로 선언하지 않고 ex.has / ex.timeframes에서 유도한다 (3.3).
        # 수기 표는 언젠가 실제 능력과 어긋나고, 그러면 라우팅이 못 주는 소스로
        # 계속 흘려보낸다. 생성은 네트워크를 타지 않으므로 여기서 읽어도 안전하다.
        probe = factory()
        # 거래소는 우리가 모르는 봉도 노출한다(업비트는 `1s`·`3m`·`1M`). 모르는
        # 표기에 `normalize`를 걸면 터지므로 **교집합만** 취한다 — 여기서 거르지
        # 않으면 소스를 등록하는 것만으로 CLI 전체가 죽는다.
        supported = tuple(tf for tf in TIMEFRAMES if tf in (probe.timeframes or {}))
        self.capabilities = ProviderCapabilities(
            timeframes=supported if probe.has.get("fetchOHLCV") else (),
            adjusted="never",  # 코인에는 액면분할·배당이 없다 (3.8)
            supports_orders=bool(probe.has.get("createOrder")),
            # 마켓 목록은 CCXT가 항상 준다. 거래대금은 fetchTickers가 있어야 한다.
            provides_universe=bool(probe.has.get("fetchMarkets", True)),
            rate_limit=RateLimitSpec(
                requests_per_second=1000 / max(probe.rateLimit or 100, 1), burst=1
            ),
        )
        self._supports_tickers = bool(probe.has.get("fetchTickers"))

    # ----------------------------------------------------------------- 인스턴스 관리
    def _get_exchange(self) -> Any:
        """거래소 인스턴스를 하나만 만들어 재사용한다.

        aiohttp 세션이 만들어진 이벤트 루프에 묶이므로, 루프가 바뀌면(테스트가
        `asyncio.run`을 여러 번 부르는 경우) 새로 만든다. 재사용을 고집하면
        "Event loop is closed"로 터진다.
        """
        loop = asyncio.get_running_loop()
        if self._exchange is None or self._loop is not loop:
            self._session = _system_dns_session()
            self._exchange = self._factory(
                {"enableRateLimit": True, "session": self._session}
            )
            self._loop = loop
        return self._exchange

    async def close(self) -> None:
        """aiohttp 세션을 닫는다. CLI가 실행 끝에 부른다."""
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None
            self._loop = None
        if self._session is not None:
            await self._session.close()
            self._session = None

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
                f"{self.id}는 {tf} 봉을 주지 않습니다. "
                f"사용 가능: {', '.join(self.capabilities.timeframes) or '(없음)'}"
            )

        step = duration(tf)
        symbol = self._quirk.to_exchange_symbol(instrument)
        rows = await self._paginate(symbol, tf, end=end, step=step, limit=limit)
        df = self._to_frame(rows, step)

        # `end`는 마감된 봉의 종료 시각이다. 인덱스를 마감 시각으로 옮겨 뒀으므로
        # 진행 중인 봉(마감이 end보다 뒤)은 여기서 잘려 나간다.
        df = df[df.index <= end]
        if limit and len(df) > limit:
            df = df.iloc[-limit:]
        return self.assert_no_future(df, end, self.id)

    async def _paginate(
        self,
        symbol: str,
        timeframe: str,
        *,
        end: datetime,
        step: timedelta,
        limit: int,
    ) -> list[list[float]]:
        """`end`에 마감하는 봉까지 `limit`개를 모은다.

        거래소가 한 번에 200봉만 주므로 `since`를 앞으로 밀며 반복한다. 역방향
        조회는 거래소마다 규약이 달라 통합 표기로 흡수되지 않는다.
        """
        exchange = self._get_exchange()
        step_ms = int(step.total_seconds() * 1000)
        end_ms = int(end.timestamp() * 1000)

        # CCXT가 주는 것은 시가 시각이다. 마감이 `end`인 봉의 시가는 `end - step`.
        last_open_ms = end_ms - step_ms
        cursor = last_open_ms - max(limit - 1, 0) * step_ms

        page_ms = self.page_size * step_ms
        collected: dict[int, list[float]] = {}
        for _ in range(MAX_PAGES):
            if cursor > last_open_ms:
                break
            batch = await exchange.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=self.page_size
            )
            if not batch:
                # ⚠️ 빈 응답 ≠ 데이터 없음. 거래소는 `since`부터 한 페이지 분량의
                # **창**을 보는데, 그 창이 상장 이전이면 비어서 돌아온다. 여기서
                # 멈추면 신규 상장 종목이 통째로 "0봉"이 되고, startup_candles가
                # 짧은 전략은 그 종목을 **조용히** 잃는다. 창을 앞으로 밀어 계속 본다.
                cursor += page_ms
                continue
            for candle in batch:
                if candle[0] <= last_open_ms:
                    collected[int(candle[0])] = candle

            advance = int(batch[-1][0]) + step_ms
            if advance <= cursor:
                break  # 진전이 없다 — 무한 루프 방지
            cursor = advance
            if len(batch) < self.page_size:
                break  # 거래소가 더 줄 것이 없다

        return [collected[ts] for ts in sorted(collected)]

    def _to_frame(self, rows: list[list[float]], step: timedelta) -> pd.DataFrame:
        """CCXT의 `[시가시각ms, o, h, l, c, v]` → 마감 시각을 인덱스로 하는 DataFrame.

        ★ **인덱스를 시가에서 마감으로 옮기는 곳이 여기다.** 이 한 줄이 빠지면
        `closed_only`가 진행 중인 봉을 통과시킨다 (모듈 docstring 참조).
        """
        if not rows:
            idx = pd.DatetimeIndex([], tz="UTC", name="time")
            return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS}, index=idx)

        opens = pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)
        closes = pd.DatetimeIndex(opens + step, name="time")
        return pd.DataFrame(
            [[float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows],
            columns=list(OHLCV_COLUMNS),
            index=closes,
        )

    # ------------------------------------------------------------------- 유니버스
    async def list_instruments(self, venue: str) -> list[UniverseEntry]:
        """거래소가 지금 거래시키는 종목과 24시간 거래대금.

        ⚠️ **"지금"의 스냅샷이다.** 상장폐지된 종목은 여기 없으므로 이 목록으로
        과거를 리플레이하면 서바이버십 편향이 들어간다 (`UniverseEntry` 참조).
        차단은 Symbol Universe 노드가 한다.
        """
        exchange = self._get_exchange()
        markets = await exchange.load_markets()
        tickers = await exchange.fetch_tickers() if self._supports_tickers else {}

        entries: list[UniverseEntry] = []
        for symbol, market in markets.items():
            if not market.get("spot", True) or not market.get("active", True):
                continue
            try:
                ref = InstrumentRef.parse(f"{venue}:{self._quirk.to_venue_symbol(market)}")
            except ValueError:
                continue  # venue 표기로 옮길 수 없는 종목은 유니버스에 넣지 않는다
            volume = (tickers.get(symbol) or {}).get("quoteVolume")
            name = self._quirk.display_name(market)
            entries.append(
                UniverseEntry(
                    instrument=replace(ref, display_name=name) if name else ref,
                    quote_volume_24h=float(volume) if volume is not None else None,
                )
            )
        return entries

    async def health_check(self) -> HealthStatus:
        try:
            exchange = self._get_exchange()
            markets = await exchange.load_markets()
        except Exception as exc:  # noqa: BLE001 - 연결 테스트는 실패 사유를 보여 주는 게 목적
            return HealthStatus(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return HealthStatus(ok=True, detail=f"{len(markets)}개 마켓")
