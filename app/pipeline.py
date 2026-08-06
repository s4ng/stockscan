"""파이프라인 — 이 프로그램이 하는 일 전부 (ARCHITECTURE.md 4.3).

**2026-08-06에 DAG 엔진을 걷어냈다.** 노드·그래프·레지스트리·위상정렬이 있었지만
**만들어지는 그래프가 언제나 하나**였다. 조합이 하나면 조합을 표현하는 장치가 값을
못 하고, 대신 "어느 노드에서 끊겼나"를 사람이 매번 되짚어야 했다.

    유니버스 → 봉 수집 → 전략 → 기록 → 로그

위에서 아래로 읽으면 그것이 프로그램의 전부다.

**엔진이 하던 일 중 지켜야 할 것은 전부 남겼다.**

| 엔진이 하던 일 | 지금 |
| :--- | :--- |
| `node_runs` 스냅샷 (4.9) | `StageRecord` — 테이블도 형식도 그대로다. `explain`이 계속 읽는다 |
| Fresh Bar Gate 커밋 판정 (규칙 11) | `execute()` 끝의 `commit()`/`discard()` — 조건 둘도 그대로 |
| 소스 실패 재시도 | `_with_retry` — 유니버스 조회에만 건다 (아래 참조) |
| 외부 전송 차단 (규칙 13) | ★ **없앴다. 더 강해졌다** — 아래 참조 |
| 분봉 차단 (규칙 12) | 타임프레임이 `1d` 상수다. 설정할 수 없으므로 어길 수 없다 |

★ **규칙 13(단일 실행은 외부로 아무것도 내보내지 않는다)이 런타임 검사에서 구조로
바뀌었다.** 예전에는 노드가 `sends_external_messages = True`를 선언하고 엔진이 그걸
보고 건너뛰었다 — 선언을 빠뜨리면 새는 구조라 "노드마다 심으면 언젠가 빠뜨린다"는
경고가 필요했다. **지금 이 모듈에는 바깥으로 나가는 코드 경로가 아예 없다.**
알림은 `app/serve.py`가 실행 **뒤에** 보낸다. 빠뜨릴 자리가 없어졌다.

⚠️ **그래서 이 파일에 전송 코드를 넣지 않는다.** 넣는 순간 손으로 돌린 `run`이
채널로 나가고, 그러면 알림을 믿지 않게 된다 (12.2).
"""

from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.config import TIMEFRAME, AppConfig, lookback_for, uses_turnover
from app.engine.context import RunContext
from app.engine.signals import draft_from_item
from app.engine.state import bar_key
from app.engine.types import Bundle, Item, validate_ohlcv
from app.market.instrument import InstrumentRef
from app.providers.base import UniverseNotSupportedError
from app.providers.ohlcv_source import CacheMissError
from app.providers.registry import AUTO, NoProviderError
from app.strategies.base import RANK_FEATURE, StrategyError
from app.strategies.registry import LoadedStrategy, load_strategy
from app.strategies.stages import eligible_items, run_stages

#: `node_runs`에 남길 랭킹 스냅샷 크기. 전부 남기면 유니버스 500종목에서 로그가 터진다.
RANK_SNAPSHOT_SIZE = 20

#: 유니버스 조회 재시도. 여기만 거는 이유는 **실패의 무게가 다르기** 때문이다 —
#: 종목 하나의 봉 조회는 실패해도 그 종목만 빠지지만(그리고 Provider 라우팅이 이미
#: 폴백한다), 유니버스가 실패하면 **그날 훑을 대상이 통째로 없어진다.**
UNIVERSE_ATTEMPTS = 3
UNIVERSE_BACKOFF = 1.0


class StageStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    """일부 단계가 실패했지만 끝까지 진행됐다."""
    FAILED = "failed"


class PipelineError(RuntimeError):
    """실행을 이어 갈 수 없을 때. 종료 코드 2(데이터 소스 실패)로 이어진다."""


@dataclass
class StageRecord:
    """단계 1회 실행 기록 — '왜 이 신호가 나왔는가'를 사후에 재현하는 근거 (4.9).

    ⚠️ **`node_runs` 테이블에 그대로 들어간다.** 엔진을 걷어내면서도 형식을 유지한
    이유는 `explain`이 이 스냅샷을 읽기 때문이다. 스키마를 바꾸면 과거 실행을
    되짚을 수 없게 되는데, 그건 이 프로젝트가 자신감 기계가 되지 않게 막는 장치다.
    """

    node_id: str
    type: str
    status: StageStatus = StageStatus.SUCCESS
    duration_ms: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "type": self.type,
            "status": str(self.status),
            "duration_ms": round(self.duration_ms, 2),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "logs": self.logs,
            "error": self.error,
            "attempts": self.attempts,
        }


