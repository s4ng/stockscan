"""노드 패키지.

여기서 각 노드 모듈을 임포트해야 `@register` 데코레이터가 실행되어 레지스트리에
등록된다. **새 노드를 추가하면 이 목록에도 추가해야 한다.**

TODO(Phase 1): 모듈 자동 탐색(pkgutil.walk_packages)으로 바꿔 누락을 방지한다.
"""

from app.nodes.actions import log_alert, persist_signal  # noqa: F401
from app.nodes.indicators import ma_filter  # noqa: F401  — 동결. 신규 추가 금지 (5장)
from app.nodes.inputs import market_data, symbol_universe  # noqa: F401
from app.nodes.logic import condition_splitter  # noqa: F401
from app.nodes.registry import catalog, get_node_class, register
from app.nodes.strategy import runner as strategy_runner  # noqa: F401
from app.nodes.triggers import manual  # noqa: F401

__all__ = ["catalog", "get_node_class", "register"]
