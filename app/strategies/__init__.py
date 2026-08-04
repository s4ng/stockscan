"""전략 프레임워크 — 프로토콜·로더·정적 검사 (ARCHITECTURE.md 4.2 / 4.7).

여기 있는 것은 **프레임워크**이고, `~/.marketscan/`(설정 파일 옆)에 있는 것이
**사용자 전략**이다. 후자는 코드라기보다 데이터에 가깝고, 소스 해시가 실행 이력에
박힌다.
"""

from app.strategies.base import Strategy, StrategyError, top_n, top_pct
from app.strategies.registry import (
    LoadedStrategy,
    StrategyNotFoundError,
    StrategySource,
    discover,
    load_strategy,
)

__all__ = [
    "LoadedStrategy",
    "Strategy",
    "StrategyError",
    "StrategyNotFoundError",
    "StrategySource",
    "discover",
    "load_strategy",
    "top_n",
    "top_pct",
]