@dataclass
class RunResult:
    run_id: str
    pipeline_id: str
    mode: str
    now: str
    status: RunStatus = RunStatus.SUCCESS
    nodes: list[StageRecord] = field(default_factory=list)
    error: str | None = None

    def node(self, node_id: str) -> StageRecord | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "mode": self.mode,
            "now": self.now,
            "status": str(self.status),
            "error": self.error,
            "nodes": [n.to_dict() for n in self.nodes],
        }


class _Recorder:
    """단계를 재고 기록한다. `with`로 감싸는 것 하나가 관측성의 전부다."""

    def __init__(self, result: RunResult, ctx: RunContext) -> None:
        self._result = result
        self._ctx = ctx

    def stage(self, node_id: str, kind: str) -> _Stage:
        record = StageRecord(node_id=node_id, type=kind)
        self._result.nodes.append(record)
        return _Stage(record, self._ctx.bind(node_id))


class _Stage:
    def __init__(self, record: StageRecord, ctx: RunContext) -> None:
        self.record = record
        self.ctx = ctx
        self._started = 0.0

    def __enter__(self) -> _Stage:
        self._started = _time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.record.duration_ms = (_time.perf_counter() - self._started) * 1000
        self.record.logs = [
            f"[{r.level}] {r.message}" for r in self.ctx.log.for_node(self.record.node_id)
        ]
        if exc is not None:
            self.record.status = StageStatus.ERROR
            self.record.error = f"{type(exc).__name__}: {exc}"
        return False


# =========================================================================== 실행
async def execute(config: AppConfig, ctx: RunContext) -> RunResult:
    """설정대로 한 번 돈다.

    ⚠️ **부작용은 `ctx`가 정한다** (규칙 11). `ctx.signals`가 dry-run이면 메모리에만
    담기고, 봉 소비는 아래 마지막 블록이 확정한다. 이 함수에 `commit` 분기를 심지
    않는다 — 분기를 여러 곳에 두면 언젠가 하나를 빠뜨리고, 그날 봉이 소리 없이 소비된다.
    """
    if not ctx.pipeline_id:
        ctx.pipeline_id = config.pipeline_id

    result = RunResult(
        run_id=ctx.run_id,
        pipeline_id=config.pipeline_id,
        mode=str(ctx.mode),
        now=ctx.now.isoformat(),
    )
    rec = _Recorder(result, ctx)

    try:
        strategy = _load_strategy(config.strategy, rec)
        universe, names = await _discover_universe(config, rec, ctx)
        bundle = await _fetch_bars(universe, names, strategy, rec, ctx)
        selected = _run_strategy(bundle, strategy, rec, ctx)
        await _persist(selected, rec, ctx)
        _log_candidates(selected, rec, ctx)
    except PipelineError as exc:
        result.status = RunStatus.FAILED
        result.error = str(exc)

    if result.status is not RunStatus.FAILED:
        failed = [r for r in result.nodes if r.status is StageStatus.ERROR]
        result.status = RunStatus.PARTIAL if failed else RunStatus.SUCCESS

    # Fresh Bar Gate는 두 조건을 모두 만족할 때만 봉 소비를 확정한다.
    #   1. 실행이 온전히 성공했을 것 — PARTIAL도 커밋하지 않는다. 실패한 단계가
    #      하필 기록이었다면 신호가 조용히 사라진다. 중복은 dedup_key가 막지만
    #      유실을 막을 장치는 없다.
    #   2. `--commit`이 붙었을 것 — dry-run이 봉을 삼키면 다음 실제 실행에서
    #      stale로 걸러져 그 신호가 영영 사라진다 (12.2 / 규칙 11).
    if result.status is RunStatus.SUCCESS and ctx.commit:
        ctx.bar_state.commit()
    else:
        ctx.bar_state.discard()
    return result


