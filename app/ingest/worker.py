"""Ingestion Worker — 봉을 모아 `ohlcv_cache`에 쌓는다 (ARCHITECTURE.md 3.9).

```
[Ingestion Worker] ──주기 수집──▶ [ohlcv_cache] ◀──읽기 전용── [MarketData 노드]
```

Fetcher 노드가 매 실행마다 외부 API를 직접 호출하면 200종목을 훑는 순간 무료 API가
막힌다. 수집을 실행에서 떼어 내면 **레이트 리밋을 한 곳에서만 관리**하고, 소스가
죽어도 쌓인 봉으로 파이프라인이 계속 돈다.

**수집 대상은 활성 파이프라인이 참조하는 instrument의 합집합에서 자동 도출한다**
(3.9). 손으로 목록을 관리하면 파이프라인을 고칠 때마다 어긋나고, 어긋난 종목은
캐시가 비어 조용히 유니버스에서 빠진다.

★ **상장폐지 종목도 대상이다.** 살아 있는 종목만 쌓으면 서바이버십 편향을 데이터
레이어에서 이미 만들어 놓는 셈이다 (4.8). 폐지 종목은 폐지 시점을 `end`로 잡아야
한다 — 오늘을 기준으로 조회하면 그 구간에 봉이 없어 **빈 결과가 성공처럼 보인다.**
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.market.calendar import CalendarRangeError
from app.market.instrument import InstrumentRef
from app.market.timeframe import normalize
from app.nodes.inputs.symbol_universe import UNIVERSE_KEY, SymbolUniverseNode
from app.providers.ohlcv_source import adjusted_of, predicted_adjusted
from app.providers.registry import AUTO
from app.storage import ohlcv_cache
from app.storage.models import IngestionJobRow

#: 폐지 종목을 어디까지 거슬러 올라가 모을 것인가. FDR의 폐지 목록은 1990년대까지
#: 있는데 그때 봉은 지금 전략의 유니버스와 무관하고 조회도 자주 실패한다.
DEFAULT_DELISTED_SINCE = date(2015, 1, 1)


@dataclass(frozen=True)
class IngestTarget:
    """수집 단위 하나. `end`가 대상마다 다른 것이 핵심이다 (폐지 종목)."""

    instrument: InstrumentRef
    timeframe: str
    lookback: int
    end: datetime
    origin: str = "pipeline"
    """pipeline(노드에 적힌 고정 목록) · universe(거래소 조회) · delisted."""

    source: str = AUTO
    """노드가 소스를 못 박았다면 그대로 따른다.

    수집이 라우팅을 무시하고 아무 소스나 쓰면 **캐시를 채운 소스와 파이프라인이
    쓰려던 소스가 갈린다.** 수정주가 정책이 다르면 그 자리에서 계열이 어긋난다 (3.8).
    """

    @property
    def key(self) -> tuple[str, str]:
        return (self.instrument.key, self.timeframe)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument.key,
            "timeframe": self.timeframe,
            "lookback": self.lookback,
            "end": self.end.isoformat(),
            "origin": self.origin,
            "source": self.source,
        }


@dataclass
class Plan:
    targets: list[IngestTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """사람에게 올릴 경고. 조용한 누락을 만들지 않기 위한 창구다."""

    def filtered(self, venue: str | None) -> Plan:
        if not venue:
            return self
        return Plan([t for t in self.targets if t.instrument.venue == venue], list(self.notes))


@dataclass
class IngestReport:
    planned: int = 0
    fetched: int = 0
    skipped_fresh: int = 0
    """이미 이 봉까지 수집이 성공했다. 하루 여러 번 불러도 소스를 두드리지 않는다."""

    inserted: int = 0
    updated: int = 0
    empty: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    """두 소스가 같은 봉을 다르게 준 경우 (3.8). 덮어쓰되 반드시 드러낸다."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "fetched": self.fetched,
            "skipped_fresh": self.skipped_fresh,
            "inserted": self.inserted,
            "updated": self.updated,
            "empty": self.empty,
            "failures": [{"instrument": k, "error": v} for k, v in self.failures],
            "conflicts": self.conflicts,
        }


