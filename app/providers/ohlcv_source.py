"""노드가 봉을 얻는 유일한 창구 (ARCHITECTURE.md 3.9).

```
[Ingestion Worker] ──주기 수집──▶ [ohlcv_cache] ◀──읽기 전용── [MarketData 노드]
```

3.9가 "노드는 캐시 구현을 모른다"고 정했으므로, 노드는 `ctx.ohlcv.load(...)`만
부르고 그 뒤가 캐시인지 소스 직접 호출인지 알지 못한다. 덕분에 뒤를 갈아 끼울 수 있다.

**왜 `adjusted`가 여기서 결정되는가**

캐시 키에 `adjusted`가 들어가므로(규칙 8) 캐시를 **읽기 전에** 그 값이 정해져야
하는데, 실제 값은 어느 소스가 응답했는지에 달려 있다. 그래서 두 단계로 나눈다.

  - **읽을 때**: 라우팅 후보들의 `capabilities.adjusted`로 예측한다.
  - **쓸 때**: 실제로 응답한 소스의 값으로 쓴다.

예측이 틀리면 다음 실행이 캐시를 못 맞히는 것으로 끝난다(성능 손해). 반대로
쓸 때 예측값을 쓰면 **조정가와 비조정가가 한 키에 섞여** 지표가 조용히 어긋나므로,
느려지는 쪽을 택했다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

import pandas as pd
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.market.instrument import InstrumentRef
from app.providers.registry import AUTO, ProviderRegistry

log = logging.getLogger(__name__)

#: 캐시 사용 정책. 노드 파라미터로 노출된다.
CachePolicy = Literal["auto", "off", "only"]


class CacheMissError(RuntimeError):
    """`cache: only`인데 캐시가 요청 구간을 채우지 못했을 때.

    조용히 소스를 부르지 않는다 — `only`를 고른 이유는 대개 "외부 호출을 하지
    않겠다"(백테스트·소스 장애)이고, 몰래 네트워크를 타면 그 전제가 깨진다.
    4.8이 백테스트 시작 전 커버리지를 확인해 **명확한 사유와 함께 거부**하라고
    정한 것과 같은 자리다.
    """


@dataclass
class LoadResult:
    df: pd.DataFrame
    provider_id: str
    """봉을 준 곳. 캐시에서 나왔으면 `cache`."""

    adjusted: bool
    """**소스가 실제로 준 것.** 설정값이 아니다 — 캐시 키에 들어간다 (규칙 8)."""

    failed_sources: tuple[str, ...] = ()
    from_cache: bool = False
    cached_sources: tuple[str, ...] = ()
    """캐시 구간을 채운 원래 소스들. 폴백으로 소스가 섞인 구간을 사후에 찾는 단서다."""

    notes: list[str] = field(default_factory=list)
    """노드가 `ctx.log`로 올릴 메시지. 여기서는 로깅하지 않는다 — node_runs에
    남으려면 노드의 로거를 타야 한다."""

    @property
    def used_fallback(self) -> bool:
        return bool(self.failed_sources)


class OhlcvSource(Protocol):
    """노드가 보는 인터페이스. 이 뒤가 캐시인지 소스인지는 노드의 관심사가 아니다."""

    async def load(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
        *,
        source: str = AUTO,
        policy: CachePolicy = "auto",
    ) -> LoadResult: ...


@dataclass
class DirectSource:
    """캐시 없이 매번 소스를 부른다. DB가 없는 실행(첫 dry-run)의 기본값이다."""

    registry: ProviderRegistry
    default_adjusted: bool = True

    async def load(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
        *,
        source: str = AUTO,
        policy: CachePolicy = "auto",
    ) -> LoadResult:
        if policy == "only":
            raise CacheMissError(
                f"{instrument.key}: cache=only인데 캐시가 없습니다. "
                f"`stockscan ingest --commit`으로 먼저 봉을 쌓으세요."
            )
        result = await self.registry.fetch_ohlcv(instrument, timeframe, end, limit, source=source)
        return LoadResult(
            df=result.df,
            provider_id=result.provider_id,
            adjusted=adjusted_of(self.registry, result.provider_id, self.default_adjusted),
            failed_sources=result.failed_sources,
        )


@dataclass
class CachedSource:
    """`ohlcv_cache`를 먼저 보고, 부족하면 소스를 부른다.

    쓰기는 `writable`일 때만 — 즉 `--commit`에서만이다. dry-run이 캐시를 채우면
    "읽기 전용 명령은 DB 파일조차 만들지 않는다"(12.1 / 규칙 11)가 깨진다.
    읽기는 dry-run에서도 한다. 읽지 않으면 dry-run이 실제 실행을 예측하지 못한다.
    """

    registry: ProviderRegistry
    sessionmaker: async_sessionmaker[AsyncSession]
    writable: bool = False
    default_adjusted: bool = True
    _degraded: bool = False
    """캐시 테이블을 읽지 못했다. 한 번 경고하고 이후 실행은 소스로만 돈다."""

    async def load(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        end: datetime,
        limit: int,
        *,
        source: str = AUTO,
        policy: CachePolicy = "auto",
    ) -> LoadResult:
        notes: list[str] = []
        expected = predicted_adjusted(
            self.registry, instrument, timeframe, self.default_adjusted, source
        )

        if policy != "off":
            cached = await self._read(instrument, timeframe, expected, end, limit, policy, notes)
            if cached is not None:
                return cached

        if policy == "only":
            raise CacheMissError(
                f"{instrument.key} · {timeframe}: 캐시가 요청 구간({limit}봉, "
                f"~{end.isoformat()})을 채우지 못했습니다. "
                f"`stockscan ingest --commit --lookback {limit}`으로 먼저 쌓으세요."
            )

        result = await self.registry.fetch_ohlcv(instrument, timeframe, end, limit, source=source)
        actual = adjusted_of(self.registry, result.provider_id, self.default_adjusted)
        if actual != expected:
            # 폴백으로 수정주가 정책이 다른 소스가 답한 경우다. 쓰기는 실제 값으로
            # 가므로 섞이지는 않지만, 다음 실행이 캐시를 못 맞히는 이유가 된다.
            notes.append(
                f"{instrument.key}: 캐시 키의 adjusted 예측({expected})과 실제 소스"
                f"({result.provider_id}, {actual})가 다릅니다."
            )
        if self.writable and self._cacheable(result.provider_id):
            notes.extend(await self._write(instrument, timeframe, result, actual, limit, end))

        return LoadResult(
            df=result.df,
            provider_id=result.provider_id,
            adjusted=actual,
            failed_sources=result.failed_sources,
            notes=notes,
        )

    # ------------------------------------------------------------------- 내부
    def _cacheable(self, provider_id: str) -> bool:
        """가짜 시세를 영구 자산에 넣지 않는다. `ProviderCapabilities.cacheable` 참조."""
        try:
            return self.registry.get(provider_id).capabilities.cacheable
        except Exception:  # noqa: BLE001 - 모르는 소스면 쓰지 않는 쪽이 안전하다
            return False

    async def _read(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        adjusted: bool,
        end: datetime,
        limit: int,
        policy: CachePolicy,
        notes: list[str],
    ) -> LoadResult | None:
        if self._degraded:
            return None
        from app.storage import ohlcv_cache
        from app.storage.models import IngestionJobRow

        try:
            async with self.sessionmaker() as session:
                df, sources = await ohlcv_cache.read_bars(
                    session, instrument, timeframe, adjusted=adjusted, end=end, limit=limit
                )
                if df.empty:
                    return None

                job = await session.get(
                    IngestionJobRow,
                    (instrument.venue, instrument.symbol, timeframe, adjusted),
                )
                # ★ **"소스가 줄 수 있는 만큼은 다 받았다"**를 판정한다.
                #
                # `last_bar_time`이 아니라 `last_success_at`으로 보는 이유는
                # 거래정지·폐지 종목이다 — 그 봉이 애초에 없어서 봉 시각으로 보면
                # 영원히 "덜 받았다"가 된다. `lookback`은 그때 **요청한** 깊이라,
                # 캐시가 그보다 얕다면 소스에 더 없는 것이다(신규 상장).
                saturated = (
                    job is not None
                    and job.last_success_at is not None
                    and job.last_success_at >= end
                    and (job.lookback or 0) >= limit
                )

                covers_end = df.index[-1].to_pydatetime() >= end or saturated
                deep_enough = len(df) >= limit or saturated
                if not (covers_end and deep_enough) and policy != "only":
                    # 부족하면 소스로 간다 — 조용히 짧은 계열을 주면 전략이
                    # startup_candles에 못 미쳐 종목이 통째로 사라지고, 그 이유가
                    # 어디에도 남지 않는다.
                    return None
        except SQLAlchemyError as exc:
            # 캐시 테이블이 아직 없는 DB(구버전)이거나 잠겼다. 수집이 안 됐다고
            # 실행을 실패시키지 않는다 — 소스는 여전히 살아 있다.
            self._degraded = True
            log.warning("ohlcv_cache를 읽지 못했습니다 (%s). 이번 실행은 소스로 돕니다.", exc)
            return None

        if len(df) < limit:
            notes.append(
                f"{instrument.key}: 캐시가 {len(df)}봉만 가지고 있습니다(요청 {limit}). "
                f"`stockscan ingest --commit --lookback {limit}`으로 더 쌓으세요."
            )
        return LoadResult(
            df=df,
            provider_id="cache",
            adjusted=adjusted,
            from_cache=True,
            cached_sources=sources,
            notes=notes,
        )

    async def _write(
        self,
        instrument: InstrumentRef,
        timeframe: str,
        result: object,
        adjusted: bool,
        limit: int,
        end: datetime,
    ) -> list[str]:
        from app.storage import ohlcv_cache

        df = result.df  # type: ignore[attr-defined]
        provider_id = result.provider_id  # type: ignore[attr-defined]
        try:
            async with self.sessionmaker() as session:
                report = await ohlcv_cache.write_bars(
                    session,
                    instrument,
                    timeframe,
                    df,
                    adjusted=adjusted,
                    source_id=provider_id,
                )
                # 수집 이력도 남긴다. `run`만 쓰는 사용자에게도 "이 봉까지 받아 봤다"가
                # 기록되어야 짧은 이력 종목이 매번 소스를 다시 부르지 않는다.
                # `ingest`가 같은 봉을 두 번 받지 않게 되는 것도 여기서 온다.
                await ohlcv_cache.record_job(
                    session,
                    instrument,
                    timeframe,
                    adjusted=adjusted,
                    success=True,
                    bars=report.written,
                    lookback=limit,
                    last_bar_time=df.index[-1].to_pydatetime() if not df.empty else None,
                    source_id=provider_id,
                    now=end,
                )
                await session.commit()
        except SQLAlchemyError as exc:
            # 캐시 쓰기 실패가 실행을 실패시키면 안 된다. 봉은 이미 손에 있다.
            log.warning("ohlcv_cache 쓰기 실패 (%s): %s", instrument.key, exc)
            return [f"{instrument.key}: 캐시에 쓰지 못했습니다 ({exc})."]
        return [c.describe() for c in report.conflicts]


# --------------------------------------------------------------------- adjusted
def adjusted_of(registry: ProviderRegistry, provider_id: str, default: bool) -> bool:
    """이 소스가 **실제로** 수정주가를 주는가.

    `always`/`never`는 설정과 무관하게 소스가 결정한다. 코인에는 액면분할·배당이
    없어 adjusted 개념 자체가 없는데(3.8) 설정을 그대로 베끼면 조정가를 받은
    것처럼 남는다. `optional`일 때만 파이프라인 설정이 의미를 갖는다.
    """
    try:
        capability = registry.get(provider_id).capabilities.adjusted
    except Exception:  # noqa: BLE001 - 출처 기록이 수집을 실패시키면 안 된다
        return default
    if capability == "always":
        return True
    if capability == "never":
        return False
    return default


def predicted_adjusted(
    registry: ProviderRegistry,
    instrument: InstrumentRef,
    timeframe: str,
    default: bool,
    source: str = AUTO,
) -> bool:
    """캐시를 **읽기 전에** 쓸 키. 라우팅 후보들이 합의하면 그 값, 아니면 설정값.

    후보가 갈리는 경우(한쪽은 always, 한쪽은 never)는 실제로는 없다 — 주식 소스는
    전부 always, 코인은 never다. 그래도 합의를 확인하는 이유는, 갈리는 소스가
    추가된 날 조용히 틀린 키로 읽는 것보다 캐시를 못 맞히는 쪽이 안전해서다.
    """
    if source != AUTO:
        return adjusted_of(registry, source, default)
    try:
        candidates = registry.resolve(instrument, timeframe)
    except Exception:  # noqa: BLE001
        return default
    values = {adjusted_of(registry, p.id, default) for p in candidates}
    return values.pop() if len(values) == 1 else default