# ----------------------------------------------------------------------- 단계들
def _load_strategy(strategy_id: str, rec: _Recorder) -> LoadedStrategy:
    """전략을 읽는 것도 단계로 남긴다.

    실패해도 `node_runs`에 흔적이 있어야 **어디서 끊겼는지**를 되짚을 수 있다 —
    기록이 통째로 비면 "실행이 안 됐다"와 "전략을 못 찾았다"가 구분되지 않는다.
    """
    with rec.stage("load", "strategyLoad") as stage:
        try:
            loaded = load_strategy(strategy_id)
        except StrategyError as exc:
            raise PipelineError(str(exc)) from exc
        stage.ctx.log.info(f"전략 {loaded.strategy.id} @ {loaded.sha256[:12]}")
        stage.record.outputs = {"main": {"strategy": loaded.strategy.id}}
    return loaded


async def _discover_universe(
    config: AppConfig, rec: _Recorder, ctx: RunContext
) -> tuple[list[str], dict[str, str]]:
    """무엇을 훑을 것인가.

    ⚠️ ★ **동적 유니버스는 백테스트에서 거부된다** (규칙 14). 소스가 주는 목록은
    언제나 "지금"이라, 과거를 리플레이하면 **전략 코드가 완전히 인과적인 채로
    유니버스가 미래를 본다**(서바이버십). `strategy check`의 AST 검사에 걸리지 않는
    look-ahead라 차단을 여기에 명시적으로 둔다.
    """
    with rec.stage("universe", "symbolUniverse") as stage:
        if ctx.is_backtest:
            raise PipelineError(
                "동적 유니버스는 백테스트에서 쓸 수 없습니다 — 소스가 주는 목록은 항상 "
                "'지금'이라 과거를 리플레이하면 유니버스가 미래를 봅니다(서바이버십 편향). "
                "backtest 명령은 종목을 직접 받습니다."
            )

        merged: dict[str, InstrumentRef] = {}
        per_venue: list[dict[str, Any]] = []

        for venue, size in config.universe.items():
            refs, source_id = await _with_retry(
                lambda v=venue, n=size: list_venue(v, n, stage.ctx),
                stage=stage,
                ctx=stage.ctx,
                what=f"{venue} 종목 목록",
            )
            added = 0
            for ref in refs:
                if ref.key not in merged:
                    merged[ref.key] = ref
                    added += 1
            per_venue.append({"venue": venue, "size": size, "count": added, "source": source_id})
            stage.ctx.log.info(f"{venue}: {added}종목 (상위 {size}, 소스 {source_id})")

        if not merged:
            # 빈 유니버스는 실패가 아니라 정상 출력이다(4.1). 다만 조용하면 안 된다.
            stage.ctx.log.warning(
                "유니버스가 0종목입니다. universe 설정이 비었는지, 소스가 빈 목록을 "
                "줬는지 확인하세요."
            )

        keys = list(merged)
        stage.record.outputs = {"main": {"count": len(keys), "venues": per_venue}}
        names = {
            key: ref.display_name
            for key, ref in merged.items()
            if ref.display_name and ref.display_name != ref.symbol
        }
        return keys, names


async def list_venue(
    venue: str, size: int, ctx: RunContext
) -> tuple[list[InstrumentRef], str]:
    """venue 하나를 훑는다. 유동성 컷은 **venue마다 따로** 건다.

    섞어 자르면 거래대금 단위가 달라(원 vs 달러) 비교 자체가 성립하지 않는다 (3.7).
    """
    assert ctx.universe is not None  # RunContext.__post_init__이 채운다
    turnover = uses_turnover(venue)
    try:
        result = await ctx.universe.list_instruments(venue, source=AUTO, needs_turnover=turnover)
    except (UniverseNotSupportedError, NoProviderError) as exc:
        raise PipelineError(str(exc)) from exc

    entries = result.entries
    for note in result.notes:
        ctx.log.info(note)
    for warning in result.warnings:
        ctx.log.warning(warning)

    if turnover:
        entries = _top_by_turnover(entries, venue, size, result.source_id, ctx)
    else:
        entries = _head(entries, venue, size, ctx)
    return [e.instrument for e in entries], result.source_id


