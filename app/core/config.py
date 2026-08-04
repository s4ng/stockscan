"""애플리케이션 설정.

Self-hosted 단일 사용자 전제이므로 설정은 환경변수 하나로 끝낸다.
민감한 값(마스터 키, API 토큰)은 절대 기본값을 두지 않는다.

경로 기본값은 모두 **상대 경로**이고, 쓰는 쪽에서 `settings.resolve()`로 `~/.marketscan`
기준의 절대 경로로 편다. 어느 디렉터리에서 CLI를 부르든 같은 파일을 보아야 하기
때문이다 — 펴지 않으면 `cd` 한 번에 DB가 새로 생기고, 사용자는 캐시와 신호가 통째로
비어 있는 것을 보게 된다.

**저장소 안에는 사용자 자산을 두지 않는다.** 설정·전략·DB·리포트가 전부 홈으로 나가
있어야 코드를 지우고 다시 받아도 살아남는다 — 특히 `ohlcv_cache`는 무료 소스가 막혀도
남는 유일한 자산이라(3.9) 저장소와 수명을 같이하면 안 된다. 저장소의 `sample/`은
설정·전략 한 벌의 예제일 뿐이다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: 이 파일 기준 프로젝트 루트 (app/core/config.py → app/core → app → 루트)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 사용자 설정·전략의 정본 위치. 저장소 바깥이라 재설치·재클론에도 살아남는다.
DEFAULT_CONFIG_DIR = Path.home() / ".marketscan"

#: 저장소에 든 예제 설정·전략. 여기서 `config_dir`로 복사해 쓴다 (정본이 아니다).
SAMPLE_DIR = PROJECT_ROOT / "sample"


class Settings(BaseSettings):
    #: `.env`를 **두 곳에서** 읽는다. 뒤엣것이 이긴다.
    #:
    #: 상대 경로 `.env`는 **실행한 디렉터리** 기준이라, 다른 데서 `serve`를 띄우면
    #: 토큰이 조용히 안 잡힌다 — 그러면 알림이 안 오는데 이유가 화면에 없다.
    #: 설정·전략·DB가 이미 `~/.marketscan`에 사니 비밀도 거기 두는 쪽이 맞다.
    model_config = SettingsConfigDict(
        env_file=(".env", str(DEFAULT_CONFIG_DIR / ".env")),
        env_prefix="MARKETSCAN_",
        extra="ignore",
    )

    app_name: str = "marketscan"
    debug: bool = False

    #: SQLite 상대 경로는 `config_dir` 기준으로 편다 — `~/.marketscan/data/marketscan.db`.
    #: `ohlcv_cache`가 여기 산다. 저장소를 지워도 남아야 하는 파일이다 (3.9).
    database_url: str = "sqlite+aiosqlite:///./data/marketscan.db"

    #: 설정·전략·DB·리포트가 사는 디렉터리. 기본 `~/.marketscan`.
    config_dir: Path = DEFAULT_CONFIG_DIR

    #: 사용자 전략 파일의 정본 위치. git으로 관리하고 소스 해시가 실행에 박힌다 (4.7).
    #:
    #: **기본은 None이고, 그때는 실행 중인 파이프라인 파일과 같은 디렉터리에서 찾는다**
    #: (`strategies.registry.strategies_dir`). 설정과 전략을 함께 옮길 수 있어야
    #: `-p sample/demo.yaml`이 저장소의 예제 전략을 그대로 집는다 — 설정만 옮기고
    #: 전략은 홈에 남으면 예제가 반쪽이 된다. 값을 주면 그쪽이 이긴다.
    strategies_dir: Path | None = None

    #: 실행할 파이프라인 정의. 형식은 **YAML로 확정**됐다 (11장 4번, Phase 1에서 해소).
    #: 로더가 확장자로 갈라 받으므로 기존 `.json` 파일도 그대로 읽힌다.
    #: 상대 경로는 `config_dir` 기준이다 — 기본값은 `~/.marketscan/config.yml`.
    pipeline_path: Path = Path("config.yml")

    #: 백테스트·실행 리포트 산출물. 서빙하지 않고 파일로 떨어뜨린다 (2.1).
    #: `config_dir` 기준 — 재생성 가능하지만 `--commit` 리포트는 실행 이력이라
    #: DB와 같은 곳에 있어야 함께 백업된다.
    reports_dir: Path = Path("reports")

    #: 텔레그램 알림. **파이프라인 정의에 넣지 않는다** (규칙 7) — 설정 파일을
    #: 그대로 복사·공유해도 비밀이 새지 않아야 한다. 둘 다 있어야 채널이 열리고,
    #: 없으면 `serve`는 알림을 **기록만** 한다 (미구현을 성공처럼 보이게 하지 않는다).
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    #: 자격 증명 암호화 마스터 키. 미설정 시 Connection 저장 기능이 비활성화된다.
    #: 이 키를 잃으면 저장된 API 키를 복호화할 수 없다 (백업 대상).
    master_key: str | None = None

    #: 전역 실주문 차단. Phase 5 전까지는 항상 False.
    live_trading_enabled: bool = False

    def resolve(self, path: Path | str) -> Path:
        """상대 경로를 `config_dir` 기준으로 편다 (사용자 자산: 설정·전략·DB·리포트).

        `~`를 편다 — 환경변수로 `~/.marketscan/other.yml`을 넘겨도 그대로 문자열이라
        `Path("~")`라는 이름의 디렉터리를 찾게 된다.
        """
        p = Path(path).expanduser()
        return p if p.is_absolute() else (Path(self.config_dir).expanduser() / p)


@lru_cache
def get_settings() -> Settings:
    return Settings()
