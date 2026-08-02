"""전략 프로토콜·로더·정적 검사 테스트.

네트워크 없이 돈다. 로더 테스트는 tmp_path에 전략 파일을 써서 **소스 해시가
실제로 바뀌는지**까지 확인한다 — 그게 4.7이 막으려는 구멍이기 때문이다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.engine.context import RunContext
from app.engine.types import Bundle, Item
from app.market.instrument import InstrumentRef
from app.strategies.base import Strategy, rank_by, top_n, top_pct
from app.strategies.check import check_source
from app.strategies.registry import (
    StrategyError,
    StrategyNotFoundError,
    discover,
    load_strategy,
)

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

SIMPLE_STRATEGY = '''
from pydantic import BaseModel, Field

from app.engine.types import Item
from app.strategies import Strategy


class MyStrategy(Strategy):
    id = "{sid}"
    display_name = "테스트 전략"
    timeframe = "1d"
    startup_candles = 3
    score_feature = "score"

    class Params(BaseModel):
        weight: float = Field(default={weight})

    def compute(self, item, p, ctx):
        close = item.ohlcv["close"]
        return item.with_features(score=float(close.iloc[-1]) * p.weight)
'''


def write_strategy(directory: Path, sid: str, weight: float = 1.0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sid}.py"
    path.write_text(SIMPLE_STRATEGY.format(sid=sid, weight=weight), encoding="utf-8")
    return path


def make_item(symbol: str, close: float, bars: int = 10) -> Item:
    index = pd.date_range(end=NOW, periods=bars, freq="D", tz="UTC", name="time")
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        },
        index=index,
    )
    return Item(
        instrument=InstrumentRef.parse(symbol),
        timeframe="1d",
        as_of=NOW,
        ohlcv=frame,
    )


@pytest.fixture
def ctx() -> RunContext:
    return RunContext.create(now=NOW)


# --------------------------------------------------------------------------- 로더
def test_discover_lists_files_without_importing(tmp_path: Path):
    write_strategy(tmp_path, "alpha")
    write_strategy(tmp_path, "beta")
    (tmp_path / "_private.py").write_text("raise RuntimeError('임포트되면 안 된다')", "utf-8")

    found = discover(tmp_path)
    assert [s.id for s in found] == ["alpha", "beta"]
    assert all(len(s.sha256) == 64 for s in found)


def test_source_hash_changes_when_file_changes(tmp_path: Path):
    """파일을 고치면 해시가 달라져야 한다 — 4.7이 막으려는 소급 변경의 감지 지점."""
    write_strategy(tmp_path, "drift", weight=1.0)
    before = load_strategy("drift", tmp_path).sha256

    write_strategy(tmp_path, "drift", weight=2.0)
    after = load_strategy("drift", tmp_path).sha256

    assert before != after


def test_load_missing_strategy_lists_alternatives(tmp_path: Path):
    write_strategy(tmp_path, "alpha")
    with pytest.raises(StrategyNotFoundError, match="alpha"):
        load_strategy("nope", tmp_path)


def test_class_id_must_match_filename(tmp_path: Path):
    """id와 파일 이름이 어긋나면 실행 이력에서 어느 파일이었는지 되짚을 수 없다."""
    path = write_strategy(tmp_path, "alpha")
    path.write_text(SIMPLE_STRATEGY.format(sid="다른이름", weight=1.0), encoding="utf-8")
    with pytest.raises(StrategyError, match="파일 이름"):
        load_strategy("alpha", tmp_path)


def test_two_strategies_in_one_file_are_rejected(tmp_path: Path):
    """소스 해시가 파일 단위이므로 파일 하나에 전략 하나여야 한다."""
    path = write_strategy(tmp_path, "twins")
    path.write_text(
        SIMPLE_STRATEGY.format(sid="twins", weight=1.0)
        + "\n\nclass Second(Strategy):\n"
        "    id = 'twins'\n"
        "    def compute(self, item, p, ctx):\n        return item\n",
        encoding="utf-8",
    )
    with pytest.raises(StrategyError, match="여러 개"):
        load_strategy("twins", tmp_path)


# ------------------------------------------------------------------------- 랭킹
def test_rank_writes_rank_and_percentile(ctx: RunContext):
    bundle = Bundle(
        [
            make_item("upbit:KRW-BTC", 100).with_features(score=0.3),
            make_item("upbit:KRW-ETH", 100).with_features(score=0.9),
            make_item("krx:005930", 100).with_features(score=0.1),
        ]
    )
    ranked = rank_by(bundle, "score", ctx)

    assert [i.instrument.symbol for i in ranked] == ["KRW-ETH", "KRW-BTC", "005930"]
    assert [i.features["rank"] for i in ranked] == [1, 2, 3]
    assert all(i.features["universe_size"] == 3 for i in ranked)


def test_rank_ascending_for_low_is_good_factors(ctx: RunContext):
    """저변동성·저PBR 팩터는 작은 쪽이 좋다."""
    bundle = Bundle(
        [
            make_item("upbit:KRW-BTC", 100).with_features(score=0.3),
            make_item("upbit:KRW-ETH", 100).with_features(score=0.1),
        ]
    )
    ranked = rank_by(bundle, "score", ctx, descending=False)
    assert ranked.items[0].instrument.symbol == "KRW-ETH"


def test_rank_warns_when_scores_are_missing(ctx: RunContext):
    """조용히 빠지면 '유니버스 전체를 훑었다'는 전제가 깨진 걸 아무도 모른다."""
    bundle = Bundle(
        [
            make_item("upbit:KRW-BTC", 100).with_features(score=0.3),
            make_item("upbit:KRW-ETH", 100),  # score 없음
        ]
    )
    ranked = rank_by(bundle, "score", ctx)

    assert len(ranked) == 1
    assert any("랭킹에서 제외" in r.message for r in ctx.log.records)


def test_rank_without_score_feature_still_records_universe_size(ctx: RunContext):
    """단일 종목 전략도 표본 수는 남긴다."""
    ranked = rank_by(Bundle([make_item("upbit:KRW-BTC", 100)]), None, ctx)
    assert ranked.items[0].features["universe_size"] == 1


def test_top_n_and_top_pct_log_the_cut(ctx: RunContext):
    bundle = Bundle([make_item(f"upbit:KRW-{i}0", 100) for i in range(1, 5)])
    assert len(top_n(bundle, 2, ctx)) == 2
    assert len(top_pct(bundle, 0.5, ctx)) == 2
    assert len(top_pct(bundle, 0.01, ctx)) == 1  # 최소 1개는 남는다
    assert any("컷" in r.message for r in ctx.log.records)


def test_default_hooks_let_single_symbol_strategies_skip_rank(ctx: RunContext):
    """rank/select에 기본 구현이 있어야 compute만 채우면 된다 (4.2 규칙 4)."""

    class Minimal(Strategy):
        id = "minimal"

        def compute(self, item, p, ctx):
            return item.with_features(x=1)

    strategy = Minimal()
    bundle = Bundle([strategy.compute(make_item("upbit:KRW-BTC", 100), None, ctx)])
    params = strategy.Params()
    assert len(strategy.select(strategy.rank(bundle, params, ctx), params, ctx)) == 1


# --------------------------------------------------------------------- 정적 검사
@pytest.mark.parametrize(
    ("code", "rule"),
    [
        ("df['c'].shift(-5)", "causality"),
        ("df['c'].shift(periods=-1)", "causality"),
        ("df['c'].rolling(5, center=True).mean()", "causality"),
        ("df['c'].bfill()", "causality"),
        ("df['c'].fillna(method='bfill')", "causality"),
        ("datetime.now()", "injected_clock"),
        ("pd.Timestamp.now()", "injected_clock"),
        ("import httpx", "no_network"),
        ("from pykrx import stock", "no_network"),
    ],
)
def test_check_catches_future_reference(code: str, rule: str):
    result = check_source(code, "x", Path("x.py"))
    assert not result.ok
    assert any(v.rule == rule for v in result.violations)


@pytest.mark.parametrize(
    "code",
    [
        "df['c'].shift(1)",
        "df['c'].rolling(20).mean()",
        "df['c'].ewm(span=12).mean()",
        "df['c'].ffill()",
        "ctx.now",
    ],
)
def test_check_allows_causal_operations(code: str):
    assert check_source(code, "x", Path("x.py")).ok


def test_check_flags_missing_declarations():
    result = check_source(
        "class Broken(Strategy):\n    display_name = 'x'\n", "broken", Path("broken.py")
    )
    assert not result.ok
    details = " ".join(v.detail for v in result.violations)
    assert "id가 없습니다" in details
    assert "compute가 없습니다" in details


def test_missing_params_is_a_warning_not_an_error():
    """파라미터가 없는 단일 종목 전략은 정상이다. 알리되 막지는 않는다."""
    result = check_source(
        "class Ok(Strategy):\n"
        "    id = 'ok'\n"
        "    def compute(self, item, p, ctx):\n        return item\n",
        "ok",
        Path("ok.py"),
    )
    assert result.ok
    assert any(v.rule == "declaration" and v.level == "warning" for v in result.violations)


def test_check_reports_syntax_error_without_crashing():
    result = check_source("def broken(:\n", "x", Path("x.py"))
    assert not result.ok
    assert result.violations[0].rule == "syntax"


def test_shipped_demo_strategy_passes_its_own_check():
    """저장소에 든 전략이 규칙을 어기고 있으면 규칙이 문서로만 존재하는 것이다."""
    from app.strategies.check import check_file

    for source in discover():
        assert check_file(source.path, source.id).ok, source.id
