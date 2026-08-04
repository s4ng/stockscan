"""추세추종 — 55일 채널 돌파 (Donchian breakout).

12-1이 **상대적으로 강한 종목**을 줄 세운다면, 이 전략은 **절대적으로 추세인
종목이 새 고점을 찍는 순간**을 잡습니다. 축이 다릅니다 — 12-1은 아무리 시장이
무너져도 "덜 빠진 상위 20%"를 뽑아내지만, 추세추종은 조건을 만족하는 종목이
하나도 없으면 **아무것도 내지 않습니다.** 그것이 추세추종의 본질입니다.

★ **신호 0건인 날이 대부분입니다. 그게 정상입니다.**
돌파는 상태가 아니라 **사건**이라 유니버스 300종목 중 하루에 0~5개만 걸립니다.
0건이 이어진다고 파라미터를 낮추면 그 순간부터 추세추종이 아니라 눌림목 매매가
됩니다 — 그리고 그건 이 파일이 아니라 다른 전략 파일에 써야 합니다.

**게이트 셋을 모두 통과해야 후보입니다.**

    1. 추세 방향   close > SMA(200)              — Faber(2007) 10개월 이동평균
    2. 절대 모멘텀 12개월 수익률 > 0              — Moskowitz·Ooi·Pedersen(2012)
    3. 돌파        close ≥ 직전 55봉 최고가       — Donchian / Turtle System 2

    │←──── 직전 55봉의 최고가(오늘 제외) ────→│
                                              ↑ 오늘 종가가 이 선을 넘으면 돌파
    ※ 오늘 봉을 창에서 빼지 않으면(shift(1) 누락) 오늘 고가가 자기 자신과
      비교돼 돌파 판정이 통째로 무의미해집니다. 이 전략에서 **가장 조용히
      틀리기 쉬운 한 줄**이고, 틀려도 결과가 그럴듯해 보입니다.

**파라미터를 백테스트로 고르지 않았습니다** (ARCHITECTURE.md 4.8). 200일선은
Faber(2007), 12개월 절대 모멘텀은 MOP(2012), 55/20 채널과 ATR(20)·2N 손절은
Dennis-Eckhardt의 Turtle 규칙 원문 값입니다. 전부 **남이 공개한 값**이므로
저에게는 out-of-sample입니다. 제가 골랐다면 일봉 2,500행에 파라미터 6개를 맞춘
것이 되어, 우연히 맞은 조합인지 구분할 방법이 없습니다.

**랭킹은 변동성으로 정규화한 추세 강도**(12개월 수익률 ÷ 연율화 변동성)로 합니다.
돌파 폭이 큰 순서로 줄 세우면 **가장 많이 뻗은 종목**이 위로 오는데, 그건 추세가
강한 게 아니라 되돌림이 임박한 것일 수 있습니다. 관리형 선물이 포지션을 변동성으로
나누는 것과 같은 논리입니다(MOP 2012).

**이 전략은 진입만 봅니다.** 청산은 사람이 합니다 — 다만 판단에 필요한 숫자는
features에 남깁니다: `stop_2n`(최초 손절, 종가 − 2×ATR)과 `exit_low_20`
(20일 저가 = Turtle System 2의 청산선). 이 둘이 없으면 "얼마나 틀릴 수 있는가"를
모른 채 후보만 보게 됩니다.
"""

from __future__ import annotations

import math

import pandas as pd
from pydantic import BaseModel, Field

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.strategies import Strategy, top_n

#: 거래일 기준 근사치. 코인은 365일 거래되지만 **표준 정의를 시장에 맞춰 바꾸지
#: 않는다** — 정의를 흔들기 시작하면 그 순간부터 파라미터 탐색이 된다 (4.8).
TREND_MA = 200  # Faber(2007) 10개월 ≈ 200거래일
MOMENTUM = 252  # MOP(2012) 12개월 절대 모멘텀
BREAKOUT = 55  # Turtle System 2 진입 채널
EXIT_CHANNEL = 20  # Turtle System 2 청산 채널
ATR_PERIOD = 20  # Turtle의 N
STOP_ATR = 2.0  # Turtle의 2N 손절

#: 변동성 연율화 계수. 랭킹 점수의 단위를 맞추는 상수일 뿐이라 시장별로 나누지
#: 않는다 — 어차피 랭킹은 시장 안에서만 매겨진다 (규칙 17).
TRADING_DAYS = 252