# ------------------------------------------------------------------------ 계획
async def plan_targets(
    spec: Any,
    ctx: RunContext,
    *,
    lookback: int | None = None,
    include_delisted: bool = False,
    delisted_since: date | None = None,
) -> Plan:
    """파이프라인이 참조하는 instrument의 합집합을 수집 대상으로 편다.

    유니버스 노드를 실제로 **실행해서** 목록을 얻는다. 컷 조건(거래대금 상위 N,
    결제 통화)을 여기서 다시 구현하면 파이프라인이 훑는 것과 캐시가 담는 것이
    갈라지고, 갈라진 종목은 실행 때 캐시 미스로 조용히 소스를 두드린다.
    """
    plan = Plan()
    universe = await _resolve_universe(spec, ctx, plan)

    seen: dict[tuple[str, str], IngestTarget] = {}
    for node in spec.nodes:
        if node.type != "marketData":
            continue
        timeframe = normalize(node.params.get("timeframe", "1d"))
        depth = lookback or int(node.params.get("lookback", 200))
        source = str(node.params.get("source") or AUTO)
        fixed = [str(s) for s in (node.params.get("instruments") or [])]
        keys = fixed or universe
        origin = "pipeline" if fixed else "universe"

        for raw in keys:
            instrument = InstrumentRef.parse(raw)
            end = _last_closed(instrument, timeframe, ctx, plan)
            if end is None:
                continue
            _add(seen, IngestTarget(instrument, timeframe, depth, end, origin, source))

    if include_delisted:
        await _add_delisted(seen, ctx, plan, lookback or 500, delisted_since)

    plan.targets = list(seen.values())
    if not plan.targets:
        plan.notes.append(
            "수집 대상이 없습니다. 파이프라인에 marketData 노드가 있는지, "
            "유니버스가 비어 있지 않은지 확인하세요."
        )
    return plan


async def _resolve_universe(spec: Any, ctx: RunContext, plan: Plan) -> list[str]:
    keys: list[str] = []
    for node in spec.nodes:
        if node.type != "symbolUniverse":
            continue
        params = SymbolUniverseNode.parse_params(node.params)
        try:
            output = await SymbolUniverseNode().run({}, params, ctx.bind(node.id))
        except Exception as exc:  # noqa: BLE001 - 한 노드가 죽어도 나머지는 모은다
            plan.notes.append(f"유니버스 노드 {node.id}를 풀지 못했습니다: {exc}")
            continue
        bundle: Bundle = output["main"]
        for key in bundle.context.get(UNIVERSE_KEY, []):
            if key not in keys:
                keys.append(str(key))
    return keys


def _last_closed(
    instrument: InstrumentRef, timeframe: str, ctx: RunContext, plan: Plan
) -> datetime | None:
    try:
        end = ctx.calendar_for(instrument).last_closed_bar(ctx.now, timeframe)
    except CalendarRangeError as exc:
        plan.notes.append(f"{instrument.key}: {exc}")
        return None
    if end is None:
        plan.notes.append(f"{instrument.key}: 마감된 {timeframe} 봉을 찾지 못했습니다.")
    return end


def _add(seen: dict[tuple[str, str], IngestTarget], target: IngestTarget) -> None:
    """같은 (종목, 봉)이 여러 노드에 걸리면 **더 깊은 lookback**을 남긴다.

    얕은 쪽으로 덮으면 캐시가 짧아져 다른 노드가 매번 캐시를 못 맞힌다.
    """
    current = seen.get(target.key)
    if current is None or target.lookback > current.lookback:
        seen[target.key] = target


async def _add_delisted(
    seen: dict[tuple[str, str], IngestTarget],
    ctx: RunContext,
    plan: Plan,
    lookback: int,
    since: date | None,
) -> None:
    """폐지 종목을 폐지 시점 기준으로 대상에 넣는다 (3.9 / 4.8 서바이버십)."""
    try:
        provider = ctx.providers.get("fdr")
        frame = await provider.list_delisted("krx", since=since or DEFAULT_DELISTED_SINCE)
    except Exception as exc:  # noqa: BLE001 - 폐지 목록 실패가 본 수집을 막으면 안 된다
        plan.notes.append(f"상장폐지 목록을 받지 못했습니다: {exc}")
        return

    calendar = ctx.calendars["krx"]
    added = 0
    for row in frame.itertuples():
        symbol = str(getattr(row, "Symbol", "")).strip()
        delisted_at = getattr(row, "DelistingDate", None)
        if not symbol or delisted_at is None or pd.isna(delisted_at):
            continue
        # 폐지일 장 마감 시각. 오늘을 end로 잡으면 그 구간에 봉이 없어 **빈 결과가
        # 성공처럼 보인다** — 서바이버십을 막으려던 수집이 아무것도 못 모은다.
        moment = pd.Timestamp(delisted_at).to_pydatetime().replace(tzinfo=ctx.now.tzinfo)
        moment = min(moment + timedelta(days=1), ctx.now)
        try:
            end = calendar.last_closed_bar(moment, "1d")
        except CalendarRangeError:
            continue
        if end is None:
            continue
        instrument = InstrumentRef.parse(f"krx:{symbol}")
        if (instrument.key, "1d") in seen:
            # 살아 있는 대상이 이미 있으면 건드리지 않는다 — 폐지 시점의 `end`로
            # 덮으면 그 종목의 최신 봉을 더 이상 모으지 않게 된다.
            continue
        seen[(instrument.key, "1d")] = IngestTarget(
            instrument, "1d", lookback, end, "delisted"
        )
        added += 1
    plan.notes.append(f"상장폐지 {added}종목을 대상에 넣었습니다 (폐지 시점 기준).")


