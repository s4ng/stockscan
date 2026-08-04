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

RANK_POOL_FEATURE = "rank_pool"
"""어느 시장 안에서 매긴 순위인가 (규칙 17).

이게 없으면 `explain`의 "순위 7 / 200"이 무엇의 200인지 알 수 없다.
"""


class StrategyError(RuntimeError):
    """전략 로딩·실행 중 발생한 오류."""


class EmptyParams(BaseModel):
    """파라미터가 없는 전략을 위한 기본 모델."""


class Strategy(ABC):
    """전략 1개. 설정 파일 옆(`~/.marketscan/<id>.py`)에 이 클래스의 구현체를 하나만 둔다."""

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
def _by_market(bundle: Bundle) -> dict[str, list[Item]]:
    """★ 시장별로 나눈다 (CLAUDE.md 규칙 17).

    횡단면은 **비교 가능한 집단** 안에서만 성립한다. 코인의 12개월 모멘텀 분포는
    대략 −60%~+300%인데 국내 대형주는 −30%~+60%라, 섞어 정렬하면 분산이 넓은
    코인이 위를 쓸어간다 — **모멘텀이 아니라 변동성으로 줄 세운 것**이 된다.
    `rank`·`percentile`도 함께 거짓말이 된다: "코인 섞인 360마리 중 7등"은
    해석할 수 없는 문장이다.

    키가 `venue`가 아니라 `market`인 것은 `nasdaq`과 `nyse`를 나눌 이유가 없어서다.
    """
    groups: dict[str, list[Item]] = {}
    for item in bundle:
        groups.setdefault(item.instrument.market, []).append(item)
    return groups


def rank_by(
    bundle: Bundle,
    feature: str | None,
    ctx: RunContext,
    *,
    descending: bool = True,
) -> Bundle:
    """`feature` 기준으로 **시장별로** 정렬하고 rank·percentile·universe_size를 채운다.

    점수가 없거나 NaN인 item은 **제외하되 반드시 경고를 남긴다** — 조용히 사라지면
    "유니버스 전체를 훑었다"는 전제가 깨진 것을 아무도 모른다.
    """
    if feature is None:
        # 순위를 매기지 않더라도 표본 수는 남긴다. 횡단면 전략의 신뢰도가 여기서 나온다.
        groups = _by_market(bundle)
        return bundle.map(
            lambda it: it.with_features(
                **{
                    UNIVERSE_SIZE_FEATURE: len(groups[it.instrument.market]),
                    RANK_POOL_FEATURE: it.instrument.market,
                }
            )
        )

    scored: dict[str, list[tuple[float, Item]]] = {}
    missing: list[str] = []
    for item in bundle:
        value = item.features.get(feature)
        if not isinstance(value, int | float) or isinstance(value, bool) or math.isnan(value):
            missing.append(item.instrument.key)
            continue
        scored.setdefault(item.instrument.market, []).append((float(value), item))

    if missing:
        ctx.log.warning(
            f"{feature} 점수가 없어 랭킹에서 제외: {', '.join(missing[:20])}"
            f"{f' 외 {len(missing) - 20}건' if len(missing) > 20 else ''}. "
            f"봉이 부족했거나 compute가 값을 채우지 못했습니다."
        )

    ranked: list[Item] = []
    for market in sorted(scored):
        pool = sorted(scored[market], key=lambda pair: pair[0], reverse=descending)
        pool_size = len(pool)
        if len(scored) > 1:
            ctx.log.info(f"랭킹 풀 {market}: {pool_size}종목")
        for position, (score, item) in enumerate(pool, start=1):
            ranked.append(
                item.with_features(
                    **{
                        RANK_FEATURE: position,
                        UNIVERSE_SIZE_FEATURE: pool_size,
                        # 어느 풀에서 매긴 순위인지가 남아야 explain이 정직하다.
                        RANK_POOL_FEATURE: market,
                        PERCENTILE_FEATURE: round(position / pool_size * 100, 4)
                        if pool_size
                        else None,
                        "score": score,
                    }
                )
            )
    return bundle.replace_items(ranked)


def top_n(bundle: Bundle, n: int, ctx: RunContext) -> Bundle:
    """**시장마다** 상위 n개씩 남긴다. 이미 rank 순으로 정렬돼 있다고 가정한다.

    컷도 시장별이어야 한다(규칙 17). 섞어 자르면 분산 넓은 시장이 자리를 다 가져가
    나머지 시장이 통째로 사라진다 — 그 순간 "멀티마켓"은 이름만 남는다.
    """
    kept: list[Item] = []
    for market, items in _by_market(bundle).items():
        if len(items) > n:
            ctx.log.info(f"{market}: 상위 {n}개만 통과 ({len(items)}개 중 {len(items) - n}개 컷)")
        kept.extend(items[:n])
    return bundle.replace_items(kept)


def top_pct(bundle: Bundle, pct: float, ctx: RunContext) -> Bundle:
    """**시장마다** 상위 pct(0~1) 비율씩 남긴다. 시장당 최소 1개는 남긴다."""
    if not 0 < pct <= 1:
        raise StrategyError(f"top_pct의 비율은 0 초과 1 이하여야 합니다 (받은 값: {pct})")
    kept: list[Item] = []
    for market, items in _by_market(bundle).items():
        keep = max(1, math.floor(len(items) * pct))
        if len(items) > keep:
            ctx.log.info(
                f"{market}: 상위 {pct:.0%}({keep}개)만 통과 "
                f"({len(items)}개 중 {len(items) - keep}개 컷)"
            )
        kept.extend(items[:keep])
    return bundle.replace_items(kept)
