"""주식 어댑터 계약 테스트 — 소스 호출을 가로채 네트워크 없이 돈다.

세 어댑터(pykrx · yfinance · fdr)가 지켜야 하는 것은 같다.

  1. ★ **날짜 → 세션 마감 시각.** 소스는 날짜만 주는데 이 저장소의 인덱스 규약은
     마감 시각이다 (규칙 15). 어긋나면 `as_of`와 맞지 않아 전량 제외된다
  2. **`end` 이후 봉을 주지 않는다** (규칙 2)
  3. **`adjusted`를 정직하게 선언한다** — 라우팅이 이 값으로 판단한다 (3.8)
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.market.instrument import InstrumentRef
from app.providers.fdr_source import FdrProvider
from app.providers.pykrx_source import PykrxProvider
from app.providers.yfinance_source import YFinanceProvider

UTC = ZoneInfo("UTC")

#: KRX 세션 마감은 06:30 UTC, 미국(EDT)은 20:00 UTC.
KRX_END = datetime(2026, 7, 31, 6, 30, tzinfo=UTC)
US_END = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)

TRADING_DAYS = ["2026-07-29", "2026-07-30", "2026-07-31"]


def krx_frame(days: list[str]) -> pd.DataFrame:
    """pykrx가 돌려주는 모양 — 한글 컬럼, naive 날짜 인덱스."""
    return pd.DataFrame(
        {
            "시가": [100.0] * len(days),
            "고가": [110.0] * len(days),
            "저가": [90.0] * len(days),
            "종가": [105.0] * len(days),
            "거래량": [1000.0] * len(days),
            "등락률": [0.5] * len(days),
        },
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="날짜"),
    )


def us_frame(days: list[str], tz: str = "America/New_York") -> pd.DataFrame:
    """yfinance가 돌려주는 모양 — 현지 자정 인덱스, 배당·분할 컬럼 포함."""
    return pd.DataFrame(
        {
            "Open": [100.0] * len(days),
            "High": [110.0] * len(days),
            "Low": [90.0] * len(days),
            "Close": [105.0] * len(days),
            "Volume": [1000.0] * len(days),
            "Dividends": [0.0] * len(days),
            "Stock Splits": [0.0] * len(days),
        },
        index=pd.DatetimeIndex([pd.Timestamp(d, tz=tz) for d in days], name="Date"),
    )


# ------------------------------------------------------------------ 봉 시각 규약
async def test_pykrx_index_is_the_session_close(monkeypatch: pytest.MonkeyPatch):
    provider = PykrxProvider()
    monkeypatch.setattr(provider, "_fetch_sync", lambda *a: krx_frame(TRADING_DAYS))

    df = await provider.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)

    assert list(df.index) == [
        datetime(2026, 7, 29, 6, 30, tzinfo=UTC),
        datetime(2026, 7, 30, 6, 30, tzinfo=UTC),
        datetime(2026, 7, 31, 6, 30, tzinfo=UTC),
    ]
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


async def test_yfinance_index_is_the_session_close(monkeypatch: pytest.MonkeyPatch):
    """현지 자정으로 오는 것을 마감 시각으로 옮긴다. EDT라 20:00Z다."""
    provider = YFinanceProvider()
    monkeypatch.setattr(provider, "_fetch_sync", lambda *a: us_frame(TRADING_DAYS))

    df = await provider.fetch_ohlcv(InstrumentRef.parse("nasdaq:AAPL"), "1d", US_END, 10)

    assert df.index[-1] == US_END
    assert df.index[0] == datetime(2026, 7, 29, 20, 0, tzinfo=UTC)


async def test_us_winter_bar_closes_an_hour_later(monkeypatch: pytest.MonkeyPatch):
    """규칙 6 — 겨울은 EST라 21:00Z다. 고정 오프셋이면 여기서 깨진다."""
    provider = YFinanceProvider()
    monkeypatch.setattr(provider, "_fetch_sync", lambda *a: us_frame(["2026-01-15"]))

    df = await provider.fetch_ohlcv(
        InstrumentRef.parse("nasdaq:AAPL"), "1d", datetime(2026, 1, 15, 21, 0, tzinfo=UTC), 10
    )

    assert df.index[-1] == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)


async def test_holiday_rows_are_kept_not_dropped(monkeypatch: pytest.MonkeyPatch):
    """캘린더가 휴장이라 보는 날에 소스가 봉을 주면 **버리지 않는다.**

    어느 쪽이 틀렸든 가격은 실재했고, 조용히 지우면 지표에 구멍이 뚫린다.
    """
    provider = PykrxProvider()
    # 2026-01-01은 신정 휴장이다
    monkeypatch.setattr(provider, "_fetch_sync", lambda *a: krx_frame(["2026-01-01"]))

    df = await provider.fetch_ohlcv(
        InstrumentRef.parse("krx:005930"), "1d", datetime(2026, 1, 2, 6, 30, tzinfo=UTC), 10
    )

    assert len(df) == 1
    assert df.index[-1] == datetime(2026, 1, 1, 6, 30, tzinfo=UTC)


# ----------------------------------------------------------------- 미래 참조 방어
async def test_bars_after_end_are_cut(monkeypatch: pytest.MonkeyPatch):
    provider = PykrxProvider()
    monkeypatch.setattr(
        provider, "_fetch_sync", lambda *a: krx_frame([*TRADING_DAYS, "2026-08-03"])
    )

    df = await provider.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)

    assert df.index[-1] == KRX_END
    assert len(df) == 3


async def test_limit_keeps_the_most_recent_bars(monkeypatch: pytest.MonkeyPatch):
    provider = PykrxProvider()
    monkeypatch.setattr(provider, "_fetch_sync", lambda *a: krx_frame(TRADING_DAYS))

    df = await provider.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 2)

    assert len(df) == 2
    assert df.index[-1] == KRX_END


async def test_empty_response_yields_an_empty_contract_frame(monkeypatch: pytest.MonkeyPatch):
    """상장 전 구간을 물으면 빈 프레임이다. 컬럼 구조는 유지해야 한다."""
    provider = PykrxProvider()
    monkeypatch.setattr(provider, "_fetch_sync", lambda *a: pd.DataFrame())

    df = await provider.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)

    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


async def test_unexpected_columns_fail_loudly(monkeypatch: pytest.MonkeyPatch):
    """소스 라이브러리가 컬럼명을 바꾸면 조용히 빈 결과가 아니라 오류여야 한다."""
    provider = PykrxProvider()
    monkeypatch.setattr(
        provider, "_fetch_sync", lambda *a: pd.DataFrame({"뭔가": [1]}, index=[pd.Timestamp.now()])
    )

    with pytest.raises(ValueError, match="컬럼"):
        await provider.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)


# ---------------------------------------------------------------------- 수정주가
@pytest.mark.parametrize(
    "provider", [PykrxProvider(), YFinanceProvider(), FdrProvider()], ids=lambda p: p.id
)
def test_adjusted_is_declared_honestly(provider):
    """3.8 — 라우팅이 이 값으로 판단한다. '옵션'이라 적으면 못 주는 걸 준다고 착각한다."""
    assert provider.capabilities.adjusted == "always"
    assert provider.capabilities.timeframes == ("1d",)


@pytest.mark.parametrize(
    "provider", [PykrxProvider(), YFinanceProvider(), FdrProvider()], ids=lambda p: p.id
)
async def test_unsupported_timeframe_is_refused(provider):
    with pytest.raises(ValueError, match="1h"):
        await provider.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1h", KRX_END, 10)


# --------------------------------------------------------------------- 유니버스
async def test_fdr_universe_carries_turnover(monkeypatch: pytest.MonkeyPatch):
    provider = FdrProvider()
    listing = pd.DataFrame(
        {
            "Code": ["005930", "000660"],
            "Name": ["삼성전자", "SK하이닉스"],
            "Amount": [1_000.0, 9_000.0],
        }
    )
    monkeypatch.setattr(provider, "_listing_sync", staticmethod(lambda key: listing))

    entries = await provider.list_instruments("krx")

    assert {e.instrument.key: e.quote_volume_24h for e in entries} == {
        "krx:005930": 1_000.0,
        "krx:000660": 9_000.0,
    }


async def test_missing_turnover_stays_none(monkeypatch: pytest.MonkeyPatch):
    """미국 목록에는 거래대금이 없다. 0으로 채우면 유동성 컷이 조용히 무의미해진다."""
    provider = FdrProvider()
    monkeypatch.setattr(
        provider,
        "_listing_sync",
        staticmethod(lambda key: pd.DataFrame({"Symbol": ["AAPL"], "Name": ["Apple"]})),
    )

    entries = await provider.list_instruments("nasdaq")

    assert entries[0].quote_volume_24h is None


async def test_delisted_list_keeps_only_common_stock(monkeypatch: pytest.MonkeyPatch):
    """★ 서바이버십 방지의 원자료 (3.9). 비주권은 가격 조회가 되지 않는다."""
    provider = FdrProvider()
    frame = pd.DataFrame(
        {
            "Symbol": ["221670", "2086401G", "004200"],
            "SecuGroup": ["주권", "신주인수권증서", "주권"],
            "DelistingDate": ["2019-07-16", "2026-07-29", "2020-07-21"],
        }
    )
    monkeypatch.setattr(provider, "_listing_sync", staticmethod(lambda key: frame))

    result = await provider.list_delisted("krx")

    assert list(result["Symbol"]) == ["004200", "221670"]  # 최근 폐지 순