# ------------------------------------------------------------------------ 수집
async def ingest(
    plan: Plan,
    ctx: RunContext,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    force: bool = False,
    pause_seconds: float = 0.0,
    progress: Any = None,
) -> IngestReport:
    """대상을 순차로 모아 캐시에 쌓는다. **부작용이므로 호출자가 `--commit`을 확인한다.**

    순차인 것은 의도다 — 무료 소스의 레이트 리밋을 한 지점에서만 밟으려는 것이
    이 워커의 존재 이유이고(3.9), 일봉 하루 1회면 병렬로 얻을 것이 없다.
    종목 하나가 실패해도 멈추지 않고, 실패는 `ingestion_jobs`에 누적된다.
    """
    report = IngestReport(planned=len(plan.targets))

    for target in plan.targets:
        instrument, timeframe = target.instrument, target.timeframe
        adjusted = predicted_adjusted(
            ctx.providers, instrument, timeframe, ctx.settings.adjusted, target.source
        )
        async with sessionmaker() as session:
            if not force and await _is_fresh(session, instrument, timeframe, adjusted, target.end):
                report.skipped_fresh += 1
                continue

        try:
            result = await ctx.providers.fetch_ohlcv(
                instrument, timeframe, target.end, target.lookback, source=target.source
            )
        except Exception as exc:  # noqa: BLE001 - 한 종목의 실패가 수집을 멈추면 안 된다
            report.failures.append((instrument.key, f"{type(exc).__name__}: {exc}"))
            async with sessionmaker() as session:
                await ohlcv_cache.record_job(
                    session,
                    instrument,
                    timeframe,
                    adjusted=adjusted,
                    success=False,
                    error=str(exc)[:500],
                )
                await session.commit()
            continue

        report.fetched += 1
        # 쓰기 키는 **실제로 응답한 소스**의 adjusted다. 예측값으로 쓰면 폴백이
        # 발동한 날 조정가와 비조정가가 한 키에 섞인다 (규칙 8).
        actual = adjusted_of(ctx.providers, result.provider_id, ctx.settings.adjusted)

        if result.df.empty:
            report.empty.append(instrument.key)

        async with sessionmaker() as session:
            written = await ohlcv_cache.write_bars(
                session,
                instrument,
                timeframe,
                result.df,
                adjusted=actual,
                source_id=result.provider_id,
            )
            last_bar = (
                result.df.index[-1].to_pydatetime() if not result.df.empty else None
            )
            await ohlcv_cache.record_job(
                session,
                instrument,
                timeframe,
                adjusted=actual,
                success=True,
                bars=written.written,
                lookback=target.lookback,
                last_bar_time=last_bar,
                source_id=result.provider_id,
                now=ctx.now,
            )
            await session.commit()

        report.inserted += written.inserted
        report.updated += written.updated
        report.conflicts.extend(c.describe() for c in written.conflicts)
        if progress is not None:
            progress(target, written)
        if pause_seconds:
            await asyncio.sleep(pause_seconds)

    return report


async def _is_fresh(
    session: AsyncSession,
    instrument: InstrumentRef,
    timeframe: str,
    adjusted: bool,
    end: datetime,
) -> bool:
    """이 봉까지 이미 수집이 성공했는가.

    `last_bar_time`이 아니라 `last_success_at`으로 판정한다 — 거래정지·폐지 종목은
    그 봉이 애초에 없어서, 봉 시각으로 보면 **영원히 새로 수집한다.**
    """
    job = await session.get(
        IngestionJobRow, (instrument.venue, instrument.symbol, timeframe, adjusted)
    )
    return job is not None and job.last_success_at is not None and job.last_success_at >= end


async def coverage_of(
    plan: Plan, ctx: RunContext, sessionmaker: async_sessionmaker[AsyncSession]
) -> list[dict[str, Any]]:
    """대상별 현재 캐시 커버리지. `ingest`의 dry-run이 보여 준다."""
    rows: list[dict[str, Any]] = []
    async with sessionmaker() as session:
        for target in plan.targets:
            adjusted = predicted_adjusted(
                ctx.providers,
                target.instrument,
                target.timeframe,
                ctx.settings.adjusted,
                target.source,
            )
            cov = await ohlcv_cache.coverage(
                session, target.instrument, target.timeframe, adjusted=adjusted
            )
            rows.append({**target.to_dict(), "adjusted": adjusted, **cov.to_dict()})
    return rows
