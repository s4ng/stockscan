"""노드 간 데이터 모델 — Bundle / Item (ARCHITECTURE.md 4.1).

모든 노드가 동일한 봉투(envelope)를 주고받는다. 필터 노드는 ohlcv를 보존한 채
items만 걸러내므로 필터를 몇 개든 이어붙일 수 있다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import pandas as pd

from app.market.instrument import InstrumentRef

#: OHLCV DataFrame이 반드시 가져야 하는 컬럼
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass
class Item:
    """심볼 하나의 상태. 단일 심볼 전략도, 다중 심볼 스크리너도 이 타입의 리스트다."""

    instrument: InstrumentRef
    timeframe: str
    as_of: datetime
    """기준이 되는 **마감된** 캔들의 종료 시각 (UTC). 캘린더가 판정한다."""

    ohlcv: pd.DataFrame
    features: dict[str, Any] = field(default_factory=dict)
    """지표 계산 결과. 예: {"sma_20": 98_400_000, "rsi_14": 71.2}"""

    tags: dict[str, Any] = field(default_factory=dict)
    """노드가 남긴 판단. 예: {"ma_cross": "golden", "ai_score": 8}"""

    meta: dict[str, Any] = field(default_factory=dict)
    """출처·지연·원본 응답 요약 등."""

    @property
    def key(self) -> tuple[str, str]:
        """Bundle 안에서 item을 식별하는 키.

        같은 종목이라도 타임프레임이 다르면 **다른 item**이다. 종목만으로 식별하면
        "일봉 추세 + 시간봉 진입" 같은 멀티 타임프레임 파이프라인에서 Merge가
        한쪽을 소리 없이 덮어쓴다.
        """
        return (self.instrument.key, self.timeframe)

    def with_features(self, **kwargs: Any) -> Item:
        """features를 덧붙인 새 Item. ohlcv는 참조를 그대로 공유한다."""
        return replace(self, features={**self.features, **kwargs})

    def with_tags(self, **kwargs: Any) -> Item:
        return replace(self, tags={**self.tags, **kwargs})

    @property
    def last_close(self) -> float | None:
        if self.ohlcv.empty or "close" not in self.ohlcv:
            return None
        return float(self.ohlcv["close"].iloc[-1])

    def summary(self) -> dict[str, Any]:
        """node_runs에 저장할 요약. DataFrame 원본은 저장하지 않는다."""
        return {
            "instrument": self.instrument.key,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "bars": int(len(self.ohlcv)),
            "last_close": self.last_close,
            "features": _jsonable(self.features),
            "tags": _jsonable(self.tags),
        }


@dataclass
class Bundle:
    """노드 간 전달 단위. 빈 Bundle도 정상 출력이다(실패가 아니다)."""

    items: list[Item] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    """파이프라인 전역 값. 예: 시장 지수, 뉴스 요약, 에러 정보."""

    @property
    def is_empty(self) -> bool:
        return not self.items

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterable[Item]:  # type: ignore[override]
        return iter(self.items)

    def filter(self, predicate: Callable[[Item], bool]) -> Bundle:
        """items만 걸러내고 context는 유지한다."""
        return Bundle([it for it in self.items if predicate(it)], dict(self.context))

    def map(self, fn: Callable[[Item], Item]) -> Bundle:
        return Bundle([fn(it) for it in self.items], dict(self.context))

    def replace_items(self, items: list[Item]) -> Bundle:
        return Bundle(items, dict(self.context))

    @classmethod
    def empty(cls) -> Bundle:
        return cls()

    @classmethod
    def merge(cls, bundles: Iterable[Bundle]) -> Bundle:
        """같은 입력 핸들로 들어온 여러 Bundle을 합친다. `Item.key` 기준 중복 제거."""
        merged: dict[tuple[str, str], Item] = {}
        context: dict[str, Any] = {}
        for b in bundles:
            context.update(b.context)
            for item in b.items:
                merged[item.key] = item
        return cls(list(merged.values()), context)

    def summary(self) -> dict[str, Any]:
        return {
            "count": len(self.items),
            "items": [it.summary() for it in self.items[:20]],
            "truncated": max(0, len(self.items) - 20),
            "context": _jsonable(self.context),
        }


def empty_ohlcv() -> pd.DataFrame:
    """빈 OHLCV 프레임. 컬럼 구조는 유지한다."""
    idx = pd.DatetimeIndex([], tz="UTC", name="time")
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS}, index=idx)


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Provider가 돌려준 프레임이 계약을 지키는지 검증한다."""
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV에 필수 컬럼이 없습니다: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("OHLCV의 index는 DatetimeIndex여야 합니다")
    if df.index.tz is None:
        raise ValueError("OHLCV의 index는 tz-aware(UTC)여야 합니다")
    return df


def _jsonable(value: Any) -> Any:
    """저장·직렬화를 위해 JSON으로 표현 가능한 형태로 낮춘다."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float | int | str | bool) or value is None:
        return value
    return str(value)
