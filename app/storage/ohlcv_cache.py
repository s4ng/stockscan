"""`ohlcv_cache` 읽기·쓰기 (ARCHITECTURE.md 3.9).

**캐시는 성능 최적화가 아니라 영구 보관하는 데이터 자산이다.** 무료 소스는
언제든 막힌다는 전제이고, 막힌 뒤에 남는 것은 여기 쌓인 것뿐이다. 그래서
이 모듈에는 **삭제 함수가 없다.**

SQLite 전용 문법(`INSERT ... ON CONFLICT`)을 쓰지 않는다. 3.9가 "노드는 캐시
구현을 모른다 · 스키마를 SQLite 전용 문법으로 굳히지 않는다"고 정했기 때문이다.
대신 구간의 기존 봉을 한 번에 읽어 신규/변경으로 가르는데, 이 비교가 **두 소스
종가 정합성 검증**(3.8)을 공짜로 해 준다 — 같은 봉을 다른 소스가 다르게 주면
그 자리에서 드러난다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.types import OHLCV_COLUMNS, empty_ohlcv
from app.market.instrument import InstrumentRef
from app.storage.models import IngestionJobRow, OhlcvCacheRow, utcnow

#: 종가가 이 비율 이상 어긋나면 정합성 경고를 낸다. 부동소수점 왕복과 소스별
#: 반올림 차이(0.01%)까지 경고로 올리면 경고가 무의미해진다.
CLOSE_MISMATCH_TOLERANCE = 1e-4


@dataclass(frozen=True)
class CloseConflict:
    """같은 봉을 두 소스가 다르게 준 경우 (3.8).

    어느 쪽이 옳은지 여기서 판정하지 않는다 — **덮어쓰되 드러낸다.** 조용히
    넘어가면 수정주가 정책 차이로 생긴 계열 불연속을 사후에 찾을 수 없다.
    """

    instrument: str
    bar_time: datetime
    stored_close: float
    stored_source: str
    incoming_close: float
    incoming_source: str

    def describe(self) -> str:
        drift = abs(self.incoming_close - self.stored_close) / max(abs(self.stored_close), 1e-12)
        return (
            f"{self.instrument} {self.bar_time.date()} 종가 불일치: "
            f"{self.stored_source} {self.stored_close:,.6g} ≠ "
            f"{self.incoming_source} {self.incoming_close:,.6g} ({drift:.2%})"
        )


@dataclass
class WriteReport:
    inserted: int = 0
    updated: int = 0
    conflicts: list[CloseConflict] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.inserted + self.updated


@dataclass(frozen=True)
class Coverage:
    """이 종목이 캐시에 얼마나 쌓여 있는가."""

    bars: int
    first: datetime | None
    last: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "bars": self.bars,
            "first": self.first.isoformat() if self.first else None,
            "last": self.last.isoformat() if self.last else None,
        }


# ------------------------------------------------------------------------- 읽기
async def read_bars(
    session: AsyncSession,
    instrument: InstrumentRef,
    timeframe: str,
    *,
    adjusted: bool,
    end: datetime,
    limit: int,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """`end`(포함) 이전의 마감 봉을 최대 limit개. 프레임과 그것을 채운 소스들.

    **`end` 이후는 조건에서 아예 제외한다.** Provider와 같은 계약이다(규칙 2) —
    캐시가 예외가 되면 백테스트 리플레이가 미래를 본다.
    """
    stmt = (
        select(OhlcvCacheRow)
        .where(
            OhlcvCacheRow.venue == instrument.venue,
            OhlcvCacheRow.symbol == instrument.symbol,
            OhlcvCacheRow.timeframe == timeframe,
            OhlcvCacheRow.adjusted == adjusted,
            OhlcvCacheRow.bar_time <= end,
        )
        .order_by(OhlcvCacheRow.bar_time.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars())
    if not rows:
        return empty_ohlcv(), ()

    rows.reverse()  # 저장소 계약은 오름차순이다
    frame = pd.DataFrame(
        {
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        },
        index=pd.DatetimeIndex([r.bar_time for r in rows], tz="UTC", name="time"),
    )
    sources = tuple(dict.fromkeys(r.source_id for r in rows))
    return frame, sources


async def coverage(
    session: AsyncSession,
    instrument: InstrumentRef,
    timeframe: str,
    *,
    adjusted: bool,
) -> Coverage:
    stmt = select(OhlcvCacheRow.bar_time).where(
        OhlcvCacheRow.venue == instrument.venue,
        OhlcvCacheRow.symbol == instrument.symbol,
        OhlcvCacheRow.timeframe == timeframe,
        OhlcvCacheRow.adjusted == adjusted,
    )
    times = sorted((await session.execute(stmt)).scalars())
    if not times:
        return Coverage(0, None, None)
    return Coverage(len(times), times[0], times[-1])


# ------------------------------------------------------------------------- 쓰기
async def write_bars(
    session: AsyncSession,
    instrument: InstrumentRef,
    timeframe: str,
    df: pd.DataFrame,
    *,
    adjusted: bool,
    source_id: str,
) -> WriteReport:
    """봉을 캐시에 넣는다. 이미 있는 봉은 값이 달라졌을 때만 갱신한다.

    호출자가 `session.commit()`을 한다 — 수집 한 종목이 실패했을 때 어디까지
    남길지는 워커가 정할 문제이지 여기가 정할 문제가 아니다.
    """
    report = WriteReport()
    if df is None or df.empty:
        return report

    incoming = _normalize(df)
    if incoming.empty:
        return report

    stmt = select(OhlcvCacheRow).where(
        OhlcvCacheRow.venue == instrument.venue,
        OhlcvCacheRow.symbol == instrument.symbol,
        OhlcvCacheRow.timeframe == timeframe,
        OhlcvCacheRow.adjusted == adjusted,
        OhlcvCacheRow.bar_time >= incoming.index[0].to_pydatetime(),
        OhlcvCacheRow.bar_time <= incoming.index[-1].to_pydatetime(),
    )
    existing = {row.bar_time: row for row in (await session.execute(stmt)).scalars()}
    now = utcnow()

    for ts, values in incoming.iterrows():
        bar_time = ts.to_pydatetime().astimezone(UTC)
        row = existing.get(bar_time)
        if row is None:
            session.add(
                OhlcvCacheRow(
                    venue=instrument.venue,
                    symbol=instrument.symbol,
                    timeframe=timeframe,
                    adjusted=adjusted,
                    bar_time=bar_time,
                    open=float(values["open"]),
                    high=float(values["high"]),
                    low=float(values["low"]),
                    close=float(values["close"]),
                    volume=float(values["volume"]),
                    source_id=source_id,
                    ingested_at=now,
                )
            )
            report.inserted += 1
            continue

        close = float(values["close"])
        if _mismatch(row.close, close) and row.source_id != source_id:
            # 3.8 정합성 검증. 소스가 같은데 값이 바뀐 것은 수정주가 재계산
            # (분할·배당)일 수 있으므로 경고하지 않는다 — 그건 정상 갱신이다.
            report.conflicts.append(
                CloseConflict(
                    instrument=instrument.key,
                    bar_time=bar_time,
                    stored_close=row.close,
                    stored_source=row.source_id,
                    incoming_close=close,
                    incoming_source=source_id,
                )
            )

        if _unchanged(row, values):
            continue
        row.open = float(values["open"])
        row.high = float(values["high"])
        row.low = float(values["low"])
        row.close = close
        row.volume = float(values["volume"])
        row.source_id = source_id
        row.ingested_at = now
        report.updated += 1

    return report


async def record_job(
    session: AsyncSession,
    instrument: InstrumentRef,
    timeframe: str,
    *,
    adjusted: bool,
    success: bool,
    bars: int = 0,
    last_bar_time: datetime | None = None,
    source_id: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    """수집 결과를 `ingestion_jobs`에 남긴다.

    실패해도 행을 남긴다 — **연속 실패 횟수가 소스가 죽었다는 유일한 신호다.**
    캐시에 옛 봉이 남아 있으면 파이프라인은 아무 일 없다는 듯 계속 돈다.
    """
    row = await session.get(
        IngestionJobRow, (instrument.venue, instrument.symbol, timeframe, adjusted)
    )
    if row is None:
        # `mapped_column(default=...)`는 INSERT 시점에만 적용된다. 방금 만든
        # 객체에서 바로 증가시키려면 여기서 채워야 한다.
        row = IngestionJobRow(
            venue=instrument.venue,
            symbol=instrument.symbol,
            timeframe=timeframe,
            adjusted=adjusted,
            bars=0,
            failure_count=0,
        )
        session.add(row)

    if success:
        row.last_success_at = now or utcnow()
        row.last_bar_time = last_bar_time
        row.last_source_id = source_id
        row.bars = bars
        row.failure_count = 0
        row.last_error = None
    else:
        row.failure_count += 1
        row.last_error = error


async def stale_jobs(session: AsyncSession, *, min_failures: int = 1) -> list[IngestionJobRow]:
    """연속 실패 중인 수집 대상. `ingest`가 사람에게 올려 준다."""
    stmt = (
        select(IngestionJobRow)
        .where(IngestionJobRow.failure_count >= min_failures)
        .order_by(IngestionJobRow.failure_count.desc())
    )
    return list((await session.execute(stmt)).scalars())


# -------------------------------------------------------------------------- 내부
def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """저장 직전의 계약 확인. 인덱스는 tz-aware UTC 오름차순, 결측 봉은 버린다."""
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("캐시에 넣을 OHLCV의 index는 tz-aware DatetimeIndex여야 합니다.")
    out = df[list(OHLCV_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    out = out[out["close"].notna()]
    out = out[~out.index.duplicated(keep="last")]
    out.index = out.index.tz_convert("UTC")
    return out.sort_index().fillna(0.0)


def _mismatch(stored: float, incoming: float) -> bool:
    scale = max(abs(stored), abs(incoming), 1e-12)
    return abs(stored - incoming) / scale > CLOSE_MISMATCH_TOLERANCE


def _unchanged(row: OhlcvCacheRow, values: pd.Series) -> bool:
    return all(
        not _mismatch(getattr(row, column), float(values[column])) for column in OHLCV_COLUMNS
    )
