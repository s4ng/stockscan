"""노드가 종목 목록을 얻는 창구 (ARCHITECTURE.md 3.9 / 4.7).

`ohlcv_source`가 봉에 대해 하는 일을 목록에 대해 한다 — 노드는 `ctx.universe`만
부르고 뒤에 캐시가 있는지 모른다.

★ **캐시를 쓸 수 있는지는 "거래대금이 필요한가"가 정한다.**

목록 응답에는 성격이 다른 두 가지가 섞여 있다. 마스터(심볼·이름·순서)는 하루
이틀 낡아도 무해하지만 **거래대금은 캐시하는 순간 그날의 유니버스가 바뀐다** —
어제의 상위 60종목을 오늘 훑게 된다. 성능 문제가 아니라 판단이 달라지는 문제다.

그래서 `top_by_turnover`를 거는 venue는 캐시를 건너뛴다. 실익이 있는 것은
거래대금이 없는 목록인데, 그게 정확히 가장 무거운 호출(FDR 미국 6,700행)이라
이득의 대부분이 거기 있다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base import UniverseEntry
from app.providers.registry import AUTO, ProviderRegistry

log = logging.getLogger(__name__)


@dataclass
class UniverseResult:
    entries: list[UniverseEntry]
    source_id: str
    from_cache: bool = False
    notes: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)
    """캐시 쓰기 실패 등. **info와 나눠 두는 이유**는 이것이 조용히 지나가면
    "그냥 좀 느리네"로 보이기 때문이다 — 실제로는 캐시가 영영 안 채워지고 있다."""


class UniverseSource(Protocol):
    async def list_instruments(
        self, venue: str, *, source: str = AUTO, needs_turnover: bool = False
    ) -> UniverseResult: ...


@dataclass
class DirectUniverse:
    """매번 소스에 묻는다. DB가 없는 실행의 기본값이다."""

    registry: ProviderRegistry

    async def list_instruments(
        self, venue: str, *, source: str = AUTO, needs_turnover: bool = False
    ) -> UniverseResult:
        entries, source_id = await self.registry.list_instruments(venue, source=source)
        return UniverseResult(entries, source_id)


@dataclass
class CachedUniverse:
    """마스터를 `instruments`에 캐시한다. **거래대금이 필요하면 건너뛴다.**"""

    registry: ProviderRegistry
    sessionmaker: async_sessionmaker[AsyncSession]
    now: datetime
    writable: bool = True
    ttl: timedelta | None = None
    _degraded: bool = False

    async def list_instruments(
        self, venue: str, *, source: str = AUTO, needs_turnover: bool = False
    ) -> UniverseResult:
        from app.storage import instruments as master

        ttl = self.ttl if self.ttl is not None else master.DEFAULT_TTL
        notes: list[str] = []

        if needs_turnover:
            # 거래대금은 캐시하지 않는다. 캐시된 마스터를 쓰면 유동성 컷이
            # 어제 값으로 걸린다 — 그날의 후보 집합이 통째로 달라진다.
            entries, source_id = await self.registry.list_instruments(venue, source=source)
            return UniverseResult(entries, source_id)

        if not self._degraded:
            try:
                async with self.sessionmaker() as session:
                    snapshot = await master.load(session, venue, now=self.now, ttl=ttl)
            except SQLAlchemyError as exc:
                self._degraded = True
                log.warning("instruments 캐시를 읽지 못했습니다 (%s). 소스로 돕니다.", exc)
                snapshot = None
            if snapshot is not None:
                age = snapshot.age(self.now)
                return UniverseResult(
                    snapshot.entries,
                    snapshot.source_id,
                    from_cache=True,
                    notes=[
                        f"{venue}: 심볼 마스터 {len(snapshot.entries)}종목을 캐시에서 "
                        f"읽었습니다 (갱신 {int(age.total_seconds() // 3600)}시간 전)"
                    ],
                )

        entries, source_id = await self.registry.list_instruments(venue, source=source)
        warnings: list[str] = []
        if self.writable and entries:
            warnings.extend(await self._save(venue, entries, source_id))
        return UniverseResult(entries, source_id, notes=notes, warnings=warnings)

    async def _save(
        self, venue: str, entries: list[UniverseEntry], source_id: str
    ) -> list[str]:
        from app.storage import instruments as master

        try:
            async with self.sessionmaker() as session:
                await master.save(session, venue, entries, source_id=source_id, now=self.now)
                await session.commit()
        except SQLAlchemyError as exc:  # noqa: BLE001 - 캐시 실패가 실행을 막으면 안 된다
            log.warning("instruments 캐시 쓰기 실패 (%s): %s", venue, exc)
            return [f"{venue}: 심볼 마스터를 캐시에 쓰지 못했습니다 ({exc})."]
        return []
