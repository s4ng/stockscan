"""테스트 전역 격리.

**테스트는 네트워크를 타지 않는다.** Phase 1에서 기본 라우팅이 코인 → CCXT
실물 시세로 바뀌었으므로(`DEFAULT_ROUTES`), 격리하지 않으면 엔진 테스트가
거래소를 두드린다. 거래소가 느리거나 죽은 날 테스트가 빨개지면 그 신호는
쓸모가 없다 — 우리가 검증하려는 것은 엔진이지 업비트의 가동률이 아니다.

CCXT 어댑터 자체는 `test_providers_ccxt.py`가 가짜 거래소로 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import SAMPLE_DIR, get_settings
from app.providers.registry import ProviderRegistry
from app.providers.synthetic import SyntheticProvider
from app.strategies import registry as strategy_registry


def synthetic_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(SyntheticProvider())
    return registry


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RunContext.create`가 기본으로 만드는 레지스트리를 synthetic 전용으로 바꾼다."""
    monkeypatch.setattr("app.engine.context.default_registry", synthetic_registry)


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설정 디렉터리를 테스트마다 새 임시 디렉터리로 돌린다.

    기본값이 `~/.marketscan`이라 두지 않으면 **테스트가 실행하는 사람의 자산을
    건드린다** — DB·리포트가 거기에 쓰이고, `strategy new` 계열은 파일을 만들고,
    전략 목록은 그 사람이 뭘 갖고 있느냐에 따라 달라진다.

    전략 탐색은 `sample/`을 보게 둔다. **`strategies_dir`(명시 설정)이 아니라
    `_pipeline_dir`(활성 파이프라인)로 넣는 것이 중요하다** — 명시 설정은 모든
    것을 이기므로, 그걸 깔아 두면 "전략은 파이프라인 파일 옆에서 찾는다"는 규칙
    자체가 테스트에서 영영 실행되지 않는다. 파이프라인을 읽는 테스트는 실제
    동작대로 이 값을 덮어쓴다.
    """
    monkeypatch.setattr(get_settings(), "config_dir", tmp_path / "config_home")
    monkeypatch.setattr(strategy_registry, "_pipeline_dir", SAMPLE_DIR)
