"""심볼 마스터 캐시 (ARCHITECTURE.md 4.7 / 3.9).

`ohlcv_cache`가 봉을 아끼듯 이 표는 **종목 목록 조회**를 아낀다. 매 실행마다
FDR의 미국 목록 6,700행을 다시 받던 것이 여기서 멈춘다.

★ **거래대금은 캐시하지 않는다.** 목록 응답에는 성격이 다른 두 가지가 섞여 있다.

| | 예 | 캐시 |
| :--- | :--- | :--- |
| 마스터 | 심볼 · 이름 · 목록 순서 | ✅ 하루 이틀 낡아도 무해 |
| 거래대금 | `Amount` · `quoteVolume` | ❌ **그날의 유니버스가 바뀐다** |

거래대금을 캐시하면 어제의 상위 60종목을 오늘 훑게 된다. 성능 문제가 아니라
판단이 달라지는 문제라, `top_by_turnover`를 거는 venue는 캐시를 건너뛰고 매번
받는다. 실익이 있는 것은 거래대금이 없는 목록(미국)뿐이고, 그게 정확히 가장
무거운 호출이라 이득의 대부분이 거기 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.market.instrument import InstrumentRef
from app.providers.base import UniverseEntry
from app.storage.models import InstrumentRow, utcnow

#: 마스터를 다시 받기까지의 기본 유효 기간.
#:
#: 상장·폐지는 하루 단위로 일어나므로 그 주기면 충분하다. 짧게 잡으면 캐시가
#: 무의미해지고, 길게 잡으면 신규 상장이 유니버스에 늦게 들어온다.
DEFAULT_TTL = timedelta(days=1)


@dataclass(frozen=True)
class MasterSnapshot:
    entries: list[UniverseEntry]
    source_id: str
    refreshed_at: datetime

    def age(self, now: datetime) -> timedelta:
        return now - self.refreshed_at


async def load(
    session: AsyncSession, venue: str, *, now: datetime, ttl: timedelta = DEFAULT_TTL
) -> MasterSnapshot | None:
    """유효한 마스터가 있으면 돌려준다. 없거나 낡았으면 None.

    ⚠️ `quote_volume_24h`는 항상 `None`이다 — 저장하지 않기 때문이다. 호출자는
    거래대금이 필요하면 캐시를 쓰지 말아야 한다.
    """
    stmt = select(InstrumentRow).where(InstrumentRow.venue == venue).order_by(InstrumentRow.rank)
    rows = list((await session.execute(stmt)).scalars())
    if not rows:
        return None

    refreshed = min(row.refreshed_at for row in rows)
    if now - refreshed > ttl:
        return None

    entries = [
        UniverseEntry(
            instrument=replace(
                InstrumentRef.parse(f"{row.venue}:{row.symbol}"),
                display_name=row.display_name or row.symbol,
            ),
            quote_volume_24h=None,
        )
        for row in rows
    ]
    return MasterSnapshot(entries, rows[0].source_id, refreshed)


async def save(
    session: AsyncSession,
    venue: str,
    entries: list[UniverseEntry],
    *,
    source_id: str,
    now: datetime | None = None,
) -> int:
    """venue의 마스터를 통째로 갈아 끼운다.

    **행을 지우고 다시 넣는다.** 상장폐지된 종목이 남아 있으면 유니버스에 없는
    종목을 계속 훑게 되는데, 목록은 언제나 "지금"의 전량 스냅샷이라 부분 갱신으로는
    사라진 종목을 알 수 없다. (`ohlcv_cache`와 달리 이 표는 자산이 아니라 캐시다 —
    지워도 다시 받으면 그만이고, 규칙 16이 겨누는 대상이 아니다.)
    """
    await session.execute(delete(InstrumentRow).where(InstrumentRow.venue == venue))
    stamp = now or utcnow()

    # ⚠️ **소스 목록에 같은 심볼이 두 번 나온다.** FDR의 나스닥 목록이 그렇다
    # (같은 회사의 다른 표기가 섞인다). 먼저 나온 것을 남긴다 — 목록 순서가 곧
    # 중요도라서(`limit` 컷이 이 순서에 기댄다) 앞엣것이 대표 표기다.
    seen: set[str] = set()
    rank = 0
    for entry in entries:
        symbol = entry.instrument.symbol
        if symbol in seen:
            continue
        seen.add(symbol)
        rank += 1
        session.add(
            InstrumentRow(
                venue=venue,
                symbol=symbol,
                display_name=entry.instrument.display_name or "",
                rank=rank,
                source_id=source_id,
                refreshed_at=stamp,
            )
        )
    return rank