class TrendBreakout55Strategy(Strategy):
    id = "trend_breakout_55"
    display_name = "추세추종 55일 채널 돌파"
    timeframe = "1d"

    #: 가장 긴 창(12개월 모멘텀)이 기준이다. +1은 기준점 봉 자신.
    #: 이보다 짧은 종목은 Strategy Runner가 제외한다 — 신규 상장 종목이 짧은
    #: 이력으로 "사상 최고가 돌파"를 만들어 내는 것을 막는다.
    startup_candles = max(MOMENTUM, TREND_MA, BREAKOUT) + 1

    score_feature = "trend_strength"
    score_descending = True

    class Params(BaseModel):
        trend_ma: int = Field(
            default=TREND_MA, ge=2, le=1000, description="추세 방향 판정 이동평균(봉). 표준 200"
        )
        momentum: int = Field(
            default=MOMENTUM, ge=2, le=1000, description="절대 모멘텀 측정 기간(봉). 표준 252"
        )
        breakout: int = Field(
            default=BREAKOUT, ge=2, le=500, description="돌파 채널 기간(봉). 표준 55"
        )
        exit_channel: int = Field(
            default=EXIT_CHANNEL, ge=2, le=500, description="청산 채널 기간(봉). 표준 20"
        )
        atr: int = Field(default=ATR_PERIOD, ge=2, le=200, description="ATR 기간(봉). 표준 20")
        stop_atr: float = Field(
            default=STOP_ATR, gt=0, le=10, description="손절 거리(ATR 배수). 표준 2N"
        )
        top_n: int = Field(
            default=10,
            ge=1,
            le=200,
            description="시장당 최대 후보 수. 전략 파라미터가 아니라 화면 컷입니다",
        )

    # ------------------------------------------------------------------ 시계열
    def compute(self, item: Item, p: Params, ctx: RunContext) -> Item:
        frame = item.ohlcv
        close = frame["close"]
        needed = max(p.momentum, p.trend_ma, p.breakout) + 1
        if len(close) < needed:
            # require_startup_candles=False로 껐을 때만 여기 온다.
            # 점수를 안 채우면 rank_by가 경고와 함께 제외한다.
            return item.with_features(insufficient_bars=len(close))

        # iloc[-1]이 판단 시점의 **마감된** 봉이다. 아래는 전부 음수 인덱스와
        # rolling·shift(양수)뿐이라 과거만 본다 (규칙 3).
        last_close = float(close.iloc[-1])

        sma = float(close.rolling(p.trend_ma).mean().iloc[-1])
        trend_ok = last_close > sma

        past = float(close.iloc[-1 - p.momentum])
        tsmom = last_close / past - 1 if past > 0 else float("nan")

        # 변동성으로 나눠야 "많이 오른 종목"과 "꾸준히 오른 종목"이 갈린다.
        daily_vol = float(close.pct_change().rolling(p.momentum).std().iloc[-1])
        annual_vol = daily_vol * math.sqrt(TRADING_DAYS)
        trend_strength = tsmom / annual_vol if annual_vol > 0 else float("nan")

        # ★ shift(1) — 오늘 봉을 창에서 뺀다. 빼지 않으면 오늘 고가가 창에 포함돼
        #   "직전 최고가"가 오늘 자신이 되고, 돌파 판정이 조용히 무너진다.
        channel_high = float(frame["high"].shift(1).rolling(p.breakout).max().iloc[-1])
        exit_low = float(frame["low"].shift(1).rolling(p.exit_channel).min().iloc[-1])

        # 종가 기준 돌파다. Turtle 원문은 장중 고가로 판정하지만 이 시스템은
        # 마감된 봉으로만 판단하므로(4.4) 장중 돌파를 알 수 없고, 알 수 있다 해도
        # 되돌려 마감한 돌파를 신호로 내면 안 된다.
        breakout = last_close >= channel_high

        atr = float(_atr(frame, p.atr).iloc[-1])
        stop = last_close - p.stop_atr * atr

        return item.with_features(
            trend_strength=trend_strength,
            tsmom_12m=tsmom,
            annual_vol=annual_vol,
            trend_ok=trend_ok,
            above_ma_pct=last_close / sma - 1 if sma > 0 else float("nan"),
            breakout=breakout,
            channel_high=channel_high,
            # 돌파 폭. 신호의 근거가 아니라 **해석용**이다 — 이 값이 크면 갭으로
            # 뛰어넘은 것이라 2N 손절까지의 거리가 그만큼 멀어진다.
            breakout_margin_pct=last_close / channel_high - 1 if channel_high > 0 else float("nan"),
            exit_low_20=exit_low,
            atr=atr,
            atr_pct=atr / last_close if last_close > 0 else float("nan"),
            stop_2n=stop,
            # 손절까지의 거리. 포지션 크기를 정하는 값이라 후보 목록에 반드시 보여야 한다.
            risk_pct=(last_close - stop) / last_close if last_close > 0 else float("nan"),
            entry_ready=bool(trend_ok and breakout and tsmom > 0),
            close=last_close,
            bars=int(len(close)),
        )

    # ------------------------------------------------------------------ 횡단면
    def select(self, bundle: Bundle, p: Params, ctx: RunContext) -> Bundle:
        """게이트 셋을 모두 통과한 종목만 남긴다.

        **탈락 사유를 게이트별로 남긴다.** 0건은 정상이지만 "왜 0건인가"를 모르면
        사람이 파라미터를 의심하게 되고, 그 다음 수순은 기준을 낮추는 것이다.
        """
        gates = {
            "200일선 위": lambda it: it.features.get("trend_ok") is True,
            "12개월 모멘텀 > 0": lambda it: (it.features.get("tsmom_12m") or 0) > 0,
            f"{p.breakout}일 신고가 돌파": lambda it: it.features.get("breakout") is True,
        }
        if len(bundle):
            passed = ", ".join(
                f"{name} {sum(1 for it in bundle if fn(it))}건" for name, fn in gates.items()
            )
            ctx.log.info(f"게이트 통과 현황 ({len(bundle)}종목 중) — {passed}")

        ready = bundle.filter(lambda it: it.features.get("entry_ready") is True)
        if not len(ready):
            ctx.log.info(
                "오늘 돌파 조건을 만족한 종목이 없습니다. "
                "추세추종에서 0건은 실패가 아니라 '들어갈 자리가 없다'는 판정입니다."
            )
            return ready

        # 컷은 시장별로 걸린다 (규칙 17). 절삭 경고는 헬퍼가 남긴다.
        return top_n(ready, p.top_n, ctx)


# --------------------------------------------------------------------------- 내부
def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    """Wilder(1978)의 ATR. 전봉 종가를 쓰므로 shift(1)이 필수다.

    `ewm(alpha=1/period, adjust=False)`가 Wilder의 원래 평활이다. 단순
    `rolling(period).mean()`을 쓰면 값이 미묘하게 달라지고, 그 차이가 그대로
    손절 가격으로 나간다.
    """
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()
