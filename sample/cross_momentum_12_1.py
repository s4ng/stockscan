"""횡단면 모멘텀 12-1 — 이 저장소의 첫 실전 전략.

**파라미터를 백테스트로 고르지 않았습니다.** 12-1은 Jegadeesh-Titman(1993) 이후
30년간 여러 시장·기간에서 반복 확인된 팩터의 표준 정의이고, 여기 적힌 숫자는
전부 그 표준값입니다. 제가 고른 값이 아니므로 **저에게는 out-of-sample입니다** —
제가 골랐다면 일봉 2,500행에 파라미터 3개를 맞춘 것이 되어, 우연히 맞는 조합을
찾았을 뿐인지 구분할 방법이 없습니다 (ARCHITECTURE.md 4.8).

**왜 최근 1개월을 건너뛰는가 (12-1의 '-1')**

단기 반전(short-term reversal) 때문입니다. 최근 한 달 많이 오른 종목은 그 다음
달에 되돌리는 경향이 있어서, 12개월 수익률에 최근 1개월을 포함하면 모멘텀
신호와 반전 효과가 서로를 상쇄합니다. 그래서 **12개월 전부터 1개월 전까지**의
수익률을 씁니다.

    │←──────── lookback 252봉 ────────→│← skip 21봉 →│
    P[t-273]                        P[t-21]        P[t]
                                                    ↑ 오늘 (판단 시점)
    momentum = P[t-21] / P[t-273] - 1

**이 전략이 검증하는 것은 수익성이 아니라 `rank`가 실제로 도는가입니다** (Phase 1).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.strategies import Strategy, top_pct

#: 거래일 기준 근사치 — 12개월 ≈ 252거래일, 1개월 ≈ 21거래일.
#: **팩터의 표준 정의를 시장에 맞춰 바꾸지 않는다.**
#: 정의를 흔들기 시작하면 그 순간부터 파라미터 탐색이 된다 (4.8).
MONTHS_12 = 252
MONTHS_1 = 21


class CrossMomentum121Strategy(Strategy):
    id = "cross_momentum_12_1"
    display_name = "횡단면 모멘텀 12-1"
    timeframe = "1d"

    #: lookback + skip + 1봉. 이보다 짧은 종목은 Strategy Runner가 제외한다 —
    #: 신규 상장 종목이 짧은 이력으로 극단적 모멘텀을 내는 것을 막는다.
    startup_candles = MONTHS_12 + MONTHS_1 + 1

    score_feature = "momentum_12_1"
    score_descending = True

    class Params(BaseModel):
        lookback: int = Field(
            default=MONTHS_12, ge=2, le=1000, description="모멘텀 측정 기간(봉). 표준 252"
        )
        skip: int = Field(
            default=MONTHS_1, ge=0, le=250, description="건너뛸 최근 기간(봉). 표준 21"
        )
        top_pct: float = Field(
            default=0.2, gt=0, le=1, description="상위 비율만 통과. 표준 관행은 상위 10~20%"
        )

    def compute(self, item: Item, p: Params, ctx: RunContext) -> Item:
        close = item.ohlcv["close"]
        needed = p.lookback + p.skip + 1
        if len(close) < needed:
            # 여기 오는 것은 require_startup_candles=False로 껐을 때뿐이다.
            # 점수를 안 채우면 rank_by가 경고와 함께 제외한다.
            return item.with_features(insufficient_bars=len(close))

        # iloc[-1]이 판단 시점의 봉이다. 음수 인덱스만 쓰므로 과거만 본다 —
        # shift(-n) · center=True · bfill이 섞이면 여기서 미래가 샌다 (규칙 3).
        recent = float(close.iloc[-1 - p.skip])
        past = float(close.iloc[-1 - p.skip - p.lookback])
        momentum = recent / past - 1 if past > 0 else float("nan")

        # 건너뛴 1개월 구간. 신호의 근거가 아니라 **해석용**이다 — 이 값이 크면
        # 이미 많이 올라온 종목이라는 뜻이고, explain에서 그게 보여야 한다.
        skipped_return = float(close.iloc[-1] / recent - 1) if p.skip and recent > 0 else 0.0

        return item.with_features(
            momentum_12_1=momentum,
            skipped_return=skipped_return,
            close=float(close.iloc[-1]),
            bars=int(len(close)),
        )

    def select(self, bundle: Bundle, p: Params, ctx: RunContext) -> Bundle:
        # top_pct 헬퍼를 쓰면 절삭 경고가 함께 남는다 (조용한 절삭 금지).
        return top_pct(bundle, p.top_pct, ctx)
