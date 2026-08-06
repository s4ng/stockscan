"""알림 템플릿 렌더링.

`{{ ... }}` 안의 식은 안전 평가기(app.engine.expr)로 계산한다. 별도 템플릿 엔진을
쓰지 않으므로 조건식 노드와 완전히 같은 문법을 공유한다.

    "[{{instrument.venue}}] {{instrument.display_name}} 점수 {{tags.score}}"
"""

from __future__ import annotations

import re
from typing import Any

from app.engine.expr import ExpressionError, evaluate
from app.engine.types import Item

_PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)


def item_env(item: Item) -> dict[str, Any]:
    """식과 템플릿이 참조할 수 있는 변수들."""
    return {
        "instrument": {
            "key": item.instrument.key,
            "venue": item.instrument.venue,
            "symbol": item.instrument.symbol,
            "display_name": item.instrument.display_name or item.instrument.symbol,
            "market": item.instrument.market,
            "quote_currency": item.instrument.quote_currency,
        },
        "features": item.features,
        "tags": item.tags,
        "meta": item.meta,
        "timeframe": item.timeframe,
        "as_of": item.as_of.isoformat(),
        "close": item.last_close,
        "bars": len(item.ohlcv),
    }


def render(template: str, env: dict[str, Any]) -> str:
    """식이 평가되지 않으면 자리표시자를 그대로 남긴다(알림이 통째로 실패하지 않도록)."""

    def substitute(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        try:
            return format_value(evaluate(expression, env))
        except ExpressionError:
            return match.group(0)

    return _PLACEHOLDER.sub(substitute, template)


def render_item(template: str, item: Item) -> str:
    return render(template, item_env(item))


def format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        # 원화처럼 큰 값은 정수로, 작은 값은 소수점 유지
        return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.4g}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)
