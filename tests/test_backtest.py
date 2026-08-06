"""백테스트 리플레이 (ARCHITECTURE.md 12.7).

여기서 지키는 것은 넷이다.

  1. ★ **전략은 그날 이후를 볼 수 없다** — 넘어가는 창의 마지막 봉이 언제나 as_of다
  2. **판정일이 `--start`~`--end` 안이다** — 그 밖의 봉은 워밍업 재료일 뿐이다
  3. **워밍업이 모자란 날은 판정하지 않는다** — 조용히 통과시키면 지표가 거짓말한다
  4. ★ **마커는 "조건 충족일"이고 리포트가 그렇게 말한다** — 이 문장이 사라지면
     단일 종목 백테스트는 없는 성과를 본 것처럼 읽힌다
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pydantic import BaseModel

from app.backtest import replay
from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.market.calendar import build_offline_calendars, session_date
from app.market.instrument import InstrumentRef
from app.report.backtest_report import report_path, write_backtest_report
from app.strategies.base import Strategy

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 3, 20, tzinfo=UTC)
INSTRUMENT = InstrumentRef.parse("krx:005930")


def frame(days: int = 40, start: datetime | None = None) -> pd.DataFrame:
    """KRX 마감 시각(06:30 UTC = 15:30 KST)에 놓인 일봉."""
    first = start or datetime(2026, 1, 5, 6, 30, tzinfo=UTC)
    index = pd.DatetimeIndex([first + timedelta(days=i) for i in range(days)], name="time")
    closes = [100.0 + i for i in range(days)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000.0] * days,
        },
        index=index,
    )


class WindowRecorder(Strategy):
    """넘어온 창의 경계를 기록만 하는 전략. 인과성 검증용."""

    id = "window_recorder"
    timeframe = "1d"
    startup_candles = 5
    score_feature = "score"

    class Params(BaseModel):
        threshold: float = 0.0

    seen: list[tuple[datetime, datetime, int]] = []  # (as_of, 창의 마지막, 봉 수)

    def compute(self, item: Item, params: Params, ctx: RunContext) -> Item:
        WindowRecorder.seen.append(
            (item.as_of, item.ohlcv.index[-1].to_pydatetime(), len(item.ohlcv))
        )
        # ctx.now도 그날로 고정돼야 한다 (규칙 1).
        assert ctx.now == item.as_of
        return item.with_features(score=float(item.ohlcv["close"].iloc[-1]))

    def select(self, bundle: Bundle, params: Params, ctx: RunContext) -> Bundle:
        return bundle.filter(lambda it: it.features["score"] >= params.threshold)


@pytest.fixture(autouse=True)
def _clear_recorder():
    WindowRecorder.seen = []


def run_replay(*, start: date, end: date, threshold: float = 0.0, days: int = 40):
    ctx = RunContext.create(now=NOW)
    return replay(
        frame=frame(days),
        instrument=INSTRUMENT,
        timeframe="1d",
        strategy=WindowRecorder(),
        params=WindowRecorder.Params(threshold=threshold),
        ctx=ctx,
        calendar=build_offline_calendars()["krx"],
        start=start,
        end=end,
    )


# ------------------------------------------------------------------------- 인과성
def test_strategy_never_sees_a_bar_after_the_judged_day():
    """★ 이것이 무너지면 백테스트 전체가 거짓말이 된다 (규칙 3)."""
    run_replay(start=date(2026, 1, 20), end=date(2026, 2, 5))

    assert WindowRecorder.seen
    for as_of, window_last, _bars in WindowRecorder.seen:
        assert window_last == as_of


def test_window_grows_by_one_bar_per_day():
    """창이 하루에 한 봉씩 늘어난다 — 같은 창을 반복해서 넣고 있지 않다."""
    run_replay(start=date(2026, 1, 20), end=date(2026, 2, 5))
    sizes = [bars for _, _, bars in WindowRecorder.seen]

    assert sizes == list(range(sizes[0], sizes[0] + len(sizes)))


def test_only_sessions_inside_the_range_are_judged():
    start, end = date(2026, 1, 20), date(2026, 2, 5)
    result = run_replay(start=start, end=end)

    assert result.days
    assert all(start <= day.session <= end for day in result.days)


def test_warmup_short_days_are_skipped_not_judged():
    """봉이 startup_candles에 못 미치는 초반은 판정하지 않는다."""
    result = run_replay(start=date(2026, 1, 5), end=date(2026, 1, 31))

    # 첫 4일은 5봉에 못 미친다 (startup_candles=5).
    assert result.skipped_warmup == 4
    assert result.days[0].session == session_date(
        datetime(2026, 1, 9, 6, 30, tzinfo=UTC), build_offline_calendars()["krx"]
    )


# --------------------------------------------------------------------------- 신호
def test_signal_days_follow_the_strategy_cut():
    """종가 120 이상인 날만 통과 — 리플레이가 select를 실제로 태운다."""
    result = run_replay(start=date(2026, 1, 5), end=date(2026, 2, 20), threshold=120.0)

    assert result.signal_days
    assert all(day.close >= 120 for day in result.signal_days)
    assert all(day.close < 120 for day in result.days if not day.signal)


def test_zero_signals_is_not_a_failure():
    result = run_replay(start=date(2026, 1, 20), end=date(2026, 2, 5), threshold=10_000.0)

    assert result.signal_days == []
    assert result.days  # 판정은 했다 — "0건"과 "안 돌았다"는 다르다


def test_rank_features_are_dropped_from_a_single_symbol_replay():
    """★ "순위 1 / 1 · 상위 100%"는 정보가 아니라 오해다."""
    result = run_replay(start=date(2026, 1, 20), end=date(2026, 2, 5))

    for day in result.days:
        assert "rank" not in day.features
        assert "percentile" not in day.features
        assert "universe_size" not in day.features


def test_cut_applied_is_false_for_single_symbol():
    """리포트 배너가 이 값에 걸려 있다. True로 바뀌면 경고가 사라진다."""
    assert run_replay(start=date(2026, 1, 20), end=date(2026, 2, 5)).cut_applied is False


# ----------------------------------------------------------------------- 세션 날짜
def test_chart_bars_use_the_session_date_not_the_close_timestamp():
    """마감 시각을 그대로 쓰면 미국장이 하루 밀린다 — 봉과 마커가 어긋난다."""
    calendars = build_offline_calendars()
    # 미국 1/5 세션의 마감은 UTC 1/5 21:00이다. KST로 옮기면 1/6 새벽이라,
    # 한국 시간대로 날짜를 떼면 하루 밀린다.
    us_close = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)

    assert session_date(us_close, calendars["us_equity"]) == date(2026, 1, 5)


def test_markers_and_bars_share_the_same_session_dates():
    result = run_replay(start=date(2026, 1, 5), end=date(2026, 2, 20), threshold=120.0)
    bar_times = {bar["time"] for bar in result.bars}

    for day in result.signal_days:
        assert day.session.isoformat() in bar_times


# --------------------------------------------------------------------------- 리포트
def test_report_is_self_contained_and_keeps_the_warning(tmp_path: Path):
    """★ 배너가 사라지면 이 리포트는 없는 성과를 본 것처럼 읽힌다."""
    result = run_replay(start=date(2026, 1, 5), end=date(2026, 2, 20), threshold=120.0)
    path = write_backtest_report(result, tmp_path / "bt.html")
    text = path.read_text(encoding="utf-8")

    assert "조건 충족" in text and "실제 신호일" in text
    # 차트 라이브러리가 인라인이다 — CDN을 걸면 반년 뒤 화면이 빈다 (§2.1).
    assert "src=" not in text
    assert "LightweightCharts" in text
    assert "createChart" in text
    # 마커가 실제로 실렸는가.
    for day in result.signal_days:
        assert day.session.isoformat() in text


def test_report_marks_where_the_backtest_started(tmp_path: Path):
    """★ 차트 왼쪽 끝부터 백테스트가 돈 것처럼 보이면 안 된다.

    워밍업 구간은 지표 계산용일 뿐이라 마커가 나올 수 없는데, 구분이 없으면
    "그 기간엔 신호가 없었다"로 읽힌다.
    """
    result = run_replay(start=date(2026, 1, 20), end=date(2026, 2, 20), threshold=120.0)
    text = write_backtest_report(result, tmp_path / "bt.html").read_text(encoding="utf-8")

    assert "백테스트 시작 2026-01-20" in text
    assert "워밍업" in text
    # 시작 이전 봉은 회색으로 낮춘다.
    assert "#9aa0a6" in text


def test_report_path_names_the_instrument_and_period():
    result = run_replay(start=date(2026, 1, 5), end=date(2026, 2, 20))
    path = report_path(result, Path("/tmp/reports"))

    assert path.name == "backtest_krx_005930_20260105_20260220.html"
