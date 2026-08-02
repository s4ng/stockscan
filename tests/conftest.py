"""테스트 전역 격리.

**테스트는 네트워크를 타지 않는다.** Phase 1에서 기본 라우팅이 코인 → CCXT
실물 시세로 바뀌었으므로(`DEFAULT_ROUTES`), 격리하지 않으면 엔진 테스트가
거래소를 두드린다. 거래소가 느리거나 죽은 날 테스트가 빨개지면 그 신호는
쓸모가 없다 — 우리가 검증하려는 것은 엔진이지 업비트의 가동률이 아니다.

CCXT 어댑터 자체는 `test_providers_ccxt.py`가 가짜 거래소로 검증한다.
"""

from __future__ import annotations

import pytest

from app.providers.registry import ProviderRegistry
from app.providers.synthetic import SyntheticProvider


def synthetic_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(SyntheticProvider())
    return registry


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RunContext.create`가 기본으로 만드는 레지스트리를 synthetic 전용으로 바꾼다."""
    monkeypatch.setattr("app.engine.context.default_registry", synthetic_registry)
