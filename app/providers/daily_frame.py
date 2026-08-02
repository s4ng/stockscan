"""날짜 인덱스 일봉 → 마감 시각 인덱스 OHLCV.

주식 소스는 하나같이 **날짜만** 준다 (pykrx는 naive 날짜, yfinance는 현지 자정,
FDR은 naive 날짜). 이 저장소의 인덱스 규약은 **봉의 마감 시각**이므로(규칙 15)
어댑터마다 변환이 필요하고, 그 변환이 세 곳에 흩어지면 언젠가 하나만 어긋난다.
어긋나면 `closed_only`가 진행 중인 봉을 통과시키거나 `as_of`와 인덱스가 맞지 않아
전량 제외된다. 그래서 한 곳에 둔다.

마감 시각의 출처는 **캘린더**다. `15:30 KST` · `16:00 ET`를 상수로 박으면
조기폐장 날 조용히 어긋난다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import pandas as pd

from app.engine.types import OHLCV_COLUMNS
from app.market.calendar import ExchangeSessionCalendar


def empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="time")
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS}, index=idx)


def daily_frame(
    raw: pd.DataFrame | None,
    columns: Mapping[str, str],
    calendar: ExchangeSessionCalendar,
    *,
    source: str,
) -> pd.DataFrame:
    """소스 프레임을 계약에 맞는 OHLCV로 옮긴다.

    캘린더가 휴장이라고 보는 날짜에 소스가 봉을 준 경우 **그 봉을 버리지 않는다** —
    어느 쪽이 틀렸든 가격은 실재했고, 조용히 지우면 지표에 구멍이 뚫린다.
    정규 폐장 시각으로 채우고 넘어간다.
    """
    if raw is None or raw.empty:
        return empty_frame()

    frame = raw.rename(columns=dict(columns))
    missing = [c for c in OHLCV_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"{source} 응답에 필요한 컬럼이 없습니다: {missing}. "
            f"받은 컬럼: {list(raw.columns)}. 소스 라이브러리 버전이 바뀌었을 수 있습니다."
        )

    out = frame[list(OHLCV_COLUMNS)].apply(pd.to_numeric, errors="coerce").astype("float64")
    out.index = pd.DatetimeIndex(
        [_close_of(ts, calendar) for ts in frame.index], name="time"
    )
    out = out[out.index.notna()]
    # 같은 날짜가 두 번 오면(소스 중복) 뒤엣것을 남긴다.
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def _close_of(ts: pd.Timestamp | datetime, calendar: ExchangeSessionCalendar) -> pd.Timestamp:
    day = pd.Timestamp(ts).date()
    close = calendar.session_close(day)
    if close is not None:
        return pd.Timestamp(close)
    return calendar.regular_close(day)