def _top_by_turnover(
    entries: list, venue: str, size: int, source_id: str, ctx: RunContext
) -> list:
    """거래대금 상위 N개. **절삭은 반드시 로그로 남긴다** (조용한 절삭 금지)."""
    priced = [e for e in entries if e.quote_volume_24h is not None]
    if entries and not priced:
        # ★ 전량이 거래대금 없음 = 소스가 아예 안 주는 것이다. 경고만 남기고 빈
        # 목록을 돌려주면 **그 시장이 통째로 사라진 채 실행이 성공한다.**
        raise PipelineError(
            f"{venue}: 소스 {source_id}가 거래대금을 주지 않아 상위 {size}종목을 "
            f"고를 수 없습니다({len(entries)}종목 전량 탈락)."
        )

    missing = [e.instrument.key for e in entries if e.quote_volume_24h is None]
    if missing:
        # 거래대금이 없는 종목을 0으로 취급하면 목록 맨 뒤로 밀려 조용히 사라진다.
        # "거래가 없었다"와 "소스가 값을 안 줬다"는 다르다.
        ctx.log.warning(
            f"{venue}: 거래대금을 받지 못해 유동성 컷에서 제외 {len(missing)}종목 — "
            f"{', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}"
        )

    ranked = sorted(priced, key=lambda e: e.quote_volume_24h, reverse=True)
    if len(ranked) > size:
        ctx.log.info(
            f"{venue}: 거래대금 상위 {size}종목만 통과 "
            f"({len(ranked)}종목 중 {len(ranked) - size}종목 컷)"
        )
    return ranked[:size]


def _head(entries: list, venue: str, size: int, ctx: RunContext) -> list:
    """목록 앞에서 N개. 거래대금을 주지 않는 venue의 대안이다.

    ⚠️ **소스가 정렬해 준 순서에 기댄다.** FDR의 미국 목록은 시총 순으로 보이지만
    문서화된 계약이 아니므로, 순서가 바뀌면 유니버스가 조용히 달라진다.
    """
    if len(entries) > size:
        ctx.log.warning(
            f"{venue}: 목록 앞 {size}종목만 통과 "
            f"({len(entries)}종목 중 {len(entries) - size}종목 컷). "
            f"거래대금 컷이 아니라 **소스가 준 순서**에 기댄 절삭입니다."
        )
    return entries[:size]


async def _fetch_bars(
    universe: list[str],
    names: dict[str, str],
    loaded: LoadedStrategy,
    rec: _Recorder,
    ctx: RunContext,
) -> Bundle:
    """봉 수집 단계. 재는 것과 받는 것을 갈라 놓았다."""
    lookback = lookback_for(loaded.strategy.startup_candles)

    with rec.stage("data", "marketData") as stage:
        stage.ctx.log.info(
            f"수집 깊이 {lookback}봉 (워밍업 {loaded.strategy.startup_candles} + 여유)"
        )
        bundle = await fetch_bars(universe, names, lookback, stage.ctx)
        stage.record.inputs = {"main": {"count": len(universe)}}
        stage.record.outputs = {"main": bundle.summary()}
        return bundle


