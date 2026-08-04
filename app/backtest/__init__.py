"""백테스트 — 과거 날짜를 하루씩 되감아 전략을 돌린다 (ARCHITECTURE.md 12.7).

**용도는 하나다: "내 구현이 안 틀렸나."** "이 전략이 돈이 되나"가 아니다.
지표 조합을 백테스트로 뒤져 좋은 것을 고르지 않는다 — 탐색 공간은 수백만인데
일봉 10년은 2,500행이라 우연히 맞는 조합이 반드시 나온다 (4.8).
"""

from app.backtest.replay import ReplayDay, ReplayResult, replay

__all__ = ["ReplayDay", "ReplayResult", "replay"]
