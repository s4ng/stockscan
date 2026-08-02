"""CcxtProvider 계약 테스트 — 가짜 거래소로 돈다 (네트워크 없음).

여기서 지키는 것은 셋이다.

  1. ★ **봉 인덱스는 마감 시각이다.** CCXT는 시가 시각을 주므로, 변환이 빠지면
     `closed_only`가 진행 중인 봉을 통과시킨다 (4.4)
  2. **빈 응답 ≠ 데이터 없음.** 신규 상장 종목을 조용히 잃지 않는다
  3. **심볼 표기 변환** — `upbit:KRW-BTC` ↔ `BTC/KRW` (3.1)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.market.instrument import InstrumentRef
from app.providers.base import LookAheadError
from app.providers.ccxt_base import CcxtProvider
from app.providers.ccxt_quirks import quirk_for

UTC = ZoneInfo("UTC")
DAY_MS = 86_400_000
END = datetime(2026, 8, 2, tzinfo=UTC)
"""8/1 일봉의 마감 시각. 8/2 봉은 아직 진행 중이다."""


class FakeExchange:
    """`fetch_ohlcv(since, limit)`를 창(window) 방식으로 흉내 낸다.

    업비트가 실제로 그렇게 동작한다 — `since`부터 한 페이지 분량의 창을 보고,
    그 창에 데이터가 없으면 **빈 배열**을 준다. 상장 이전 구간을 조회하면
    "데이터 없음"이 아니라 "이 창에는 없음"이 돌아온다.
    """

    def __init__(self, first_open_ms: int, count: int) -> None:
        self.candles = [
            [first_open_ms + i * DAY_MS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
            for i in range(count)
        ]
        self.calls: list[tuple[str, int | None, int | None]] = []

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[float]]:
        self.calls.append((symbol, since, limit))
        page = limit or 200
        if since is None:
            return self.candles[-page:]
        window_end = since + page * DAY_MS
        return [c for c in self.candles if since <= c[0] < window_end][:page]

    async def load_markets(self) -> dict[str, Any]:
        return {
            "BTC/KRW": {"id": "KRW-BTC", "symbol": "BTC/KRW", "spot": True, "active": True},
            "ETH/BTC": {"id": "BTC-ETH", "symbol": "ETH/BTC", "spot": True, "active": True},
            "DEAD/KRW": {"id": "KRW-DEAD", "symbol": "DEAD/KRW", "spot": True, "active": False},
        }

    async def fetch_tickers(self) -> dict[str, Any]:
        return {
            "BTC/KRW": {"quoteVolume": 900.0},
            "ETH/BTC": {"quoteVolume": 5.0},
        }

    async def close(self) -> None:
        return None


def make_provider(exchange: FakeExchange, page_size: int = 200) -> CcxtProvider:
    provider = CcxtProvider("upbit", page_size=page_size)
    provider._exchange = exchange  # noqa: SLF001 - 네트워크를 끊기 위한 주입
    import asyncio

    provider._loop = asyncio.get_event_loop()  # noqa: SLF001
    return provider


def opens_before(end: datetime, count: int) -> int:
    """`end`에 마감하는 봉을 마지막으로 하는 `count`개 봉의 첫 시가(ms)."""
    last_open = int(end.timestamp() * 1000) - DAY_MS
    return last_open - (count - 1) * DAY_MS


# ------------------------------------------------------------------ 봉 시각 규약
async def test_index_is_bar_close_not_bar_open():
    """★ 이 변환이 빠지면 closed_only가 진행 중인 봉을 통과시킨다."""
    exchange = FakeExchange(opens_before(END, 10), 10)
    provider = make_provider(exchange)

    df = await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 10)

    assert df.index[-1] == END  # 마지막 봉의 **마감**이 곧 as_of
    assert df.index[0] == END - timedelta(days=9)
    # 인덱스가 시가였다면 마지막은 END - 1일이었을 것이다
    assert df.index[-1] != END - timedelta(days=1)


async def test_forming_bar_is_dropped():
    """거래소가 진행 중인 봉을 함께 줘도 `end` 뒤로 밀려 잘려 나간다."""
    # 8/2 시가 봉(= 8/3 마감)까지 들고 있는 거래소
    exchange = FakeExchange(opens_before(END, 10), 11)
    provider = make_provider(exchange)

    df = await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 20)

    assert df.index[-1] == END
    assert len(df) == 10


def test_assert_no_future_is_the_backstop_when_the_filter_is_wrong():
    """마지막 방어선을 직접 겨눈다 (규칙 2).

    평소에는 `df.index <= end` 필터가 먼저 걸러서 여기까지 오지 않는다. 그래서
    이 단언은 **필터가 틀린 날**을 위한 것이다 — 어댑터가 봉 시각 규약을 잘못
    구현하면 미래 봉이 그대로 통과하는데, 그건 폴백으로 감출 문제가 아니라
    즉시 터져야 하는 문제다.
    """
    import pandas as pd

    from app.engine.types import OHLCV_COLUMNS

    future = END + timedelta(days=1)
    df = pd.DataFrame(
        [[1.0, 1.0, 1.0, 1.0, 1.0]],
        columns=list(OHLCV_COLUMNS),
        index=pd.DatetimeIndex([future], name="time"),
    )

    with pytest.raises(LookAheadError):
        CcxtProvider.assert_no_future(df, END, "ccxt.upbit")


# ------------------------------------------------------------------ 페이지네이션
async def test_pagination_collects_more_than_one_page():
    """12-1 모멘텀은 273봉이 필요하다 — 페이지네이션은 선택이 아니다."""
    exchange = FakeExchange(opens_before(END, 320), 320)
    provider = make_provider(exchange, page_size=200)

    df = await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 320)

    assert len(df) == 320
    assert len(exchange.calls) >= 2
    assert df.index.is_monotonic_increasing
    assert not df.index.has_duplicates


async def test_empty_window_does_not_end_the_search():
    """★ 신규 상장 종목을 조용히 잃지 않는다.

    조회 시작점이 상장 이전이면 거래소는 빈 배열을 준다. 거기서 멈추면 실제로는
    58봉이 있는 종목이 '0봉'으로 보고되고, startup_candles가 짧은 전략은 그
    종목을 잃는다.
    """
    # 320봉을 요청하지만 실제 이력은 최근 30봉뿐이다
    exchange = FakeExchange(opens_before(END, 30), 30)
    provider = make_provider(exchange, page_size=200)

    df = await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 320)

    assert len(df) == 30
    assert df.index[-1] == END


async def test_limit_caps_the_result():
    exchange = FakeExchange(opens_before(END, 300), 300)
    provider = make_provider(exchange, page_size=200)

    df = await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 50)

    assert len(df) == 50
    assert df.index[-1] == END


# ------------------------------------------------------------------------ 심볼
def test_upbit_symbol_conversion_is_bidirectional():
    quirk = quirk_for("upbit")
    ref = InstrumentRef.parse("upbit:KRW-BTC")

    assert quirk.to_exchange_symbol(ref) == "BTC/KRW"
    assert quirk.to_venue_symbol({"id": "KRW-BTC", "symbol": "BTC/KRW"}) == "KRW-BTC"


async def test_fetch_uses_the_exchange_symbol():
    exchange = FakeExchange(opens_before(END, 5), 5)
    provider = make_provider(exchange)

    await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 5)

    assert exchange.calls[0][0] == "BTC/KRW"


# ---------------------------------------------------------------------- 유니버스
async def test_list_instruments_returns_venue_symbols_and_turnover():
    exchange = FakeExchange(opens_before(END, 5), 5)
    provider = make_provider(exchange)

    entries = await provider.list_instruments("upbit")

    keys = {e.instrument.key: e.quote_volume_24h for e in entries}
    assert keys["upbit:KRW-BTC"] == 900.0
    assert "upbit:KRW-DEAD" not in keys  # active=False는 유니버스에 넣지 않는다
    # 거래소가 준 BTC 마켓도 그대로 실린다. 결제 통화 필터는 노드가 한다.
    assert "upbit:BTC-ETH" in keys


# -------------------------------------------------------------------- capabilities
def test_capabilities_only_expose_timeframes_we_understand():
    """거래소는 `1s`·`1M`도 노출한다. 그대로 받으면 normalize가 터진다."""
    provider = CcxtProvider("upbit")

    assert "1d" in provider.capabilities.timeframes
    assert "1s" not in provider.capabilities.timeframes
    assert "1M" not in provider.capabilities.timeframes


async def test_unsupported_timeframe_is_refused():
    exchange = FakeExchange(opens_before(END, 5), 5)
    provider = make_provider(exchange)
    provider.capabilities = provider.capabilities.__class__(timeframes=("1w",))

    with pytest.raises(ValueError, match="1d"):
        await provider.fetch_ohlcv(InstrumentRef.parse("upbit:KRW-BTC"), "1d", END, 5)
