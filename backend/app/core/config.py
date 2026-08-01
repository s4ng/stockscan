"""애플리케이션 설정.

Self-hosted 단일 사용자 전제이므로 설정은 환경변수 하나로 끝낸다.
민감한 값(마스터 키, API 토큰)은 절대 기본값을 두지 않는다.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="TRADEFLOW_", extra="ignore"
    )

    app_name: str = "tradeflow"
    debug: bool = False

    #: 기본은 로컬 바인딩. 외부 노출 시 반드시 리버스 프록시 + 인증을 앞에 둘 것.
    host: str = "127.0.0.1"
    port: int = 8000

    database_url: str = "sqlite+aiosqlite:///./data/tradeflow.db"

    #: 프론트엔드 개발 서버 오리진
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    #: 자격 증명 암호화 마스터 키. 미설정 시 Connection 저장 기능이 비활성화된다.
    #: 이 키를 잃으면 저장된 API 키를 복호화할 수 없다 (백업 대상).
    master_key: str | None = None

    #: 전역 실주문 차단. Phase 5 전까지는 항상 False.
    live_trading_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
