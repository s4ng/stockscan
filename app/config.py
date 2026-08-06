"""설정 — 사람이 적는 것 전부 (ARCHITECTURE.md 6).

**2026-08-06에 DAG 정의를 걷어냈다.** 예전 `config.yml`은 노드 6개와 엣지 5개를
YAML로 적는 파일이었는데, **조합이 하나뿐이라 그 형식이 값을 못 했다** — 두 개의
설정 파일이 똑같은 배선을 갖고 있었고 다른 것은 전략 하나였다. 배선은 코드로
내려갔고(`app/pipeline.py`), 여기에는 **실제로 사람이 정하는 것만** 남는다.

    수집할 시장과 폭 · 전략 · 실행 시각 · 알림 채널

나머지는 전부 유도하거나 고정한다.

| 사라진 것 | 어디로 갔나 |
| :--- | :--- |
| `nodes` · `edges` · `pipeline_id` · `name` · `version` | `app/pipeline.py`에 고정 |
| `timeframe` · `closed_only` · `skip_stale` · `source` | 고정값 (판단 단위는 `1d`뿐 — 규칙 12) |
| `adjusted` · `max_concurrency` · `kind` · `on_error` | 고정값 |
| `lookback` | ★ **전략의 `startup_candles`에서 유도** |
| 전략 `params` | ★ **전략 파일의 `Params` 기본값이 정본** |
| `at[].market` | 시장을 나누지 않는다 — Fresh Bar Gate가 알아서 거른다 |

★ **`lookback`을 유도하는 것이 특히 중요하다.** 예전에는 `lookback: 320`을 손으로
적고 옆에 "전략이 253봉을 요구합니다"라고 주석을 달았는데, **전략을 바꾸면 그 둘이
어긋나고 어긋난 종목은 워밍업 부족으로 조용히 전량 제외된다.** "전략이 있는데 신호가
0건"이 가장 흔한 사고였던 이유가 이것이다.

★ **파라미터가 설정에서 사라진 것도 의도다** (4.8). 값이 전략 파일 안에 있으면
"왜 이 값인가"를 적은 docstring 바로 옆에 놓이고, 설정 파일에서 슬쩍 바꿔 돌려 보는
경로가 없어진다. 파라미터를 바꾸는 것은 전략을 고치는 일이어야 한다.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.config import SAMPLE_DIR, get_settings
from app.market.instrument import VENUES, markets, venues_of

#: 워밍업 위에 얹는 여유 봉.
#:
#: 정확히 `startup_candles`만 받으면 **경계에 서게 된다** — 소스가 휴장일·상장 공백
#: 때문에 요청보다 적게 주는 일이 흔하고, 그러면 그 종목이 통째로 빠진다. 여유를
#: 두는 비용은 캐시 몇 줄이고, 없을 때의 비용은 조용한 전량 제외다.
LOOKBACK_MARGIN = 60

#: 판단 단위. 규칙 12 — 분봉은 정책 계층에서 막고 타입은 건드리지 않는다.
TIMEFRAME = "1d"


class ConfigError(ValueError):
    """설정을 읽거나 해석하지 못했을 때. 종료 코드 3(검증 실패)으로 이어진다."""


class ScheduleConfig(BaseModel):
    """언제 돌 것인가.

    **시각은 로컬 기준으로 적는다.** 마감 시각에서 유도하지 않는 이유는 서머타임이다 —
    미국장 마감은 한국 시각으로 1년에 두 번 한 시간씩 움직이는데, 유도한 값은 그
    사실을 어디에도 남기지 않는다.

    ⚠️ **시장을 나누지 않는다.** 예전에는 슬롯마다 `market: krx`를 달아 그 시장만
    돌렸는데, Fresh Bar Gate(3.5)가 어차피 마감되지 않은 시장을 제외하므로 두 번
    거르는 것이었다. 시각만 적으면 그 시각에 새 봉이 있는 시장이 알아서 판정된다.
    """

    at: list[time] = Field(default_factory=list, description='실행 시각. 예: ["15:40", "06:10"]')
    heartbeat: time | None = Field(
        default=None,
        description="생존 신고 시각. ★ 없으면 '신호 0건'과 '프로세스 사망'이 구분되지 않습니다",
    )

    @field_validator("at")
    @classmethod
    def _unique(cls, value: list[time]) -> list[time]:
        # 같은 시각이 두 번 있으면 그 슬롯이 두 번 발화해 봉을 두 번 소비한다.
        return sorted(set(value))


class TelegramConfig(BaseModel):
    """알림 채널.

    ⚠️ **원래 규칙 7은 "키를 설정 파일에 넣지 않는다"였다** — 설정 파일은 복사·공유되기
    때문이다. 2026-08-06에 사용자 결정으로 뒤집었다: 이 파일은 저장소 바깥
    (`~/.marketscan/`)에 살고 개인용 self-hosted라 공유 경로가 없으며, 파일 하나로
    끝나는 쪽이 실제로 쓰기 쉽다.

    **대신 두 가지를 지킨다.** 저장소의 예제에는 플레이스홀더만 두고, 환경변수를
    덮어쓰기 경로로 남긴다 — 설정을 백업·공유해야 할 때 값을 비우고 환경변수로
    넘길 수 있어야 한다.
    """

    token: str = ""
    chat_id: str = ""

    def resolved(self) -> tuple[str, str]:
        """설정값이 우선, 비었거나 플레이스홀더면 환경변수.

        `<봇 토큰>` 같은 예제 값을 그대로 두고 실행하는 일이 흔한데, 그걸 진짜
        토큰으로 취급하면 매 전송이 실패하면서 원인이 "토큰이 틀렸다"로 보인다.
        """
        settings = get_settings()
        token = _real(self.token) or (settings.telegram_token or "").strip()
        chat_id = _real(self.chat_id) or (settings.telegram_chat_id or "").strip()
        return token, chat_id


def _real(value: str) -> str:
    """플레이스홀더는 빈 값으로 취급한다."""
    text = (value or "").strip()
    return "" if not text or text.startswith("<") else text


class AppConfig(BaseModel):
    """`~/.marketscan/config.yml`의 전부."""

    timezone: str = Field(default="Asia/Seoul", description="표시용. 저장은 항상 UTC")

    universe: dict[str, int] = Field(
        default_factory=dict,
        description="venue → 훑을 종목 수. 예: {krx: 200, nasdaq: 100}",
    )
    strategy: str = Field(default="", description="설정 파일과 같은 디렉터리의 <id>.py")
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    @field_validator("universe")
    @classmethod
    def _known_venues(cls, value: dict[str, int]) -> dict[str, int]:
        for venue, size in value.items():
            if venue not in VENUES:
                raise ValueError(
                    f"알 수 없는 venue: {venue!r}. 지원 목록: {', '.join(sorted(VENUES))}"
                )
            if size < 1:
                raise ValueError(f"{venue}의 종목 수는 1 이상이어야 합니다 (받은 값: {size})")
        return value

    # ------------------------------------------------------------------ 유도값
    @property
    def pipeline_id(self) -> str:
        """실행 이력과 `dedup_key`가 쓰는 식별자 (4.5).

        전략에서 유도한다 — 전략이 곧 파이프라인의 정체이기 때문이고, 두 전략을
        나란히 돌려도 `signals`가 섞이지 않는다.
        """
        return f"pipe_{self.strategy}" if self.strategy else "pipe_unnamed"

    def venue_of(self, market: str) -> dict[str, int]:
        """이 시장에 속한 venue만 남긴 유니버스. `--market` 필터가 쓴다."""
        allowed = set(venues_of(market))
        return {v: n for v, n in self.universe.items() if v in allowed}

    def for_market(self, market: str) -> AppConfig:
        if market not in markets():
            raise ConfigError(
                f"알 수 없는 시장: {market!r}. 사용 가능: {', '.join(markets())}"
            )
        return self.model_copy(update={"universe": self.venue_of(market)}, deep=True)

    def describe_universe(self) -> str:
        """`describe`의 사람용 한 줄."""
        if not self.universe:
            return "미지정"
        return " + ".join(
            f"{venue} {'거래대금 상위' if uses_turnover(venue) else '목록 상위'} {size}"
            for venue, size in self.universe.items()
        )

    def snapshot(self) -> dict[str, Any]:
        """`pipeline_versions`에 남길 직렬화 (규칙 10).

        ⚠️ **토큰은 빼고 남긴다.** 실행 이력은 백업·공유 대상이고, 비밀이 거기까지
        따라가면 설정 파일 하나만 조심해서는 막을 수 없게 된다.
        """
        data = self.model_dump(mode="json")
        data.pop("telegram", None)
        return data


def uses_turnover(venue: str) -> bool:
    """이 venue의 종목 목록에 거래대금이 실려 오는가.

    KRX 목록(FDR)은 `Amount`를 주지만 **미국 목록에는 거래대금이 아예 없다.** 그래서
    한쪽은 거래대금 상위 N, 다른 쪽은 목록 앞 N으로 자른다. 예전에는 이 구분을
    설정에 적었는데(`top_by_turnover` vs `limit`), 소스의 능력이지 사람이 정할 일이
    아니라 여기로 내렸다.

    ⚠️ 거래대금이 없는 venue에 거래대금 컷을 걸면 **그 시장이 통째로 사라진 채
    실행이 성공한다.** 그래서 조용히 0으로 취급하지 않고 이 표로 갈라 받는다.
    """
    return venue == "krx"


def lookback_for(startup_candles: int) -> int:
    """전략이 요구하는 워밍업에서 수집 깊이를 유도한다."""
    return startup_candles + LOOKBACK_MARGIN


# --------------------------------------------------------------------------- 로딩
def default_path() -> Path:
    """기본 설정 파일. `~/.marketscan/config.yml`."""
    settings = get_settings()
    return settings.resolve(settings.config_path)


def load(path: Path | None = None) -> AppConfig:
    """설정을 읽는다. **전략은 이 파일 옆에서 찾는다.**

    설정과 전략은 한 벌이라 통째로 복사·백업할 수 있어야 한다.
    """
    from app.strategies.registry import bind_config_dir

    target = path.expanduser() if path else default_path()
    if not target.is_file():
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {target}. "
            f"예제를 그대로 쓰려면 저장소의 {SAMPLE_DIR} 안의 파일을 "
            f"{get_settings().config_dir}로 복사하세요 "
            f"(설정과 전략이 같은 디렉터리에 있어야 합니다). "
            f"--config로 경로를 지정하거나 MARKETSCAN_CONFIG_PATH를 설정해도 됩니다."
        )

    bind_config_dir(target)

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{target}의 형식이 잘못됐습니다 — {exc}") from exc

    if raw is None:
        raise ConfigError(f"{target}이(가) 비어 있습니다. strategy와 universe를 적으세요.")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{target}의 최상위는 매핑이어야 합니다 (읽은 값: {type(raw).__name__})."
        )

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise ConfigError(f"{target}의 설정이 맞지 않습니다 — {details}") from exc

    if not config.strategy:
        raise ConfigError(
            f"{target}에 strategy가 없습니다. 같은 디렉터리의 전략 파일 이름을 적으세요 "
            f"(예: strategy: trend_breakout_55)."
        )
    if not config.universe:
        raise ConfigError(
            f"{target}에 universe가 없습니다. 훑을 시장과 종목 수를 적으세요 "
            f"(예: universe: {{krx: 200, nasdaq: 100}})."
        )
    return config
