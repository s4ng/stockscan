"""Strategy 프로토콜 (ARCHITECTURE.md 4.2).

v0.4는 전략을 지표 노드의 조합으로 표현했다. v0.5는 **파이썬 클래스 하나**다.
지표 × 파라미터 × 조합은 끝이 없고, 이 시스템의 사용자는 파이썬을 쓰는 본인 한
명이므로 조건 세 개를 AND로 묶는 데 노드 4개가 필요할 이유가 없다.

훅은 셋이고, 축이 다르다.

| 훅        | 축      | 역할                                                |
| :-------- | :------ | :-------------------------------------------------- |
| `compute` | 시계열  | 종목별 지표를 features에 채운다. item을 버리지 않는다 |
| `rank`    | 횡단면  | 유니버스 내 순위·백분위. **이 훅이 중심이다**         |
| `select`  | 횡단면  | 최종 컷. 여기서만 item을 버린다                       |

전략에는 Provider·Cache 핸들을 주지 않는다. 이미 `end`로 잘린 DataFrame만 받으므로
데이터를 통한 미래 참조가 구조적으로 불가능하다.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from app.engine.context import RunContext
from app.engine.types import Bundle, Item

#: rank 기본 구현이 features에 채우는 키. 알림 템플릿과 explain이 이 이름에 의존한다.
RANK_FEATURE = "rank"
PERCENTILE_FEATURE = "percentile"
UNIVERSE_SIZE_FEATURE = "universe_size"


class StrategyError(RuntimeError):
    """전략 로딩·실행 중 발생한 오류."""


class EmptyParams(BaseModel):
    """파라미터가 없는 전략을 위한 기본 모델."""


class Strategy(ABC):
    """전략 1개. 최상위 `strategies/<id>.py`에 이 클래스의 구현체를 하나만 둔다."""

    #: 파일 이름(확장자 제외)과 반드시 같아야 한다. 로더가 강제한다.
    id: ClassVar[str]
    display_name: ClassVar[str] = ""

    #: 판단 단위. 정책 계층에서 1d/1w로 제한된다 (3.6 / 규칙 12).
    timeframe: ClassVar[str] = "1d"

    #: 지표 워밍업에 필요한 봉 수. Market Data의 lookback 산출과 봉 부족 판정에 쓰인다.
    startup_candles: ClassVar[int] = 200

    #: 파라미터 선언. JSON Schema → 폼·`--param` 플래그가 여기서 생성된다.
    Params: ClassVar[type[BaseModel]] = EmptyParams

    #: rank 기본 구현이 정렬 기준으로 삼을 feature 이름.
    #: None이면 기본 rank는 순위를 매기지 않고 유니버스 크기만 남긴다.
    score_feature: ClassVar[str | None] = None

    #: 점수가 큰 쪽이 좋은가. 저변동성·저PBR 팩터는 False로 둔다.
    score_descending: ClassVar[bool] = True

    # ------------------------------------------------------------------ 시계열
    @abstractmethod
    def compute(self, item: Item, params: BaseModel, ctx: RunContext) -> Item:
        """종목 하나의 지표를 계산해 features에 채운 Item을 돌려준다.

        **인과적이어야 한다.** `rolling` · `ewm` · `shift(양수)`는 안전하고,
        `shift(음수)` · `center=True` · `bfill`은 미래를 본다. 후자가 하나라도
        섞이면 4.8의 피처 행렬 사전 계산이 통째로 무너진다.
        `marketscan strategy check`가 AST로 상당 부분 잡지만 통과가 보장은 아니다.
        """

    # ------------------------------------------------------------------ 횡단면
    def rank(self, bundle: Bundle, params: BaseModel, ctx: RunContext) -> Bundle:
        """유니버스 내 순위·백분위를 features에 기록한다 (기본 구현).

        단일 종목 전략은 이 훅을 건드릴 필요가 없다. 여러 종목을 줄 세우는
        전략은 `score_feature`만 선언하면 여기서 처리된다.
        """
        return rank_by(bundle, self.score_feature, ctx, descending=self.score_descending)

    def select(self, bundle: Bundle, params: BaseModel, ctx: RunContext) -> Bundle:
        """최종 컷 (기본 구현: 전량 통과).

        `top_n` · `top_pct` 헬퍼를 쓰면 절삭 경고까지 함께 남는다.
        """
        return bundle

    # ------------------------------------------------------------------ 부가정보
    @classmethod
    def descriptor(cls) -> dict[str, Any]:
        """`marketscan describe` / `strategy list`가 내보내는 요약."""
        return {
            "id": cls.id,
            "display_name": cls.display_name or cls.id,
            "timeframe": cls.timeframe,
            "startup_candles": cls.startup_candles,
            "score_feature": cls.score_feature,
            "params_schema": cls.Params.model_json_schema(),
        }


# --------------------------------------------------------------------------- 헬퍼
def rank_by(
    bundle: Bundle,
    feature: str | None,
    ctx: RunContext,
    *,
    descending: bool = True,
) -> Bundle:
    """`feature` 기준으로 items를 정렬하고 rank·percentile·universe_size를 채운다.

    점수가 없거나 NaN인 item은 **제외하되 반드시 경고를 남긴다** — 조용히 사라지면
    "유니버스 전체를 훑었다"는 전제가 깨진 것을 아무도 모른다.
    """
    universe_size = len(bundle)
    if feature is None:
        # 순위를 매기지 않더라도 표본 수는 남긴다. 횡단면 전략의 신뢰도가 여기서 나온다.
        return bundle.map(lambda it: it.with_features(**{UNIVERSE_SIZE_FEATURE: universe_size}))

    scored: list[tuple[float, Item]] = []
    missing: list[str] = []
    for item in bundle:
        value = item.features.get(feature)
        if not isinstance(value, int | float) or isinstance(value, bool) or math.isnan(value):
            missing.append(item.instrument.key)
            continue
        scored.append((float(value), item))

    if missing:
        ctx.log.warning(
            f"{feature} 점수가 없어 랭킹에서 제외: {', '.join(missing[:20])}"
            f"{f' 외 {len(missing) - 20}건' if len(missing) > 20 else ''}. "
            f"봉이 부족했거나 compute가 값을 채우지 못했습니다."
        )

    scored.sort(key=lambda pair: pair[0], reverse=descending)
    ranked_size = len(scored)
    ranked: list[Item] = []
    for position, (score, item) in enumerate(scored, start=1):
        ranked.append(
            item.with_features(
                **{
                    RANK_FEATURE: position,
                    UNIVERSE_SIZE_FEATURE: ranked_size,
                    PERCENTILE_FEATURE: round(position / ranked_size * 100, 4)
                    if ranked_size
                    else None,
                    "score": score,
                }
            )
        )
    return bundle.replace_items(ranked)


def top_n(bundle: Bundle, n: int, ctx: RunContext) -> Bundle:
    """상위 n개만 남긴다. 이미 rank 순으로 정렬돼 있다고 가정한다."""
    if len(bundle) <= n:
        return bundle
    ctx.log.info(f"상위 {n}개만 통과 ({len(bundle)}개 중 {len(bundle) - n}개 컷)")
    return bundle.replace_items(bundle.items[:n])


def top_pct(bundle: Bundle, pct: float, ctx: RunContext) -> Bundle:
    """상위 pct(0~1) 비율만 남긴다. 최소 1개는 남긴다."""
    if not 0 < pct <= 1:
        raise StrategyError(f"top_pct의 비율은 0 초과 1 이하여야 합니다 (받은 값: {pct})")
    keep = max(1, math.floor(len(bundle) * pct))
    return top_n(bundle, keep, ctx)
