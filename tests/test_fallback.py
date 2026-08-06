"""소스 폴백 가시화 회귀 테스트 (ARCHITECTURE.md 3.4 / 3.8).

**실제 라우팅 조합**(`krx: pykrx→fdr` · `nasdaq: yfinance→fdr`)으로 검증한다.
`failed_sources` 경로는 구현돼 있었지만 지금까지는 가짜 소스로만 확인했고,
그러면 정작 실사용 조합에서 폴백이 조용히 지나가도 알 수 없다.

**왜 조용하면 안 되는가.** 폴백은 소스가 바뀌었다는 뜻이다. 소스가 바뀌면
수정주가 정책 차이로 지표가 불연속해지고(3.8), 같은 `ctx.now`에 다른 결과가
나와 백테스트 동치성이 깨진다. 그래서 **어느 종목이 어느 소스로 대체됐는지**가
`Item.meta`와 실행 로그에 남아야 한다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.market.instrument import InstrumentRef
from app.pipeline import fetch_bars
from app.providers.fdr_source import FdrProvider
from app.providers.pykrx_source import PykrxProvider
from app.providers.registry import (
    DEFAULT_ROUTES,
    AllProvidersFailedError,
    ProviderRegistry,
)
from app.providers.yfinance_source import YFinanceProvider

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
KRX_END = datetime(2026, 7, 31, 6, 30, tzinfo=UTC)
DAYS = ["2026-07-29", "2026-07-30", "2026-07-31"]


def fdr_krx_frame() -> pd.DataFrame:
    """FDR이 돌려주는 모양 — 영문 컬럼, naive 날짜 인덱스."""
    return pd.DataFrame(
        {
            "Open": [100.0] * 3,
            "High": [110.0] * 3,
            "Low": [90.0] * 3,
            "Close": [105.0] * 3,
            "Volume": [1000.0] * 3,
        },
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in DAYS], name="Date"),
    )


def routed_registry(monkeypatch: pytest.MonkeyPatch, *, pykrx_dies: bool) -> ProviderRegistry:
    """운영과 같은 라우팅 표를 쓰되, 소스 호출만 가로챈다."""
    pykrx, fdr, yfinance = PykrxProvider(), FdrProvider(), YFinanceProvider()

    def boom(*args, **kwargs):
        raise RuntimeError("KRX 공개 엔드포인트가 응답하지 않습니다")

    monkeypatch.setattr(pykrx, "_fetch_sync", boom if pykrx_dies else (lambda *a: pd.DataFrame()))
    monkeypatch.setattr(fdr, "_fetch_sync", staticmethod(lambda *a: fdr_krx_frame()))
    monkeypatch.setattr(yfinance, "_fetch_sync", staticmethod(lambda *a: pd.DataFrame()))

    registry = ProviderRegistry()
    for provider in (pykrx, fdr, yfinance):
        registry.register(provider)
    for venue in ("krx", "nasdaq", "nyse"):
        registry.set_route(venue, "*", list(DEFAULT_ROUTES[venue]))
    return registry


# ------------------------------------------------------------------ 레지스트리
async def test_pykrx_failure_falls_back_to_fdr(monkeypatch: pytest.MonkeyPatch):
    registry = routed_registry(monkeypatch, pykrx_dies=True)

    result = await registry.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)

    assert result.provider_id == "fdr"
    assert result.failed_sources == ("pykrx",)
    assert result.used_fallback is True
    assert len(result.df) == 3


async def test_no_fallback_leaves_no_trace(monkeypatch: pytest.MonkeyPatch):
    """폴백이 없었으면 `failed_sources`는 비어야 한다. 늘 채우면 경고가 무의미해진다."""
    registry = routed_registry(monkeypatch, pykrx_dies=False)
    monkeypatch.setattr(
        registry.get("pykrx"),
        "_fetch_sync",
        staticmethod(
            lambda *a: pd.DataFrame(
                {
                    "시가": [100.0],
                    "고가": [110.0],
                    "저가": [90.0],
                    "종가": [105.0],
                    "거래량": [1000.0],
                },
                index=pd.DatetimeIndex([pd.Timestamp("2026-07-31")], name="날짜"),
            )
        ),
    )

    result = await registry.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)

    assert result.provider_id == "pykrx"
    assert result.failed_sources == ()
    assert result.used_fallback is False


async def test_all_sources_dead_raises_with_every_reason(monkeypatch: pytest.MonkeyPatch):
    """전부 죽으면 조용히 빈 결과가 아니라 오류다. 사유가 모두 실려야 한다."""
    registry = routed_registry(monkeypatch, pykrx_dies=True)

    def boom(*args, **kwargs):
        raise RuntimeError("FDR도 응답하지 않습니다")

    monkeypatch.setattr(registry.get("fdr"), "_fetch_sync", staticmethod(boom))

    with pytest.raises(AllProvidersFailedError) as exc:
        await registry.fetch_ohlcv(InstrumentRef.parse("krx:005930"), "1d", KRX_END, 10)

    assert "pykrx" in str(exc.value)
    assert "fdr" in str(exc.value)


# --------------------------------------------------------------- 노드까지 전파
async def test_fallback_is_visible_in_item_meta_and_logs(monkeypatch: pytest.MonkeyPatch):
    """★ 사후에 "어느 소스로 대체됐나"를 되짚는 유일한 단서다 (3.4)."""
    ctx = RunContext.create(now=NOW, providers=routed_registry(monkeypatch, pykrx_dies=True))
    bundle: Bundle = await fetch_bars(["krx:005930"], {}, 3, ctx.bind("data"))

    item = bundle.items[0]
    assert item.meta["source"] == "fdr"
    assert item.meta["fallback_from"] == ["pykrx"]
    # 소스가 바뀐 것은 경고로 남아야 한다 — node_runs에 실려 사후에 읽힌다.
    warnings = [r.message for r in ctx.log.records if r.level == "warning"]
    assert any("폴백" in m and "pykrx" in m and "fdr" in m for m in warnings)


async def test_fallback_records_the_source_that_actually_answered(
    monkeypatch: pytest.MonkeyPatch,
):
    """`adjusted`는 응답한 소스가 정한다 — 캐시 키에 들어가므로 (규칙 8)."""
    ctx = RunContext.create(now=NOW, providers=routed_registry(monkeypatch, pykrx_dies=True))
    bundle = await fetch_bars(["krx:005930"], {}, 3, ctx.bind("data"))

    # pykrx도 fdr도 always지만, 값의 출처가 "설정"이 아니라 "소스"여야 한다.
    assert bundle.items[0].meta["adjusted"] is True