async def fetch_bars(
    universe: list[str],
    names: dict[str, str],
    lookback: int,
    ctx: RunContext,
) -> Bundle:
    """봉을 받는다.

    두 가지 안전장치를 담고 있다.
      - **closed_only**: 미완성 봉을 잘라내 신호가 생겼다 사라지는 현상을 막는다 (4.4)
      - **skip_stale**: 직전 실행과 같은 봉이면 제외한다 (Fresh Bar Gate, 3.5)

    봉은 `ctx.ohlcv`에서 온다. **이 함수는 뒤에 `ohlcv_cache`가 있는지 모른다** (3.9).
    """
    log = ctx.log

    items: list[Item] = []
    stale: list[str] = []
    no_bar: list[str] = []
    fallbacks: list[str] = []
    from_cache: list[str] = []
    missing: list[str] = []

    for raw in universe:
        instrument = InstrumentRef.parse(raw)
        if names.get(instrument.key):
            instrument = _named(instrument, names[instrument.key])
        calendar = ctx.calendar_for(instrument)

        as_of = calendar.last_closed_bar(ctx.now, TIMEFRAME)
        if as_of is None:
            no_bar.append(instrument.key)
            continue
        ctx.assert_not_future(as_of, f"{instrument.key}의 as_of")

        # ---- Fresh Bar Gate ------------------------------------------------
        key = bar_key("data", instrument.key, TIMEFRAME)
        if not ctx.is_backtest:
            previous = ctx.bar_state.last_seen(key)
            if previous is not None and previous >= as_of:
                stale.append(instrument.key)
                continue

        assert ctx.ohlcv is not None
        try:
            fetched = await ctx.ohlcv.load(
                instrument, TIMEFRAME, as_of, lookback, source=AUTO, policy="auto"
            )
        except CacheMissError as exc:
            missing.append(instrument.key)
            log.warning(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - 한 종목의 실패가 나머지를 막지 않는다
            # Provider 라우팅이 이미 소스 폴백을 시도한 뒤다. 여기까지 왔으면
            # 그 종목은 오늘 받을 수 없다는 뜻이고, 전체를 세울 이유는 없다.
            no_bar.append(instrument.key)
            log.warning(f"{instrument.key} 수집 실패: {exc}")
            continue

        df = validate_ohlcv(fetched.df)
        df = df[df.index <= as_of]  # closed_only — 미완성 봉을 자른다

        meta: dict[str, object] = {
            "source": fetched.provider_id,
            # 설정값이 아니라 **소스가 실제로 준 것**을 적는다 (3.8). 이 값은
            # 캐시 키에 들어가므로(규칙 8) 틀리면 조정가/비조정가가 섞인다.
            "adjusted": fetched.adjusted,
        }
        for note in fetched.notes:
            log.warning(note)
        if fetched.from_cache:
            meta["cached_sources"] = list(fetched.cached_sources)
            from_cache.append(instrument.key)
        if fetched.used_fallback:
            meta["fallback_from"] = list(fetched.failed_sources)
            fallbacks.append(
                f"{instrument.key}: {', '.join(fetched.failed_sources)} → {fetched.provider_id}"
            )

        # 봉 소비는 예약만 한다. 실행이 성공해야 execute()가 확정한다.
        ctx.bar_state.stage(key, as_of)
        items.append(
            Item(instrument=instrument, timeframe=TIMEFRAME, as_of=as_of, ohlcv=df, meta=meta)
        )

    if stale:
        log.info(f"새로 마감된 봉이 없어 제외: {', '.join(stale)}")
    if no_bar:
        log.warning(f"마감된 봉을 찾지 못함: {len(no_bar)}종목")
    if from_cache:
        log.info(f"{len(from_cache)}종목을 ohlcv_cache에서 읽었습니다 (외부 호출 없음)")
    if missing:
        log.warning(
            f"캐시 부족으로 제외 {len(missing)}종목 — "
            f"`marketscan ingest --commit`으로 봉을 쌓으세요."
        )
    if fallbacks:
        log.warning(
            f"소스 폴백 발동 — {' | '.join(fallbacks)}. "
            f"지표가 불연속해 보이면 원래 소스의 상태를 확인하세요."
        )
    log.info(f"{len(items)}개 종목 수집 완료 ({TIMEFRAME})")
    return Bundle(items, {"universe": universe})


def _run_strategy(
    bundle: Bundle, loaded: LoadedStrategy, rec: _Recorder, ctx: RunContext
) -> Bundle:
    """`compute`(시계열) → `rank`(횡단면) → `select`(컷).

    ★ 순서는 `stages.run_stages`가 단일 출처다 — `backtest`의 리플레이가 **같은
    함수**를 부른다. 여기에 순서를 다시 적으면 언젠가 한쪽만 바뀌고, 그날부터
    백테스트가 실거래와 다른 코드를 돌면서 재현했다고 말한다.
    """
    strategy = loaded.strategy

    with rec.stage("strategy", "strategyRunner") as stage:
        log = stage.ctx.log
        params = strategy.Params()  # ★ 값은 전략 파일이 정본이다 (4.8)

        eligible = eligible_items(bundle, strategy, True, stage.ctx)
        if not eligible:
            log.info("판정할 종목이 없습니다")
            empty = bundle.replace_items([])
            stage.record.inputs = {"main": bundle.summary()}
            stage.record.outputs = {"main": empty.summary()}
            return empty

        stages = run_stages(
            strategy, Bundle(eligible, dict(bundle.context)), params, stage.ctx
        )
        ranked, selected = stages.ranked, stages.selected

        # `universe_size`는 **점수가 나온** 종목 수다. 훑은 종목 수와 다르고, 둘을
        # 같은 말로 읽으면 "30개 중 2등"으로 오해한다 — 봉이 모자란 종목이 비교
        # 대상에서 이미 빠져 있다. 분모의 출처를 신호에 남긴다.
        scanned = len(bundle)
        selected = selected.map(lambda it: it.with_features(universe_scanned=scanned))

        log.info(
            f"{strategy.id}: {len(bundle)}개 입력 → {len(ranked)}개 랭킹 → {len(selected)}개 선정"
        )
        selected.context["strategy"] = {
            "id": strategy.id,
            "sha256": loaded.sha256,
            "timeframe": strategy.timeframe,
            "params": params.model_dump(mode="json"),
            "universe_size": len(ranked),
            "ranked_top": [
                {
                    "instrument": item.instrument.key,
                    "rank": item.features.get(RANK_FEATURE),
                    "score": item.features.get("score"),
                }
                for item in ranked.items[:RANK_SNAPSHOT_SIZE]
            ],
        }
        stage.record.inputs = {"main": bundle.summary()}
        stage.record.outputs = {"main": selected.summary()}
        return selected


async def _persist(bundle: Bundle, rec: _Recorder, ctx: RunContext) -> None:
    """`signals`에 남긴다 — **부작용의 유무는 배출구가 정한다** (규칙 11).

    `ctx.signals`가 dry-run이면 메모리에만 담기고, `--commit`이 붙었을 때만 CLI가
    DB 배출구를 꽂는다. 여기에 분기를 심지 않는다.
    """
    with rec.stage("persist", "persistSignal") as stage:
        log = stage.ctx.log
        if bundle.is_empty:
            log.info("기록할 신호가 없습니다")
            return

        strategy = bundle.context.get("strategy")
        written = duplicates = 0
        for item in bundle:
            draft = draft_from_item(
                item,
                run_id=ctx.run_id,
                pipeline_id=ctx.pipeline_id,
                node_id="persist",
                kind="entry",
                strategy=strategy if isinstance(strategy, dict) else None,
            )
            if await ctx.signals.emit(draft):
                written += 1
            else:
                duplicates += 1

        if ctx.signals.persistent:
            log.info(f"신호 {written}건 기록 (중복 제외 {duplicates}건)")
        else:
            log.info(
                f"dry-run — 신호 {written}건을 기록하지 **않았습니다**. "
                f"실제로 남기려면 --commit을 붙이세요."
            )
        stage.record.outputs = {"main": {"written": written, "duplicates": duplicates}}


def _log_candidates(bundle: Bundle, rec: _Recorder, ctx: RunContext) -> None:
    """후보를 실행 로그에 남긴다. **바깥으로 나가지 않는다.**

    산출물은 stdout과 HTML 리포트뿐이다. 채널 전송은 `serve`의 몫이다 (12.2).
    """
    with rec.stage("log", "logAlert") as stage:
        if bundle.is_empty:
            stage.ctx.log.info("남길 신호가 없습니다")
            return
        for item in bundle.items[:20]:
            name = item.instrument.display_name or item.instrument.symbol
            stage.ctx.log.info(
                f"[{ctx.mode.upper()}] [{item.instrument.venue}] {name} · "
                f"{item.last_close} {item.instrument.quote_currency} ({item.as_of.isoformat()})"
            )
        if len(bundle) > 20:
            stage.ctx.log.warning(
                f"{len(bundle) - 20}건을 로그에 남기지 않았습니다. 전체는 HTML 리포트에 있습니다."
            )


# --------------------------------------------------------------------------- 내부
async def _with_retry(fn, *, stage: _Stage, ctx: RunContext, what: str):
    """지수 백오프 재시도. 실패가 이어지면 그대로 터뜨린다."""
    last: Exception | None = None
    for attempt in range(1, UNIVERSE_ATTEMPTS + 1):
        stage.record.attempts = attempt
        try:
            return await fn()
        except PipelineError:
            raise  # 능력 문제(거래대금 없음 등)는 재시도해도 같다
        except Exception as exc:  # noqa: BLE001 - 재시도를 위해 광범위하게 잡는다
            last = exc
            if attempt < UNIVERSE_ATTEMPTS:
                ctx.log.warning(f"{what} {attempt}회차 실패, 재시도합니다: {exc}")
                await asyncio.sleep(UNIVERSE_BACKOFF * (2 ** (attempt - 1)))
    assert last is not None
    raise PipelineError(f"{what} 조회에 실패했습니다 — {last}") from last


def _named(instrument: InstrumentRef, display_name: str) -> InstrumentRef:
    from dataclasses import replace

    return replace(instrument, display_name=str(display_name))


def bars_seen(result: RunResult) -> int:
    """이 실행이 훑은 종목 수. 리포트와 CLI가 쓴다."""
    data = result.node("data")
    return int((data.outputs.get("main", {}) if data else {}).get("count", 0))


def as_of_of(result: RunResult) -> datetime | None:
    return datetime.fromisoformat(result.now) if result.now else None
