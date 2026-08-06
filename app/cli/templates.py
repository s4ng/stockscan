"""`stockscan strategy new`가 찍어 내는 전략 템플릿.

`Params`를 미리 넣어 두는 이유는 4.2 규칙 3 — 노드 방식의 유일한 실질적 이득이던
"JSON Schema → 폼 자동 생성"을 그대로 유지하기 위해서다. 코드를 고치지 않고
파라미터를 바꿀 수 있어야 한다.
"""

from __future__ import annotations

STRATEGY_TEMPLATE = '''"""{strategy_id} — (전략 설명을 여기에 적으세요)

체크리스트
  1. compute는 **인과적**이어야 한다. rolling · ewm · shift(양수)는 안전하고
     shift(음수) · center=True · bfill은 미래를 본다.
  2. 파라미터는 백테스트로 뒤져서 고르지 않는다. 검증된 팩터의 표준값을 쓴다.
  3. 다 쓰면 `stockscan strategy check {strategy_id}`를 돌린다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.strategies import Strategy, top_pct


class {strategy_id}Strategy(Strategy):
    id = "{strategy_id}"
    display_name = "{strategy_id}"

    #: 판단 단위는 1d / 1w 뿐이다 (규칙 12).
    timeframe = "1d"

    #: 지표 워밍업에 필요한 봉 수. 부족한 종목은 Strategy Runner가 제외한다.
    startup_candles = 60

    #: rank 기본 구현이 이 feature로 유니버스를 줄 세운다.
    score_feature = "score"

    class Params(BaseModel):
        lookback: int = Field(default=20, ge=2, le=500, description="점수 계산 기간(봉)")
        top_pct: float = Field(default=0.1, gt=0, le=1, description="상위 비율만 통과")

    def compute(self, item: Item, p: Params, ctx: RunContext) -> Item:
        close = item.ohlcv["close"]
        # 예시 — 최근 lookback봉 수익률. 실제 팩터로 바꾸세요.
        score = float(close.iloc[-1] / close.iloc[-p.lookback] - 1)
        return item.with_features(score=score)

    def select(self, bundle: Bundle, p: Params, ctx: RunContext) -> Bundle:
        return top_pct(bundle, p.top_pct, ctx)
'''
