"""애플리케이션 설정.

Self-hosted 단일 사용자 전제이므로 설정은 환경변수 하나로 끝낸다.
민감한 값(마스터 키, API 토큰)은 절대 기본값을 두지 않는다.

경로 기본값은 모두 **상대 경로**다. 어느 디렉터리에서 CLI를 부르든 같은 파일을
보아야 하므로, 쓰는 쪽에서 `settings.resolve()`로 프로젝트 루트 기준 절대 경로로
편다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: 이 파일 기준 프로젝트 루트 (app/core/config.py → app/core → app → 루트)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MARKETSCAN_", extra="ignore")

    app_name: str = "marketscan"
    debug: bool = False

    database_url: str = "sqlite+aiosqlite:///./data/marketscan.db"

    #: 사용자 전략 파일의 정본 위치. git으로 관리하고 소스 해시가 실행에 박힌다 (4.7).
    strategies_dir: Path = Path("strategies")

    #: 실행할 파이프라인 정의. 형식은 **YAML로 확정**됐다 (11장 4번, Phase 1에서 해소).
    #: 로더가 확장자로 갈라 받으므로 기존 `.json` 파일도 그대로 읽힌다.
    pipeline_path: Path = Path("pipelines/demo.yaml")

    #: 백테스트·실행 리포트 산출물. 서빙하지 않고 파일로 떨어뜨린다 (2.1).
    reports_dir: Path = Path("reports")

    #: 자격 증명 암호화 마스터 키. 미설정 시 Connection 저장 기능이 비활성화된다.
    #: 이 키를 잃으면 저장된 API 키를 복호화할 수 없다 (백업 대상).
    master_key: str | None = None

    #: 전역 실주문 차단. Phase 5 전까지는 항상 False.
    live_trading_enabled: bool = False

    def resolve(self, path: Path | str) -> Path:
        """상대 경로를 프로젝트 루트 기준으로 편다."""
        p = Path(path)
        return p if p.is_absolute() else (PROJECT_ROOT / p)


@lru_cache
def get_settings() -> Settings:
    return Settings()
