"""테스트 전역 격리.

**테스트는 네트워크를 타지 않는다.** 기본 라우팅(`DEFAULT_ROUTES`)이 실물 소스를
가리키므로, 격리하지 않으면 파이프라인 테스트가 pykrx·yfinance를 두드린다. 소스가
느리거나 죽은 날 테스트가 빨개지면 그 신호는 쓸모가 없다 — 우리가 검증하려는 것은
이 프로그램이지 무료 API의 가동률이 아니다.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from app.config import AppConfig, ScheduleConfig
from app.core.config import SAMPLE_DIR, get_settings
from app.providers.registry import ProviderRegistry
from app.providers.synthetic import SyntheticProvider
from app.strategies import registry as strategy_registry


def synthetic_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(SyntheticProvider())
    return registry


def make_config(**overrides) -> AppConfig:
    """테스트용 설정 한 벌. 예제 전략(`sample/`)을 그대로 쓴다."""
    base = {
        "timezone": "Asia/Seoul",
        "universe": {"nasdaq": 5},
        "strategy": "demo_momentum",
        "schedule": ScheduleConfig(at=[time(15, 40)], heartbeat=time(9, 0)),
    }
    base.update(overrides)
    return AppConfig.model_validate(base)


@pytest.fixture
def config() -> AppConfig:
    return make_config()


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RunContext.create`가 기본으로 만드는 레지스트리를 synthetic 전용으로 바꾼다."""
    monkeypatch.setattr("app.engine.context.default_registry", synthetic_registry)


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """설정 디렉터리를 테스트마다 새 임시 디렉터리로 돌린다.

    기본값이 `~/.stockscan`이라 두지 않으면 **테스트가 실행하는 사람의 자산을
    건드린다** — DB·리포트가 거기에 쓰이고, `strategy new` 계열은 파일을 만들고,
    전략 목록은 그 사람이 뭘 갖고 있느냐에 따라 달라진다.

    전략 탐색은 `sample/`을 보게 둔다. **`strategies_dir`(명시 설정)이 아니라
    `_config_dir`(활성 설정 파일)로 넣는 것이 중요하다** — 명시 설정은 모든 것을
    이기므로, 그걸 깔아 두면 "전략은 설정 파일 옆에서 찾는다"는 규칙 자체가
    테스트에서 영영 실행되지 않는다.
    """
    monkeypatch.setattr(get_settings(), "config_dir", tmp_path / "config_home")
    monkeypatch.setattr(strategy_registry, "_config_dir", SAMPLE_DIR)
