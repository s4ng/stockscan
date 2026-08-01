# 프로젝트 핸드오프 문서 (Handoff Document)

## 1. 프로젝트 개요 (Project Overview)
본 프로젝트는 **노드 기반 비주얼 파이프라인(Flow-based Visual Pipeline)**을 활용한 **주식 및 암호화폐 자동매매 / 신호 알림 시스템**입니다. 
n8n, ComfyUI, Node-RED와 유사한 인터페이스를 통해 사용자가 코딩 없이 노드(Market Data, TA Filter, AI Filter, Action 등)를 드래그 앤 드롭으로 조합하고 전략 파이프라인을 구축할 수 있도록 지원합니다.

### 핵심 목표
* **전략 유연성 확보**: 복잡한 트레이딩 전략을 시각적 그래프(DAG) 형태로 구현 및 확장
* **AI & 퀀트 융합**: 단순 기술적 지표(이평선, 볼린저밴드 등) 필터링에 LLM 기반 AI 분석 필터를 손쉽게 결합
* **멀티 채널 액션**: 최종 필터링된 시그널을 매매 API 연동(주문 실행) 또는 텔레그램/슬랙 알림으로 자동 전송

---

## 2. 시스템 아키텍처 및 기술 스택 (Architecture & Tech Stack)

```text
[ React Flow (UI) ] ── (DAG JSON) ──> [ FastAPI (Backend Engine) ]
                                              │
                      ┌───────────────────────┼───────────────────────┐
                      ▼                       ▼                       ▼
            [ Market Data API ]     [ TA Engine (Pandas-TA) ]   [ LLM API (OpenAI) ]
                      │                       │                       │
                      └───────────────────────┼───────────────────────┘
                                              ▼
                                    [ Action Exec / Alert ]
                                 (Broker API / Telegram Bot)
```

### 추천 기술 스택 (Tech Stack)
* **Frontend**: React, React Flow, TailwindCSS, Zustand / Redux Toolkit
* **Backend**: Python 3.11+, FastAPI, NetworkX (DAG 실행 엔진), Pydantic
* **Data & Analytics**: Pandas, Pandas-TA, CCXT (코인 API), PyKRX (국내 주식)
* **Database**: SQLite

---

## 3. 핵심 노드 사양 (Node Specifications)

| 노드 범주 (Category) | 노드 명칭 (Node Name) | 역할 및 사양 (Description) | 주요 Input / Output |
| :--- | :--- | :--- | :--- |
| **Trigger / Input** | **Schedule Trigger** | 지정된 주기(분/시/일)로 파이프라인 트리거 실행 | Out: Cron Event |
| | **Market Data Fetcher** | 거래소 API(업비트, 바이낸스, 한투 등) OHLCV 캔들 수집 | In: Symbol / Out: DataFrame |
| **Indicator Filter** | **MA Filter** | 이동평균선 크로스(Golden/Dead) 및 위치 조건 필터링 | In: DataFrame / Out: Filtered List |
| | **Bollinger Filter** | 볼린저 밴드 상/하한선 돌파 및 이탈 조건 필터링 | In: DataFrame / Out: Filtered List |
| | **RSI / MACD Filter** | 과매수/과매도 구간 및 다이버전스 조건 검사 | In: DataFrame / Out: Filtered List |
| **AI / Logic** | **LLM Decision Node** | 수집된 차트/뉴스 데이터를 LLM에 전달하여 매수 타당성 평가 | In: Filtered List / Out: Scored List |
| | **Condition Splitter** | 점수/조건에 따라 True/False 분기 처리 | In: Any / Out: Branch A, Branch B |
| **Action / Output** | **Broker API Execution** | 거래소 API를 통한 실제 주문 실행 (지정가/시장가) | In: Target Symbol List |
| | **Telegram Alert** | 텔레그램 봇 API를 이용해 지정된 채널로 신호 전송 | In: Message Data / Target Chat ID |

---

## 4. 데이터 스키마 (Pipeline DAG JSON Schema)

프론트엔드에서 노드를 배치하고 연결하면 백엔드로 다음과 같은 표준화된 **DAG(Directed Acyclic Graph) JSON**이 전달됩니다.

```json
{
  "pipeline_id": "pipe_trading_strategy_v1",
  "name": "변동성 돌파 + 이평선 + AI 검증 알림 파이프라인",
  "nodes": [
    {
      "id": "node_1",
      "type": "marketData",
      "data": { "market": "KRW-BTC", "timeframe": "1h", "limit": 200 }
    },
    {
      "id": "node_2",
      "type": "taFilter",
      "data": { "indicator": "SMA", "period": 20, "condition": "cross_above" }
    },
    {
      "id": "node_3",
      "type": "aiFilter",
      "data": { 
        "model": "gpt-4o", 
        "prompt": "현재 캔들 패턴과 지표 상태를 보고 매수 추천 점수를 1-10점으로 산출해줘." 
      }
    },
    {
      "id": "node_4",
      "type": "actionTelegram",
      "data": { "chat_id": "@crypto_signal_channel", "template": "[알림] {{symbol}} AI 매수 추천 점수: {{score}}" }
    }
  ],
  "edges": [
    { "id": "e1-2", "source": "node_1", "target": "node_2" },
    { "id": "e2-3", "source": "node_2", "target": "node_3" },
    { "id": "e3-4", "source": "node_3", "target": "node_4" }
  ]
}
```

---

## 5. 단계별 구현 로드맵 (Implementation Roadmap)

1. **Phase 1: PoC & Core Engine (1~2주)**
   * React Flow 기본 캔버스 구축 및 노드 드래그 앤 드롭 구현
   * 백엔드 NetworkX 기반의 단순 파이프라인 실행 엔진(Runner) 작성
   * 시세 수집 ➔ 이평선 필터 ➔ 텔레그램 알림 파이프라인 동작 검증
2. **Phase 2: AI Node & Complex Filters (2~3주)**
   * OpenAI/Anthropic API 연동 AI Decision 노드 개발
   * 비동기 태스크 큐(Celery/Redis) 도입을 통한 LLM API 호출 병목 방지
   * 에러 핸들링 및 재시도 노드 구상
3. **Phase 3: Backtesting Integration & Execution (3~4주)**
   * 백테스팅 모드 지원 (과거 시계열 데이터를 노드 파이프라인에 대입 연산)
   * 거래소 API(CCXT, 한국투자증권 OpenAPI) 매수/매도 주문 실행 노드 안정화
   * 모의투자를 통한 파이프라인 실전 검증

---

## 6. 개발 및 운영 시 주요 주의사항 (Key Considerations)

* **비동기 처리 필수**: LLM API 연동 및 외부 거래소 API 호출은 응답 지연(Latency)이 발생하므로 메인 루프를 블로킹하지 않도록 비동기(AsyncIO / Worker Task)로 설계해야 합니다.
* **에러 분기 지원**: 외부 API 타임아웃이나 거래소 장애 시 파이프라인 전체가 중단되지 않도록 노드별 `On Error` 엣지 연결 및 기본값 설정을 지원해야 합니다.
* **백테스팅 모듈 연동**: 각 노드의 로직을 **입출력이 명확한 순수 함수(Pure Function)**로 작성해야 백테스트 연산 시 실시간 데이터와 동등한 결과를 얻을 수 있습니다.
