# tradeflow — backend

노드 기반 트레이딩 신호 파이프라인의 실행 엔진과 REST API.

```bash
uv sync                 # 의존성 설치
uv run pytest -q        # 테스트
uv run uvicorn app.main:app --reload
```

전체 구조와 설계 근거는 저장소 루트의 `ARCHITECTURE.md`, 작업 규칙은 `CLAUDE.md`를 참고하세요.

## 레이어

| 경로 | 역할 |
| :--- | :--- |
| `app/engine/` | Bundle/Item 계약, RunContext, DAG 검증, 실행 러너 |
| `app/market/` | InstrumentRef, MarketCalendar, 타임프레임 — 시장 차이를 흡수 |
| `app/providers/` | 시세 소스 플러그인 + 라우팅/폴백 |
| `app/nodes/` | 노드 구현체 (트리거·입력·지표·로직·액션) |
| `app/api/` | FastAPI 라우터 |
