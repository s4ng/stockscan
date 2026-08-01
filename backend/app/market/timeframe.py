"""타임프레임 정규화 (ARCHITECTURE.md 3.6).

내부 표기는 `1m 5m 15m 30m 1h 4h 1d 1w`로 통일하고, 어댑터가 각 거래소 표기로 변환한다.
백테스트는 일봉 이상만 허용한다 (4.8의 커버리지 게이트).
"""

from __future__ import annotations

from datetime import timedelta

#: 지원 타임프레임 → 길이
TIMEFRAMES: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}

#: 백테스트가 허용하는 타임프레임. 분봉은 과거 이력 확보 비용이 커서 제외한다.
BACKTESTABLE: frozenset[str] = frozenset({"1d", "1w"})


class UnknownTimeframeError(ValueError):
    pass


def normalize(raw: str) -> str:
    """대소문자·공백을 정리하고 지원 여부를 검증한다."""
    tf = raw.strip().lower()
    if tf not in TIMEFRAMES:
        raise UnknownTimeframeError(
            f"지원하지 않는 타임프레임: {raw!r}. 지원 목록: {', '.join(TIMEFRAMES)}"
        )
    return tf


def duration(timeframe: str) -> timedelta:
    return TIMEFRAMES[normalize(timeframe)]


def is_intraday(timeframe: str) -> bool:
    """일봉 미만인지. 실매매는 허용, 백테스트는 커버리지 게이트를 통과해야 한다."""
    return duration(timeframe) < timedelta(days=1)


def is_backtestable(timeframe: str) -> bool:
    return normalize(timeframe) in BACKTESTABLE
