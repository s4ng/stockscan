"""추세 안의 짧은 조정, 그 재출발 — 데이브 랜드리의 눌림목.

`trend_breakout_55`가 **신고가를 찍는 순간**을 잡는다면, 이 전략은 **이미 추세인
종목이 잠깐 쉬었다가 다시 출발하는 순간**을 잡습니다. 같은 추세추종인데 사는
자리가 반대입니다 — 저쪽은 제일 높은 곳에서 사고, 이쪽은 그 직전 조정이 끝나는
곳에서 삽니다.

`trend_breakout_55`의 docstring이 "0건이 이어진다고 파라미터를 낮추면 그 순간부터
추세추종이 아니라 눌림목 매매가 됩니다 — 그건 이 파일이 아니라 **다른 전략 파일에
써야 합니다**"라고 적어 둔, 그 다른 파일입니다. 저쪽을 무르게 만드는 대신 여기를
새로 만들었습니다.

**게이트 넷을 모두 통과해야 후보입니다.**

    1. 추세 자격   SMA(10) > EMA(20) > EMA(30) 정배열, 종가 > EMA(30)
    2. 조정 시작점 조정이 시작된 고점이 직전 20봉 최고가
    3. 조정        직전 봉까지 연속 저고점(lower high) 2~5봉
    4. 재출발      오늘 종가 > 직전 봉 고가(피벗)

                 ┌ 고점 — 직전 20봉 최고가여야 한다 (게이트 2)
                 │
                 ●╮      ← 조정 3봉 (게이트 3)
                 │ ╰●╮
                 │   ╰●  ← 피벗 = **직전 봉**의 고가 (게이트 4)
                 │    ╰──●  오늘 종가가 피벗 위면 재출발
        ─────────┴──────────
                   ╰ 손절 = 조정 구간 최저 저가

★ **재출발 없이는 신호가 아닙니다.** 게이트 4를 빼면 그냥 내려가는 중인 종목을
사게 됩니다. 랜드리가 반복해서 강조하는 것이 그것이고, 동시에 이 파일에서 **가장
먼저 무르게 만들고 싶어지는 조건**입니다 — 조정만 보고 미리 알려 주면 더 싸게 살
수 있을 것 같기 때문입니다. 그 유혹이 왜 틀렸는지가 바로 아래입니다.

★★ **왜 종가로 판정하나 — 성적표가 여기에 걸려 있습니다.**

랜드리의 진입은 **직전 봉 고가 위에 걸어 둔 장중 매수 스톱**입니다. 이 시스템은
마감된 일봉으로만 판단하므로(4.4) 그대로 옮길 수 없었고, 선택지가 셋이었습니다.

  (A) 조정이 끝난 날 "내일 피벗 위에 스톱을 걸어라"고 미리 알린다
      → ❌ `evaluate`는 **신호 봉의 종가**를 기준가로 잡습니다. 실제로는 스톱에
         걸리지 않았을 신호까지 전부 산 것으로 계산되고, 하필 안 걸리는 쪽이
         조정이 그대로 이어진 종목이라 성적표가 다른 것을 재게 됩니다.

  (B) 오늘 **고가**가 피벗을 넘었으면 신호 — 랜드리의 체결에 가장 가깝다
      → ❌ **가장 위험합니다.** 넘었다가 되돌려 마감한 날, 실제 체결가는 피벗인데
         기준가는 그보다 낮은 종가라 **사후 수익률이 실제보다 좋게 나옵니다.**
         정확히 이 저장소가 막으려는 자신감 기계입니다.

  (C) 오늘 **종가**가 피벗을 넘었으면 신호 ✅
      → 기준가(종가) ≥ 실제로 체결됐을 가격(피벗)이라 **틀려도 보수적인 쪽으로만
         틀립니다.** 새로 도입하는 가정이 하나도 없습니다.

`trend_breakout_55`도 같은 이유로 종가 돌파를 씁니다. **C를 A나 B로 바꾸지
않습니다** — 바꾸려면 `evaluate`의 기준가부터 손봐야 하고, 그건 전략 파일에서 할
일이 아닙니다.

⚠️ **대신 랜드리보다 한 박자 늦게 삽니다.** 그 차이를 숨기지 않고
`entry_slippage_pct`(종가 ÷ 피벗 − 1)로 매번 남깁니다. 이 값이 크면 갭으로 뛰어넘은
날이라 손절까지의 거리가 그만큼 멀어집니다.

★ **파라미터는 이 파일이 정본입니다** (설정 파일에는 전략 이름만 적습니다).
**백테스트로 고르지 않았습니다** (4.8) — 전부 남이 공개한 값이라 저에게는
out-of-sample입니다.

  - SMA(10) · EMA(20) · EMA(30) — 랜드리가 추세 자격에 쓰는 이동평균 3종 세트
  - 조정 2~5봉 — 랜드리의 "1-2-3"(2~3봉)과 Persistent Pullback(4봉 이상)을 합친 폭
  - 피벗 = 직전 봉 고가 — 랜드리의 매수 스톱 위치 그대로
  - 손절 = 조정 구간 최저 저가 — 랜드리의 손절 위치 그대로
  - ADX · ATR 14 — Wilder(1978)의 표준 기간

⚠️ **`swing_high = 20`만 출처가 약합니다.** 랜드리는 "고점에서의 조정"이라고만 하고
몇 봉인지 적지 않았습니다. 20봉(≈1개월)은 관습값이고 이 저장소에도 이미 있습니다
(Turtle 청산 채널). **이 파일에서 제일 먼저 의심할 값이라 여기 적어 둡니다** —
조용히 두면 나중에 "원래 그런 값"이 됩니다.

**랭킹은 ADX(14)입니다.** 랜드리 본인이 추세 자격에 ADX를 쓰고 "높을수록 좋은
추세"로 봅니다. 조정 폭이나 반등 크기로 줄 세우고 싶어지는데, 그건 추세가 강한
순서가 아니라 **많이 움직인 순서**라 변동성으로 정렬한 것이 됩니다 (규칙 17이
시장을 나누는 것과 같은 사고입니다).

**이 전략은 진입만 봅니다.** 청산은 사람이 하되 판단에 필요한 숫자는 남깁니다 —
`stop_swing`(랜드리의 손절)과 `stop_2atr`(변동성 기준 비교값).

⚠️ **이것도 상태가 아니라 사건입니다.** 조정 자체는 흔하지만 **재출발이 오늘
일어나야** 후보가 되므로 신호 0건인 날이 흔합니다. 55일 신고가를 요구하지 않으니
`trend_breakout_55`보다는 자주 나겠지만, **얼마나 자주인지는 아직 모릅니다** —
쌓이기 전에 짐작을 적어 두면 그게 나중에 사실 행세를 합니다. `top_n` 기본값 5는
그 짐작이 아니라 알림 한 통에 읽을 만한 길이에서 온 값이고, 전략 파라미터가 아니라
**화면 컷이라 손대도 됩니다.**
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.strategies import Strategy, top_n

#: 랜드리의 추세 자격 — 이동평균 3종. 빠른 것만 SMA이고 나머지는 EMA다.
TREND_FAST = 10
TREND_MID = 20
TREND_SLOW = 30

#: 조정이 시작된 고점을 "최근 고점"으로 인정할 범위. ⚠️ 위 docstring 참고 —
#: 이 파일에서 출처가 가장 약한 값이다.
SWING_HIGH = 20

#: 조정 길이. 2~3봉이 랜드리의 "1-2-3", 4봉 이상이 Persistent Pullback이다.
#: 6봉을 넘어가면 조정이 아니라 추세가 꺾인 것으로 본다.
MIN_PULLBACK = 2
MAX_PULLBACK = 5

#: Wilder(1978)의 표준 기간. ADX와 ATR이 같은 값을 쓴다 — 원문이 그렇다.
WILDER = 14
STOP_ATR = 2.0


class LandryPullbackStrategy(Strategy):
    id = "landry_pullback"
    display_name = "랜드리 눌림목 (추세 조정 후 재출발)"
    timeframe = "1d"

    #: EMA는 창이 잘려도 값이 나오지만 **수렴하지 않은 값이 나온다.** 가장 긴
    #: EMA(30)의 5배를 워밍업으로 잡는다. Wilder 평활(ADX)은 이중이라 더 느린데
    #: 14×10 = 140이 그 안에 들어온다.
    startup_candles = max(TREND_SLOW * 5, WILDER * 10, SWING_HIGH + MAX_PULLBACK + 2)

    score_feature = "adx"
    score_descending = True

    class Params(BaseModel):
        trend_fast: int = Field(
            default=TREND_FAST, ge=2, le=200, description="추세 자격 단기 SMA(봉). 표준 10"
        )
        trend_mid: int = Field(
            default=TREND_MID, ge=2, le=200, description="추세 자격 중기 EMA(봉). 표준 20"
        )
        trend_slow: int = Field(
            default=TREND_SLOW, ge=2, le=200, description="추세 자격 장기 EMA(봉). 표준 30"
        )
        swing_high: int = Field(
            default=SWING_HIGH, ge=2, le=200, description="조정 시작 고점을 볼 범위(봉). 관습값 20"
        )
        min_pullback: int = Field(
            default=MIN_PULLBACK, ge=1, le=20, description="최소 조정 길이(봉). 랜드리 1-2-3"
        )
        max_pullback: int = Field(
            default=MAX_PULLBACK,
            ge=1,
            le=20,
            description="최대 조정 길이(봉). 넘으면 조정이 아니라 추세가 꺾인 것",
        )
        wilder: int = Field(
            default=WILDER, ge=2, le=200, description="ADX·ATR 기간(봉). Wilder 표준 14"
        )
        stop_atr: float = Field(
            default=STOP_ATR, gt=0, le=10, description="비교용 손절 거리(ATR 배수). 표준 2N"
        )
        top_n: int = Field(
            default=5,
            ge=1,
            le=200,
            description="시장당 최대 후보 수. 전략 파라미터가 아니라 화면 컷입니다",
        )

    # ------------------------------------------------------------------ 시계열
    def compute(self, item: Item, p: Params, ctx: RunContext) -> Item:
        frame = item.ohlcv
        close = frame["close"]
        if len(close) < _needed(p):
            # require_startup_candles=False로 껐을 때만 여기 온다.
            # 점수를 안 채우면 rank_by가 경고와 함께 제외한다.
            return item.with_features(insufficient_bars=len(close))

        # iloc[-1]이 판단 시점의 **마감된** 봉이다. 아래는 전부 음수 인덱스와
        # rolling·ewm·diff뿐이라 과거만 본다 (규칙 3).
        high, low = frame["high"], frame["low"]
        last_close = float(close.iloc[-1])

        # ---- 게이트 1: 추세 자격 --------------------------------------------
        sma_fast = float(close.rolling(p.trend_fast).mean().iloc[-1])
        ema_mid = float(close.ewm(span=p.trend_mid, adjust=False).mean().iloc[-1])
        ema_slow = float(close.ewm(span=p.trend_slow, adjust=False).mean().iloc[-1])
        trend_ok = sma_fast > ema_mid > ema_slow and last_close > ema_slow

        # ---- 게이트 3: 조정 --------------------------------------------------
        pullback_bars = _pullback_bars(high, p.max_pullback)
        pullback_ok = p.min_pullback <= pullback_bars <= p.max_pullback

        # ---- 게이트 2: 조정 시작점이 최근 고점인가 ----------------------------
        # 조정이 n봉이면 그 앞 봉(iloc[-2-n])이 조정이 시작된 고점이다.
        peak_pos = len(high) - 2 - pullback_bars
        peak_high = float(high.iloc[peak_pos])
        window_from = max(0, peak_pos - p.swing_high + 1)
        # 고점 자신까지만 본다. 오늘을 창에 넣으면 재출발한 종목이 스스로를 밀어내
        # "고점이 아니었다"가 되고, 게이트 2가 조용히 항상 거짓이 된다.
        peak_is_high = peak_high >= float(high.iloc[window_from : peak_pos + 1].max())

        # ---- 게이트 4: 재출발 -------------------------------------------------
        # 피벗은 **직전 봉**의 고가다. 랜드리가 매수 스톱을 거는 자리 그대로.
        pivot = float(high.iloc[-2])
        resumed = last_close > pivot

        # 손절 — 조정 봉들과 오늘 중 가장 낮은 저가 (랜드리는 조정 저점 아래에 둔다).
        swing_low = float(low.iloc[peak_pos + 1 :].min())

        atr = float(_atr(frame, p.wilder).iloc[-1])
        adx = float(_adx(frame, p.wilder).iloc[-1])

        # ⚠️ 앞의 셋이 텔레그램 알림 한 줄에 실린다 (serve.py의 `_reason`은 순위 키를
        #    뺀 나머지를 **앞에서부터** 싣는다). 순서를 바꾸면 알림이 조용히 빈약해진다.
        return item.with_features(
            adx=adx,
            pullback_bars=pullback_bars,
            risk_pct=(last_close - swing_low) / last_close if last_close > 0 else float("nan"),
            # --- 이하는 explain·리포트가 본다 ---
            trend_ok=trend_ok,
            sma_fast=sma_fast,
            ema_mid=ema_mid,
            ema_slow=ema_slow,
            peak_is_high=peak_is_high,
            peak_high=peak_high,
            pullback_ok=pullback_ok,
            # 조정이 얼마나 깊었나. **게이트가 아니라 해석용이다** — 랜드리가 깊이에
            # 수치를 주지 않았고, 여기서 내가 정하면 그게 내가 고른 파라미터가 된다.
            pullback_depth_pct=(
                (peak_high - swing_low) / peak_high if peak_high > 0 else float("nan")
            ),
            resumed=resumed,
            pivot=pivot,
            # 랜드리는 피벗에서 체결되지만 이 시스템은 종가에 산다. 그 차이를 남긴다.
            entry_slippage_pct=last_close / pivot - 1 if pivot > 0 else float("nan"),
            stop_swing=swing_low,
            stop_2atr=last_close - p.stop_atr * atr,
            atr=atr,
            entry_ready=bool(trend_ok and peak_is_high and pullback_ok and resumed),
            close=last_close,
            bars=int(len(close)),
        )

    # ------------------------------------------------------------------ 횡단면
    def select(self, bundle: Bundle, p: Params, ctx: RunContext) -> Bundle:
        """게이트 넷을 모두 통과한 종목만 남긴다.

        **탈락 사유를 게이트별로 남긴다.** 어느 게이트에서 말랐는지 모르면 사람이
        기준을 의심하게 되고, 그 다음 수순은 기준을 낮추는 것이다.
        """
        gates = {
            "이동평균 정배열": lambda it: it.features.get("trend_ok") is True,
            f"고점이 {p.swing_high}봉 최고가": lambda it: it.features.get("peak_is_high") is True,
            f"조정 {p.min_pullback}~{p.max_pullback}봉": (
                lambda it: it.features.get("pullback_ok") is True
            ),
            "재출발(종가 > 피벗)": lambda it: it.features.get("resumed") is True,
        }
        if len(bundle):
            passed = ", ".join(
                f"{name} {sum(1 for it in bundle if fn(it))}건" for name, fn in gates.items()
            )
            ctx.log.info(f"게이트 통과 현황 ({len(bundle)}종목 중) — {passed}")

        ready = bundle.filter(lambda it: it.features.get("entry_ready") is True)
        if not len(ready):
            ctx.log.info(
                "오늘 재출발한 종목이 없습니다. 조정 중인 종목을 미리 사는 전략이 아니라 "
                "다시 오르기 시작한 것을 확인하고 사는 전략입니다."
            )
            return ready

        # 컷은 시장별로 걸린다 (규칙 17). 절삭 경고는 헬퍼가 남긴다.
        return top_n(ready, p.top_n, ctx)


# --------------------------------------------------------------------------- 내부
def _needed(p: LandryPullbackStrategy.Params) -> int:
    """계산이 성립하는 최소 봉 수. 값의 신뢰도는 `startup_candles`가 따로 본다."""
    return max(p.trend_slow, p.wilder * 2, p.swing_high + p.max_pullback + 2)


def _pullback_bars(high: pd.Series, limit: int) -> int:
    """**직전 봉부터** 뒤로 세어, 연속으로 저고점(lower high)인 봉이 몇 개인가.

    ★ 오늘 봉(iloc[-1])은 세지 않는다. 오늘은 조정이 아니라 **재출발을 판정하는
    봉**이다. 여기서 한 칸을 잘못 잡으면 피벗도 한 봉 밀리는데, 그래도 신호는
    그럴듯하게 계속 나온다 — 이 파일에서 가장 조용히 틀리기 쉬운 곳이다.

    한도를 1 넘어서면 세는 것을 멈춘다. `limit + 1`을 돌려주므로 호출부의 상한
    비교에서 그대로 탈락한다 — "조정이 너무 길다"와 "조정이 딱 한도다"가 구분된다.
    """
    count = 0
    while count <= limit and 3 + count <= len(high):
        if not high.iloc[-2 - count] < high.iloc[-3 - count]:
            break
        count += 1
    return count


def _true_range(frame: pd.DataFrame) -> pd.Series:
    prev_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    """Wilder(1978)의 ATR. `ewm(alpha=1/period)`가 원문의 평활이다."""
    return _true_range(frame).ewm(alpha=1 / period, adjust=False).mean()


def _adx(frame: pd.DataFrame, period: int) -> pd.Series:
    """Wilder(1978)의 ADX. 방향은 보지 않고 **추세의 강도만** 잰다.

    0으로 나누는 자리를 NaN으로 둔다 — 값이 아예 안 움직인 종목의 ADX는 정의되지
    않는다. 0으로 채우면 "추세가 약하다"가 되어 랭킹 맨 아래에 조용히 줄을 서는데,
    실제로는 **잴 수 없는 것**이다. NaN이면 `rank_by`가 경고와 함께 제외한다.
    """
    alpha = 1 / period
    up = frame["high"].diff()
    down = -frame["low"].diff()

    # 같은 봉에서 양방향이 다 커질 수는 없다 — 큰 쪽만 남기는 것이 Wilder의 정의다.
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    atr = _atr(frame, period).replace(0.0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr

    total = (plus_di + minus_di).replace(0.0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / total
    return dx.ewm(alpha=alpha, adjust=False).mean()
