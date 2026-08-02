"""타임프레임 정규화 (ARCHITECTURE.md 3.6).

내부 표기는 `1m 5m 15m 30m 1h 4h 1d 1w`로 통일하고, 어댑터가 각 거래소 표기로 변환한다.

**판단 단위는 `1d`와 `1w`뿐이다.** 이유는 데이터가 아니라 비용 구조다 — 하루 왕복
1회면 연 250회고 KRX 기준 연 60% 이상을 비용으로 먼저 낸다. 그리고 그 구간의 상대는
호가창을 보고 있는 참여자다. **못 이기는 게임이라 안 하는 것**이지 분봉 이력을 못
구해서 미루는 것이 아니다.

⚠️ 다만 **정책 계층에서만 막고 타입은 건드리지 않는다.** `Item.timeframe: str`,
`ProviderCapabilities.timeframes`, 캘린더의 분봉 분기는 그대로 둔다.
`Literal["1d"]`로 굳히면 되돌리는 것이 재설계가 되어 버린다 (규칙 12).
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

#: 판단(전략·알림·백테스트)에 쓸 수 있는 타임프레임. 여기가 정책 계층의 단일 출처다.
JUDGEMENT: frozenset[str] = frozenset({"1d", "1w"})

#: v0.4 호환 별칭. v0.4는 "백테스트만 일봉 이상"이었으나 v0.5는 판단 전체가 일봉 이상이다.
BACKTESTABLE: frozenset[str] = JUDGEMENT


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
    """일봉 미만인지. 캘린더·Provider는 이 분기를 계속 갖고 있다 (타입은 안 건드린다)."""
    return duration(timeframe) < timedelta(days=1)


def is_judgeable(timeframe: str) -> bool:
    """이 타임프레임으로 판단해도 되는가. 파이프라인 검증기가 이 함수만 본다."""
    return normalize(timeframe) in JUDGEMENT


def is_backtestable(timeframe: str) -> bool:
    """v0.4 호환 별칭. 판단 가능 여부와 같은 뜻이 되었다."""
    return is_judgeable(timeframe)
