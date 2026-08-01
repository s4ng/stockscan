# ARCHITECTURE.md

노드 기반 비주얼 파이프라인 트레이딩 시스템 아키텍처 설계서

> 상태: **v0.3 (draft)** — `docs/trading_bot_node_handoff.md`의 아이디어를 구현 가능한 수준으로 구체화한 문서입니다.
> ⚠️ 표시는 **미결정 또는 외부 확인이 필요한 항목**입니다.

**확정된 전제**

| 항목 | 결정 |
| :--- | :--- |
| 배포 형태 | **개인용 / Self-hosted**. 서비스화·멀티유저 없음 |
| 대상 시장 | **암호화폐 + 한국 주식 + 미국 주식** (Toss 증권 우선, 증권사 교체 가능하도록 인터페이스화) |
| 1차 범위 | **신호 알림 전용**. 실주문은 검증 후 별도 단계 |
| 봉 단위 | **백테스트는 일봉 이상만**. 실매매(알림)는 분봉 허용 |
| 데이터 소스 | **사용자가 Connection으로 직접 구성**. 소스는 플러그인, 파이프라인은 소스에 비종속 |

---

## 1. 개요

### 1.1 목적

React Flow 캔버스에서 노드를 조합해 **암호화폐·한국주식·미국주식 전략을 시각적으로 구성**하고, 백엔드 DAG 엔진이 이를 실행해 **신호 알림**(향후 실주문)으로 연결하는 개인용 Self-hosted 시스템.

### 1.2 설계 원칙

| 원칙 | 의미 |
| :--- | :--- |
| **시장 중립 코어 (Market-Neutral Core)** | 엔진·노드는 "코인/주식"을 모르며, 거래소별 차이는 Provider와 Calendar 뒤로 숨긴다. 새 증권사 추가 = 어댑터 1개 작성 |
| **결정성 (Determinism)** | 같은 입력 + 같은 시각 → 같은 출력. 백테스트와 실행이 동일 코드 경로를 쓴다 |
| **시간 주입 (Injected Clock)** | 노드는 `datetime.now()`를 직접 호출하지 않는다. 모든 시각은 실행 컨텍스트가 제공한다 |
| **격리된 실패 (Isolated Failure)** | 노드 하나의 실패가 파이프라인 전체를 중단시키지 않는다 |
| **기본은 안전 (Safe by Default)** | 실주문은 명시적으로 켜야만 동작한다. 기본 모드는 `notify` |
| **관측 가능성 (Observability)** | 모든 실행의 노드별 입/출력이 저장되어 사후 재현이 가능하다 |

### 1.3 비목표 (Non-goals)

- 초저지연(HFT) — 분/시간/일 봉 기준 전략을 대상으로 한다.
- 멀티테넌시·과금·회원 관리 — Self-hosted 단일 사용자 전제.
- 범용 워크플로 엔진 — n8n의 일반 자동화 기능을 목표로 하지 않는다.
- 실시간 호가/체결 스트리밍 — 초기 범위는 봉(OHLCV) 기반.

---

## 2. 시스템 구성

```text
┌──────────────────────────────────────────────────────────────┐
│  Frontend (React + React Flow + Zustand)                      │
│  캔버스 편집 · 노드 파라미터 폼 · 실행 이력 뷰어 · 백테스트 리포트  │
└───────────────┬──────────────────────────────▲───────────────┘
                │ REST                          │ SSE (실행 진행)
┌───────────────▼──────────────────────────────┴───────────────┐
│  FastAPI  ·  파이프라인 CRUD/버전 · 실행 트리거 · 이력 조회       │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│  Scheduler (APScheduler)                                      │
│  Schedule Trigger → 크론 등록. 시장 캘린더 인식(장중/마감 후)     │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│  DAG Execution Engine                                         │
│  NetworkX 위상정렬 → 레벨별 asyncio 병렬 실행                   │
│  RunContext 주입 (clock · mode · credentials · cache · log)    │
└──┬──────────┬──────────┬──────────┬──────────────────────────┘
   │          │          │          │
   ▼          ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐
│Indicator│ │AI/LLM  │ │Logic   │ │Action        │
│Nodes    │ │Adapter │ │분기/병합│ │Telegram/Slack│
│pandas-ta│ │+캐시   │ │쿨다운  │ │(→ Broker P5) │
└────┬────┘ └────────┘ └────────┘ └──────────────┘
     │
┌────▼─────────────────────────────────────────────────────────┐
│  Market Abstraction Layer          ★ 멀티 마켓의 핵심          │
│  ┌──────────────┐ ┌────────────┐ ┌────────┐ ┌─────────────┐  │
│  │InstrumentRef │ │MarketCalendar│ │Routing │ │Connections  │  │
│  │venue:symbol  │ │24x7/KRX/US │ │Table   │ │(사용자 API키)│  │
│  └──────────────┘ └────────────┘ └────────┘ └─────────────┘  │
└────┬─────────────────────────────────────────────▲───────────┘
     │ 읽기 전용                                    │ 등록/테스트
┌────▼──────────────┐                              │
│  ohlcv_cache      │◀── 주기 수집 ──┐              │
│  (데이터 자산)      │               │              │
└───────────────────┘   ┌───────────┴────────────┐ │
                        │  Ingestion Worker      │ │
                        │  레이트리밋·폴백·재시도  │ │
                        └───┬────────────────────┘ │
    ┌───────┬───────┬───────┼───────┬───────┬──────┴──┐
    ▼       ▼       ▼       ▼       ▼       ▼         ▼
  Upbit  Binance  Toss⚠️   KIS    PyKRX  yfinance  Alpaca
 (CCXT)  (CCXT)  (한/미)  (분봉)  (일봉)  (일봉)   (미국)
                                                          
┌───────────────────────────────────────────────────────────────┐
│  Storage: SQLite(WAL)                                         │
│  pipelines · pipeline_versions · runs · node_runs ·           │
│  signals · alerts_sent · instruments · market_calendar ·      │
│  connections(암호화) · source_routes · llm_cache · ohlcv_cache │
│  [Phase 5] orders · positions                                 │
└───────────────────────────────────────────────────────────────┘
```

### 2.1 기술 스택

| 레이어 | 선택 | 비고 |
| :--- | :--- | :--- |
| Frontend | React 19, `@xyflow/react` 12, TailwindCSS 4, **Zustand** 5 | React Flow 12부터 패키지명이 `@xyflow/react`. 상태관리는 공식 권장인 Zustand |
| TypeScript | **7.x (Go 네이티브 컴파일러)** | ⚠️ `typescript-eslint`가 아직 TS 7을 지원하지 않아 ESLint는 보류. 정적 검사는 `tsc --noEmit` |
| Node.js | **22 LTS 이상** | pnpm 11이 `>=22.13`, Vite 8이 `>=22.12`를 요구 |
| Frontend 검증 | Zod (백엔드 Pydantic → JSON Schema → 폼 자동 생성) | 노드 추가 시 프론트 수정 최소화 |
| Backend | Python 3.11+, FastAPI, Pydantic v2 | |
| DAG | NetworkX (위상정렬·사이클 검출) + 자체 asyncio 러너 | |
| 지표 | ⚠️ `pandas-ta-classic` 또는 TA-Lib | 원본 `pandas-ta`(twopirllc)는 사실상 유지보수 중단 — 확정 필요 |
| 시세 (일봉) | PyKRX(한국), yfinance(미국), FinanceDataReader(종목 마스터) | 무인증. 백테스트 이력용 |
| 시세 (분봉) | 한국투자증권 OpenAPI(한/미), Alpaca(미국) | 인증 필요. 실매매 신호용 |
| 거래 (Phase 5) | CCXT(코인), Toss⚠️ / KIS(주식) | 3.3 Provider 참조 |
| 거래 캘린더 | `exchange_calendars` 또는 `pandas_market_calendars` | KRX·NYSE·NASDAQ 휴장일/조기폐장 내장 |
| 스케줄 | APScheduler (AsyncIOScheduler) | Celery는 필요해질 때만 |
| DB | SQLite (WAL) | 단일 사용자 전제. SQLAlchemy로 이식성 확보 |
| LLM | Anthropic Claude / OpenAI (Provider 추상화) | 프롬프트·응답 전량 캐시 |
| 배포 | Docker Compose (backend + frontend + volume) | 8절 참조 |

---

## 3. 멀티 마켓 추상화 ★

**이 시스템의 난이도는 대부분 여기에 있습니다.** 코인과 주식을 한 캔버스에서 다루려면 심볼 표기, 거래 시간, 캔들 마감, 통화가 전부 달라지기 때문입니다. 아래 4개 개념으로 흡수합니다.

### 3.1 InstrumentRef — 통일 심볼 식별자

거래소마다 표기가 다르고(`KRW-BTC` / `BTC/USDT` / `005930` / `AAPL`), 티커가 시장 간 충돌할 수 있습니다. 항상 **venue를 붙인 정규 문자열**로 다룹니다.

```python
@dataclass(frozen=True)
class InstrumentRef:
    venue: str              # "upbit" | "binance" | "krx" | "nasdaq" | "nyse"
    symbol: str             # "KRW-BTC" | "BTC/USDT" | "005930" | "AAPL"
    asset_class: str        # "crypto" | "equity"
    quote_currency: str     # "KRW" | "USDT" | "USD"
    display_name: str       # "비트코인" | "삼성전자" | "Apple Inc."

    @property
    def key(self) -> str:   # "upbit:KRW-BTC", "krx:005930", "nasdaq:AAPL"
        return f"{self.venue}:{self.symbol}"
```

- UI에서는 `display_name`으로 검색하고 내부는 `key`로 다룬다.
- `instruments` 테이블에 심볼 마스터를 캐시해 자동완성을 제공한다 (주식은 종목 수가 많음).

### 3.2 MarketCalendar — 거래 시간

```python
class MarketCalendar(Protocol):
    tz: ZoneInfo
    def is_open(self, t: datetime) -> bool: ...
    def last_closed_bar(self, t: datetime, timeframe: str) -> datetime | None: ...
    def next_bar_close(self, t: datetime, timeframe: str) -> datetime: ...
    def is_trading_day(self, d: date) -> bool: ...
```

| 구현 | 대상 | 특징 |
| :--- | :--- | :--- |
| `Crypto24x7Calendar` | 코인 | 항상 열림. 일봉 마감 기준(UTC 00:00 vs KST 09:00)만 정하면 됨 |
| `KrxCalendar` | 한국 주식 | 09:00–15:30 KST, 휴장일, 임시 휴장, 동시호가 구간 |
| `UsEquityCalendar` | 미국 주식 | 09:30–16:00 **America/New_York**, 서머타임 자동, 조기폐장(추수감사절 등), 프리/애프터 제외 |

⚠️ 미국 시장은 **고정 오프셋(UTC-5)으로 계산하면 안 됩니다.** 서머타임 때문에 한국 기준 개장 시각이 22:30 ↔ 23:30으로 바뀝니다. 반드시 `ZoneInfo("America/New_York")`를 씁니다.

### 3.3 Provider — 플러그인 구조

원본 문서는 "거래소 API"를 하나로 묶었지만, 실제로는 **시세만 주는 소스(PyKRX, yfinance)**와 **주문까지 되는 소스(Toss, KIS, Upbit)**가 섞입니다. 인터페이스를 분리하고, **각 Provider가 자기 인증 스키마를 스스로 선언**하게 해서 새 소스 추가를 파일 1개로 끝냅니다.

```python
class MarketDataProvider(Protocol):
    id: str                                     # "alpaca"
    display_name: str                           # "Alpaca Markets"
    venues: list[str]                           # ["nasdaq", "nyse"]
    credential_schema: type[BaseModel] | None   # None이면 무인증 내장 소스
    capabilities: ProviderCapabilities

    def calendar_for(self, inst: InstrumentRef) -> MarketCalendar: ...
    async def list_instruments(self, venue: str) -> list[InstrumentRef]: ...
    async def fetch_ohlcv(
        self, inst: InstrumentRef, timeframe: str,
        end: datetime, limit: int,              # end 이후 데이터는 절대 반환 금지
    ) -> pd.DataFrame: ...
    async def health_check(self) -> HealthStatus: ...   # 연결 테스트 버튼용

class BrokerProvider(Protocol):                 # Phase 5에서 활성화
    async def get_balances(self) -> ...
    async def get_positions(self) -> ...
    async def place_order(self, req: OrderRequest) -> OrderResult: ...
    async def cancel_order(self, order_id: str) -> None: ...
```

```python
@dataclass
class ProviderCapabilities:
    timeframes: list[str]           # ["1m","5m","15m","1h","1d"]
    max_lookback: dict[str, int]    # timeframe별 과거 조회 한계 {"1m": 7일치, "1d": 무제한}
    adjusted: Literal["always", "optional", "never"]   # 수정주가 지원 (3.8 참조)
    supports_orders: bool
    supports_fractional: bool       # 미국주식 소수점 매매
    rate_limit: RateLimitSpec
```

**`credential_schema`가 UI 폼을 만듭니다.** Provider가 아래처럼 선언하면 Connections 화면에 폼이 자동 생성되므로, 새 소스를 추가할 때 프론트엔드를 건드릴 필요가 없습니다.

```python
class AlpacaCredentials(BaseModel):
    api_key:    str       = Field(..., title="API Key")
    api_secret: SecretStr = Field(..., title="API Secret")
    feed:       Literal["iex", "sip"] = Field("iex", title="데이터 피드")
```

**어댑터 목록**

| 어댑터 | venue | 시세 | 주문 | 인증 | 비고 |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `UpbitProvider` | upbit | ✅ | ✅ | 키 | CCXT |
| `BinanceProvider` | binance | ✅ | ✅ | 키 | CCXT |
| `TossProvider` | krx, nasdaq, nyse | ⚠️ | ⚠️ | ⚠️ | **1차 타깃.** 아래 주의 참조 |
| `KisProvider` | krx, nasdaq, nyse | ✅ | ✅ | 키+토큰 | 국내·해외 **분봉** 주력. Toss 대체 경로 |
| `PykrxProvider` | krx | ✅ | ❌ | 없음 | 국내 **일봉**·수급·펀더멘털. 분봉 없음 |
| `YFinanceProvider` | nasdaq, nyse, krx | ✅ | ❌ | 없음 | 미국 **일봉** 이력. 비공식 |
| `AlpacaProvider` | nasdaq, nyse | ✅ | (P5) | 키 | 미국 공식. 무료 티어 IEX |
| `FdrProvider` | krx, nasdaq, nyse | ✅ | ❌ | 없음 | 종목 마스터 부트스트랩용 |

> ⚠️ **Toss 증권 API 확인 필요 항목**: 공개 오픈 API 제공 여부와 스펙(인증 방식, 시세 제공 범위, 과거 봉 조회 가능 여부, 레이트 리밋, 미국주식 지원 범위, 개인 개발자 신청 절차)을 확인하지 못했습니다. 어댑터 인터페이스는 시장 중립적이므로 스펙 확인 후 `TossProvider`만 채우면 됩니다. **과거 봉 조회가 제한적이면 시세는 PyKRX/KIS/Alpaca로, 주문만 Toss로 나누는 구성**이 가능하도록 인터페이스를 분리해 두었습니다.

### 3.4 Connection과 라우팅 — 소스는 노드가 아니다

**설계 결정**: 데이터 소스를 캔버스의 노드로 만들지 **않습니다.** 사용자가 API 키를 직접 구성하는 통제권은 유지하되, 아래 이유로 소스를 파이프라인 바깥으로 뺍니다.

| 소스를 노드로 만들면 | 결과 |
| :--- | :--- |
| 파이프라인이 소스에 종속 | yfinance→Alpaca 교체 시 캔버스를 뜯어고쳐야 함 |
| 멀티마켓 혼합이 깨짐 | 코인+한국+미국에 소스 노드 3개+Merge를 매번 배치. Fresh Bar Gate(3.5) 무력화 |
| 폴백 불가 | 소스 하나가 죽으면 파이프라인 실패 |
| 캐시 공유 어려움 | 파이프라인마다 같은 종목을 중복 호출 |

**대신 3계층으로 나눕니다.**

**① Connections** — 사용자가 소스별 API 키를 등록하는 설정 화면. `credential_schema`로 폼이 자동 생성되고, **[연결 테스트]** 버튼이 `health_check()`를 호출해 즉시 검증합니다. 무인증 소스(PyKRX, yfinance, FDR)는 등록 없이 항상 활성화됩니다.

```
conn_kis_main      → KisProvider     [테스트 ✅]
conn_alpaca_paper  → AlpacaProvider  [테스트 ✅]
conn_upbit_ro      → UpbitProvider   [테스트 ✅]
```

**② Routing Table** — `venue × timeframe` 조합별 소스 **우선순위**. 앞 소스가 실패하면 다음으로 폴백합니다. 시스템이 기본값을 제안하고 사용자가 편집합니다.

```
upbit  · *   → [conn_upbit_ro]
krx    · 1d  → [pykrx, conn_kis_main]
krx    · 5m  → [conn_kis_main]
nasdaq · 1d  → [conn_alpaca_paper, yfinance]
nasdaq · 5m  → [conn_alpaca_paper]
```

**③ 노드의 `source` 파라미터** — 기본 `"auto"`(라우팅 표 사용). 특정 Connection ID를 지정하면 그 소스만 사용합니다. 노드화로 얻으려던 명시적 통제권은 이 파라미터 하나로 확보됩니다.

```json
{ "type": "marketData",
  "params": { "instruments": [...], "timeframe": "1d", "source": "auto" } }
```

이 구조에서 **새 증권사 추가 = Provider 파일 1개 작성 + Connections에서 키 등록**이며, 기존 파이프라인은 수정할 필요가 없습니다.

### 3.5 혼합 파이프라인 사용성 — Fresh Bar Gate

**코인과 주식을 한 파이프라인에 넣었을 때 가장 흔한 문제**: 미국장이 닫혀 있는 시간에 파이프라인이 돌면 주식 쪽은 어제와 똑같은 캔들을 다시 읽고, 같은 신호를 매번 재발생시킵니다.

해결: **item 단위로 "새로 마감된 캔들이 있는지" 판정**하고, 없으면 조용히 제외합니다.

```
Market Data Fetcher
  ├─ item별로 calendar.last_closed_bar(ctx.now, timeframe) 계산
  ├─ 직전 실행의 as_of와 같으면 → stale
  └─ skip_stale: true (기본값) → stale item 제외
```

이 규칙 덕분에 하나의 파이프라인에 `upbit:KRW-BTC`, `krx:005930`, `nasdaq:AAPL`을 함께 넣고 **매시간 실행**해도:

- 코인은 매시간 신호 판정
- 한국 주식은 09:00–15:30 KST 동안만
- 미국 주식은 22:30–05:00 KST(서머타임 시 -1h) 동안만

자동으로 동작합니다. 사용자는 캘린더를 신경 쓸 필요가 없습니다.

**Schedule Trigger 옵션**

| 옵션 | 동작 |
| :--- | :--- |
| `always` | 크론 그대로 실행 (혼합 파이프라인 기본값 — Fresh Bar Gate가 걸러줌) |
| `market_open` | 지정 캘린더 장중에만 실행 |
| `after_close` | 장 마감 + N분 후 1회 (일봉 전략에 적합) |
| `on_bar_close` | 해당 타임프레임 봉 마감 + N초 후 |

### 3.6 타임프레임 정규화

내부 표기는 `1m · 5m · 15m · 30m · 1h · 4h · 1d · 1w`로 통일하고, 어댑터가 각 거래소 표기로 변환합니다. Provider가 지원하지 않으면 하위 타임프레임에서 리샘플합니다.

- 주식의 `4h`처럼 **의미 없는 조합은 UI에서 선택 불가**하도록 `capabilities.timeframes`를 폼에 반영합니다. (사용성 포인트)
- 일봉 마감 기준: 주식은 캘린더의 장 마감, 코인은 파이프라인 설정(`daily_boundary: "UTC00" | "KST00"`)으로 명시합니다.
  ⚠️ **KST 09:00은 UTC 00:00과 같은 순간입니다.** 업비트 일봉 기준(KST 09:00)이 곧 `UTC00`이며, 실제 선택지는 `UTC00`과 한국 자정(`KST00` = UTC 15:00) 둘입니다.

**봉 단위별 정책** — 백테스트와 실매매의 요구가 다르므로 분리합니다.

| 용도 | 허용 봉 | 필요한 이력 | 소스 |
| :--- | :--- | :--- | :--- |
| **백테스트** | `1d` 이상만 | 수년치 | PyKRX / yfinance / Alpaca — 긴 이력 확보 가능 |
| **실매매 알림** | 전체 (`1m`~) | 지표 계산분(예: 200봉)만 | KIS / Alpaca — 짧은 조회 범위로 충분 |

이 분리 덕분에 **"분봉 과거 이력 확보"라는 가장 비싼 문제를 회피**합니다. 분봉은 최근 200봉만 있으면 되므로 무료 티어로도 동작합니다. 백테스트 게이트는 4.8 참조.

### 3.7 통화

`Item.quote_currency`를 항상 보존하고, 알림 템플릿은 통화 기호와 함께 출력합니다. 서로 다른 통화 금액을 **합산하는 로직은 넣지 않습니다** (환율 도입 전까지). 필요해지면 `fx_rates` 캐시 + `Portfolio Value` 노드를 별도 추가합니다.

### 3.8 수정주가 (Adjusted Price) ★

**소스를 여러 개 쓸 때 지표를 조용히 틀어지게 만드는 1순위 원인입니다.**

액면분할·유상증자가 일어나면 과거 가격이 소급 조정되는데, **소스마다 조정 방식과 기본값이 다릅니다.** yfinance는 조정가가 기본이고 PyKRX는 옵션입니다. 같은 종목의 20일 이동평균이 소스에 따라 다른 값이 나오고, 폴백이 발동해 소스가 바뀌면 **어제와 오늘의 지표가 불연속**해집니다.

**규칙**

- `adjusted` 정책을 **파이프라인 전역으로 하나 고정**한다 (기본 `true`).
- **캐시 키에 `adjusted`를 포함**한다. 섞이면 원인 추적이 불가능해진다.
- Provider가 `capabilities.adjusted != "always"`인데 정책이 `true`면 라우팅에서 제외한다.
- 분할·병합 이벤트를 감지하면 **해당 종목 캐시 전체를 무효화하고 재수집**한다.
- 두 소스가 같은 날 종가를 다르게 주면 경고 로그를 남긴다 (정합성 검증).

### 3.9 Ingestion Worker — 캐시는 성능이 아니라 데이터 자산

Fetcher 노드가 매 실행마다 외부 API를 직접 호출하면 **스크리너로 200종목을 돌리는 순간 무료 API가 차단**됩니다. 수집을 실행 경로에서 분리합니다.

```
[Ingestion Worker] ──주기 수집──▶ [ohlcv_cache] ◀──읽기 전용── [MarketData 노드]
```

| 이점 | 설명 |
| :--- | :--- |
| 레이트 리밋 단일 지점 | 소스별 쿼터를 한 곳에서만 관리 |
| 중복 호출 제거 | 파이프라인 3개가 같은 종목을 써도 API 호출은 1회 |
| 장애 격리 | 소스가 죽어도 캐시된 봉으로 파이프라인은 계속 동작 |
| 백테스트 가속 | 외부 호출 없이 캐시만 재생 |

**핵심 관점**: 무료 소스는 언제든 깨진다는 전제로, `ohlcv_cache`를 "성능 최적화"가 아니라 **영구 보관하는 데이터 자산**으로 다룹니다. yfinance가 막혀도 이미 쌓인 이력으로 백테스트는 계속 돌아갑니다.

- 수집 대상은 **활성 파이프라인이 참조하는 instrument × timeframe의 합집합**에서 자동 도출한다.
- 분봉을 계속 쌓으면 시간이 지나며 분봉 백테스트가 자연스럽게 열린다 (4.8의 커버리지 게이트가 자동 판정).
- Phase 1에서는 노드가 직접 호출해도 되지만, **인터페이스는 처음부터 "노드는 캐시를 읽는다"로 둡니다.** 나중에 워커만 끼워 넣으면 되도록.

---

## 4. 실행 코어

### 4.1 노드 간 데이터 모델 — `Bundle` / `Item`

**원본 문서의 가장 큰 공백**은 노드 간 타입이 `DataFrame → Filtered List`로 바뀌는 지점입니다. 이러면 필터를 두 개 이상 연결할 수 없습니다(두 번째가 받을 DataFrame이 없음). 모든 노드가 **같은 봉투(envelope)** 를 주고받도록 통일합니다.

```python
@dataclass
class Item:
    instrument: InstrumentRef
    timeframe: str
    as_of: datetime                # 기준이 되는 "마감된" 캔들 시각 (UTC 저장)
    ohlcv: pd.DataFrame            # index=UTC datetime, [open,high,low,close,volume]
    features: dict[str, Any]       # 지표: {"sma_20": 98_400_000, "rsi_14": 71.2}
    tags: dict[str, Any]           # 판단: {"ma_cross": "golden", "ai_score": 8}
    meta: dict[str, Any]           # provider, 지연, 원본 응답 요약

@dataclass
class Bundle:
    items: list[Item]
    context: dict[str, Any]        # 파이프라인 전역 값 (시장 지수, 뉴스 요약 등)
```

**규칙**

- 필터 노드는 `ohlcv`를 **보존한 채** `items`만 걸러내고 근거를 `features`/`tags`에 남긴다 → 필터 체이닝 가능.
- 빈 `Bundle`도 정상 출력이다. 하위 노드는 빈 입력 시 no-op (실패 아님).
- 단일 심볼 전략과 다중 심볼 스크리너를 **같은 구조로** 표현한다. `len(items)`만 다르다.
- DataFrame은 프로세스 내 참조로 전달하고, 저장 시에는 요약(shape·마지막 행·해시)만 기록한다.

### 4.2 노드 인터페이스

```python
class BaseNode(Protocol):
    type: ClassVar[str]                     # "marketData"
    ParamsModel: ClassVar[type[BaseModel]]
    inputs:  ClassVar[list[str]]            # ["main"]
    outputs: ClassVar[list[str]]            # ["main"] / ["true","false"]

    async def run(self, inputs: dict[str, Bundle],
                  params: BaseModel, ctx: RunContext) -> dict[str, Bundle]: ...
```

```python
@dataclass
class RunContext:
    run_id: str
    mode: Literal["backtest", "shadow", "notify", "paper", "live"]
    now: datetime                   # ⚠️ 노드는 반드시 이 값만 사용. datetime.now() 금지
    user_tz: ZoneInfo               # 표시용 (기본 Asia/Seoul). 저장은 항상 UTC
    providers: ProviderRegistry
    credentials: CredentialResolver
    cache: CacheStore
    log: NodeLogger
```

`ctx.now` 강제가 **백테스트–실행 동치성의 핵심**입니다. 백테스트는 이 값만 과거로 되돌려 같은 노드 코드를 재생합니다.

### 4.3 실행 엔진

1. **검증** — 사이클 검출, 핸들 연결 유효성, 파라미터 Pydantic 검증. 실패 시 실행 자체를 거부.
2. **위상 정렬** — `nx.topological_generations()`로 레벨 분할.
3. **레벨별 병렬** — 같은 레벨은 `asyncio.gather`. 동시 실행 수 제한.
4. **노드 상태** — `pending → running → (success | error | skipped)`.
5. **분기** — Condition Splitter는 `true`/`false` 중 한쪽에만 출력. 미선택 브랜치의 하위 노드는 `skipped` 전파.
6. **에러 정책** (노드별 `on_error`)

| 정책 | 동작 |
| :--- | :--- |
| `fail` | 실행 중단 |
| `skip` | 해당 노드 skip + 하위 전파 |
| `route` | `error` 핸들로 오류를 내보내 별도 브랜치 실행 (예: 운영 알림) |
| `retry` | 지수 백오프 재시도 후 위 정책으로 폴백 (기본: 외부 API 노드) |

7. **기록** — 노드 진입/종료마다 `node_runs`에 입출력 요약·소요시간·에러 저장.

### 4.4 캔들 마감 처리

미완성 캔들로 판단하면 지표가 흔들려 **신호가 생겼다 사라지는** 전형적 버그가 발생합니다.

- Fetcher 기본 `closed_only: true` — 마지막 미완성 봉 제거.
- `Item.as_of`는 항상 **마감된 마지막 캔들 시각** (캘린더가 판정).
- 트리거는 봉 마감 후 지연(기본 +10초)을 두고 실행.
- 모든 시각은 **UTC 저장**, 표시할 때만 `user_tz` 변환.

### 4.5 중복 알림 방지 (Alert Dedup / Cooldown)

알림 전용 단계에서도 중복 발화는 실사용을 망칩니다(재시도·스케줄 중복·수동 재실행). 실주문 단계의 idempotency와 같은 메커니즘을 알림에 적용합니다.

```
dedup_key = sha256(pipeline_id | node_id | instrument.key | as_of | signal_kind)
```

- `alerts_sent` 테이블의 `dedup_key`에 UNIQUE 제약 → 같은 캔들 기준 알림은 **한 번만** 발송.
- **Cooldown 노드**: "같은 종목은 N시간 내 재알림 금지" 같은 완화 조건도 별도 제공.
- Phase 5에서 이 키 스킴이 그대로 `orders.idempotency_key`가 됩니다 (`| side` 추가).

### 4.6 자격 증명

키를 **DAG JSON에 직접 넣지 않습니다** (파이프라인 export/공유 시 유출). 노드는 Connection ID만 참조합니다 (3.4).

```json
{ "type": "marketData", "params": { "source": "conn_kis_main" } }
```

- `connections` 테이블에 암호화 저장. 마스터 키는 환경변수 / OS 키체인.
- `CredentialResolver`가 실행 시점에만 복호화해 주입.
- 로그·`node_runs` 저장 시 키 패턴 자동 마스킹. `SecretStr`로 실수 노출 방지.
- 파이프라인 export 시 Connection ID만 남고 키는 빠진다 (import 측에서 재연결).
- 증권사·거래소 키는 **가능하면 읽기 전용/거래 전용을 분리**하고, **출금 권한은 절대 부여하지 않는다.**

### 4.7 저장소

SQLite + **WAL 모드**. 단일 사용자 전제라 충분하지만, 스케줄러와 API가 동시에 쓰면 `database is locked`가 발생하므로 쓰기는 짧은 트랜잭션 + `busy_timeout`으로 처리합니다. SQLAlchemy를 써서 SQLite 전용 문법을 피합니다.

| 테이블 | 역할 |
| :--- | :--- |
| `pipelines` | 메타 (이름, 활성 버전, 활성화 여부) |
| `pipeline_versions` | DAG JSON 스냅샷 **(불변)**. 실행은 항상 특정 버전 참조 |
| `runs` | 실행 단위 (트리거·mode·시작/종료·상태) |
| `node_runs` | 노드별 입/출력 요약, 로그, 에러, duration |
| `signals` | 생성된 신호 (instrument, 방향, 근거 tags, as_of) |
| `alerts_sent` | 발송 알림 + `dedup_key` UNIQUE |
| `instruments` | 심볼 마스터 캐시 (자동완성용) |
| `market_calendar` | 휴장일·조기폐장 캐시 |
| `connections` | 소스별 사용자 API 키 (암호화). provider_id + 라벨 + 자격증명 |
| `source_routes` | `(venue, timeframe) → 우선순위 소스 목록` 라우팅 표 |
| `llm_cache` | 프롬프트 해시 → 응답 |
| `ohlcv_cache` | **데이터 자산.** 아래 참조 |
| `ingestion_jobs` | 수집 대상·마지막 성공 시각·실패 카운트 |
| `orders` / `positions` | **Phase 5**. 스키마만 미리 정의 |

**`ohlcv_cache` 스키마 주의점**

```
PK: (venue, symbol, timeframe, adjusted, bar_time)
    + source_id        어느 소스에서 받았는지 (정합성 추적용)
    + ingested_at
```

- `adjusted`를 **키에 포함**해야 조정가/비조정가가 섞이지 않습니다 (3.8).
- `source_id`를 남겨야 폴백으로 소스가 바뀐 구간을 사후에 찾을 수 있습니다.
- 이 테이블은 **삭제하지 않습니다.** 무료 소스가 막혀도 남는 유일한 자산입니다. 백업 대상에 포함하세요.

**버전 불변성**: 실행 중 캔버스를 편집해도 진행 중인 Run은 영향받지 않습니다. 저장 시 새 버전을 만들고 Run은 `pipeline_version_id`를 고정합니다.

### 4.8 백테스트

동일 노드 코드를 시각만 바꿔 재생합니다.

```python
for bar_time in calendar.bars(start, end, timeframe):
    ctx = RunContext(mode="backtest", now=bar_time, ...)
    await engine.execute(pipeline_version, ctx)
```

| 이슈 | 대응 |
| :--- | :--- |
| **미래 참조 (look-ahead)** | Provider는 `end` 이후 캔들을 절대 반환하지 않는다. backtest 모드에서 assert로 강제 |
| **LLM 비용/비결정성** | `(model, prompt_hash, input_digest)` 키로 `llm_cache` 저장 → 재실행 무료·결정적. `temperature=0` 권장. 캐시 미스 정책: 호출 / 스킵 / 중단 |
| **시장별 캘린더** | 백테스트 루프도 캘린더 기준으로 봉을 생성 (휴장일 건너뜀) |
| **체결 가정 (Phase 5)** | 다음 봉 시가 체결 기본, 슬리피지·수수료·세금(국내 거래세) 파라미터화 |
| **성과 지표** | 총수익률·MDD·승률·샤프·거래 횟수 — `runs`에 요약 저장 |

**커버리지 게이트 — 백테스트는 일봉 이상만**

백테스트 시작 전 `ohlcv_cache` 커버리지를 확인해 요청 구간을 채울 수 없으면 **명확한 사유와 함께 거부**합니다.

```
요청: nasdaq:AAPL · 5m · 2024-01-01 ~ 2025-01-01
결과: ❌ 거부 — 5m 캐시 커버리지 2025-11-03 이후 (요청 구간의 3%)
      제안: 1d로 백테스트하거나, 수집이 더 쌓일 때까지 대기
```

- 초기에는 분봉 커버리지가 없으므로 **자연스럽게 일봉만 통과**합니다.
- Ingestion Worker(3.9)가 분봉을 계속 쌓으면 커버리지가 늘어 **시간이 지나며 자동으로 열립니다.** 하드코딩 금지보다 이쪽이 낫습니다.
- 하드 상한만 별도로 둡니다: `1m` 봉 백테스트는 데이터·연산량 대비 실익이 낮아 비허용.

**분봉 전략의 대안 검증 — Forward Test (shadow) 모드**

분봉 전략은 백테스트 없이 라이브로 가는 셈이라 위험합니다. 파이프라인을 실시간으로 돌리되 **알림은 보내지 않고 `signals`에만 기록**하는 모드를 제공합니다. 몇 주 돌린 뒤 신호 품질을 확인하고 알림을 켜는 흐름이 분봉 전략의 현실적 검증 경로입니다.

### 4.9 관측성

- 실행 진행을 SSE로 스트리밍 → 캔버스에서 노드 색이 실시간 변경 (n8n 방식).
- `node_runs`의 입출력 스냅샷으로 **"왜 이 신호가 나왔는가"를 사후 재현**.
- 구조화 로그(JSON) + `run_id` / `node_id` 상관 필드.

---

## 5. 노드 카탈로그

| 범주 | 노드 | 입력 → 출력 | 주요 파라미터 |
| :--- | :--- | :--- | :--- |
| **Trigger** | Schedule Trigger | — → `main` | cron, 실행 조건(always/market_open/after_close/on_bar_close), 지연 |
| | Manual Trigger | — → `main` | 테스트·디버깅용 |
| **Input** | Market Data Fetcher | — → `main` | instruments[], timeframe, lookback, closed_only, **skip_stale**, **source**(auto / 연결 ID) |
| | Symbol Universe | — → `main` | 고정 목록 / 시총 상위 N / 거래대금 필터 (venue 혼합 가능) |
| **Indicator** | MA Filter | `main` → `main` | 기간, SMA/EMA, 조건(cross_above/below/gt/lt) |
| | Bollinger Filter | `main` → `main` | 기간, 표준편차, 조건 |
| | RSI / MACD Filter | `main` → `main` | 기간, 임계값, 다이버전스 |
| | Custom Expression | `main` → `main` | 안전 평가식 (`close > sma_20 and rsi_14 < 30`) |
| **AI / Logic** | LLM Decision | `main` → `main` | provider, model, 프롬프트 템플릿, 출력 스키마, 캐시 정책 |
| | Condition Splitter | `main` → `true` / `false` | 조건식 |
| | Merge | `a`, `b` → `main` | union / intersection / append |
| | **Alert Cooldown** | `main` → `main` | 종목당 재알림 금지 기간 |
| | Sort / Limit | `main` → `main` | 점수 상위 N개만 통과 (알림 폭주 방지) |
| **Action** | Telegram Alert | `main` → `main` | credential_id, chat_id, 템플릿 |
| | Persist Signal | `main` → `main` | `signals` 테이블 기록 |
| | *Broker Order* | `main` → `main` | **Phase 5**. RiskGuard 필수 |

> **Alert Cooldown**, **Sort/Limit**, **skip_stale**은 원본에 없던 항목입니다. 스크리너형 파이프라인에서 알림이 수십 건씩 쏟아지는 것을 막는 실사용 필수 장치입니다.

---

## 6. DAG JSON 스키마 (개정)

원본 대비: `data` → `params`, 핸들 명시, 자격 증명 참조, 버전/모드, 에러 정책, **venue 포함 심볼**.

```json
{
  "pipeline_id": "pipe_multimarket_v1",
  "version": 3,
  "name": "코인+주식 이평선 돌파 AI 검증 알림",
  "settings": {
    "user_timezone": "Asia/Seoul",
    "default_mode": "notify",
    "daily_boundary": "UTC00",
    "adjusted": true,
    "max_concurrency": 4
  },
  "nodes": [
    {
      "id": "node_0",
      "type": "scheduleTrigger",
      "position": { "x": 0, "y": 0 },
      "params": { "cron": "0 * * * *", "run_when": "always", "delay_seconds": 10 }
    },
    {
      "id": "node_1",
      "type": "marketData",
      "position": { "x": 240, "y": 0 },
      "params": {
        "instruments": ["upbit:KRW-BTC", "upbit:KRW-ETH", "krx:005930", "nasdaq:AAPL"],
        "timeframe": "1h",
        "lookback": 200,
        "closed_only": true,
        "skip_stale": true,
        "source": "auto"
      },
      "on_error": { "policy": "retry", "max_attempts": 3, "fallback": "route" }
    },
    {
      "id": "node_2",
      "type": "taFilter",
      "position": { "x": 480, "y": 0 },
      "params": { "indicator": "SMA", "period": 20, "condition": "cross_above", "source": "close" }
    },
    {
      "id": "node_3",
      "type": "llmDecision",
      "position": { "x": 720, "y": 0 },
      "params": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "credential_id": "cred_llm",
        "prompt": "다음 종목의 캔들 패턴과 지표 상태를 보고 매수 타당성을 평가하라.\n종목: {{instrument.display_name}} ({{instrument.asset_class}})\n지표: {{features}}",
        "output_schema": { "score": "int(1..10)", "reason": "string" },
        "temperature": 0,
        "cache": "always"
      },
      "on_error": { "policy": "route" }
    },
    {
      "id": "node_4",
      "type": "conditionSplitter",
      "position": { "x": 960, "y": 0 },
      "params": { "expression": "tags.score >= 8" }
    },
    {
      "id": "node_5",
      "type": "alertCooldown",
      "position": { "x": 1200, "y": -80 },
      "params": { "per_instrument_hours": 24 }
    },
    {
      "id": "node_6",
      "type": "actionTelegram",
      "position": { "x": 1440, "y": -80 },
      "params": {
        "credential_id": "cred_telegram",
        "chat_id": "@my_signal_channel",
        "template": "[{{instrument.venue}}] {{instrument.display_name}} 매수 신호\n점수: {{tags.score}}/10\n현재가: {{features.close}} {{instrument.quote_currency}}\n사유: {{tags.reason}}"
      }
    },
    {
      "id": "node_9",
      "type": "actionTelegram",
      "position": { "x": 960, "y": 220 },
      "params": { "credential_id": "cred_telegram", "chat_id": "@ops_alerts",
                  "template": "[오류] {{node_id}}: {{error.message}}" }
    }
  ],
  "edges": [
    { "id": "e0", "source": "node_0", "source_handle": "main",  "target": "node_1", "target_handle": "main" },
    { "id": "e1", "source": "node_1", "source_handle": "main",  "target": "node_2", "target_handle": "main" },
    { "id": "e2", "source": "node_2", "source_handle": "main",  "target": "node_3", "target_handle": "main" },
    { "id": "e3", "source": "node_3", "source_handle": "main",  "target": "node_4", "target_handle": "main" },
    { "id": "e4", "source": "node_4", "source_handle": "true",  "target": "node_5", "target_handle": "main" },
    { "id": "e5", "source": "node_5", "source_handle": "main",  "target": "node_6", "target_handle": "main" },
    { "id": "e6", "source": "node_1", "source_handle": "error", "target": "node_9", "target_handle": "main" },
    { "id": "e7", "source": "node_3", "source_handle": "error", "target": "node_9", "target_handle": "main" }
  ]
}
```

---

## 7. 디렉터리 구조 (제안)

```
project2/
├── docs/trading_bot_node_handoff.md
├── ARCHITECTURE.md
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/                 # pipelines · runs · instruments · credentials · backtest
│   │   ├── engine/
│   │   │   ├── types.py         # Item, Bundle
│   │   │   ├── context.py       # RunContext
│   │   │   ├── graph.py         # 검증 · 위상정렬
│   │   │   └── runner.py        # 실행 루프 · 에러 정책
│   │   ├── market/              # ★ 멀티 마켓 추상화
│   │   │   ├── instrument.py    # InstrumentRef
│   │   │   ├── calendar.py      # Crypto24x7 / KRX / UsEquity
│   │   │   ├── timeframe.py     # 정규화 · 리샘플
│   │   │   ├── routing.py       # (venue,timeframe) → 소스 우선순위 · 폴백
│   │   │   ├── adjust.py        # 수정주가 정책 · 분할 감지
│   │   │   └── connections.py   # Connection CRUD · health_check
│   │   ├── ingestion/           # 수집 워커 · 레이트리밋 · 캐시 채우기
│   │   ├── providers/
│   │   │   ├── base.py          # MarketDataProvider · BrokerProvider · Capabilities
│   │   │   ├── registry.py      # 플러그인 등록 (import 시 자동 수집)
│   │   │   ├── upbit.py  binance.py  toss.py  kis.py
│   │   │   ├── pykrx.py  yfinance.py  alpaca.py  fdr.py
│   │   │   └── llm/             # anthropic.py · openai.py
│   │   ├── nodes/
│   │   │   ├── registry.py
│   │   │   └── triggers/ inputs/ indicators/ ai/ logic/ actions/
│   │   ├── backtest/            # replay.py · metrics.py
│   │   ├── scheduler/
│   │   ├── storage/             # SQLAlchemy 모델 · 리포지토리
│   │   ├── risk/                # Phase 5
│   │   └── core/                # config · crypto · logging
│   └── tests/
└── frontend/src/
    ├── canvas/    nodes/    runs/    backtest/    store/
```

---

## 8. 배포 (Self-hosted)

- **Docker Compose**: `backend`(FastAPI+APScheduler) + `frontend`(정적 서빙) + SQLite 볼륨.
- 기본 바인딩은 **`127.0.0.1`**. 원격 접속이 필요하면 리버스 프록시(Caddy/Nginx) + 인증을 반드시 앞에 둡니다.
- 인증은 **단일 API 토큰 + 세션 쿠키** 수준으로 충분합니다 (멀티유저 없음). 다만 **토큰 없이 인터넷에 노출하면 안 됩니다** — 거래소 키가 담긴 시스템입니다.
- 백업 대상: SQLite 파일 + 마스터 키. 마스터 키를 잃으면 자격 증명 복호화가 불가능합니다.
- 타임존: 컨테이너는 UTC로 고정, 표시만 `user_timezone`.

---

## 9. 구현 로드맵

원본 3단계를 유지하되 **Phase 0(계약 확정)**을 앞에 두고, 실주문을 마지막으로 미룹니다.

### Phase 0 — 계약 확정 (3~5일)
- `Item` / `Bundle` / `RunContext` / `BaseNode` / `InstrumentRef` / `MarketCalendar` / `MarketDataProvider` 타입 확정
- DAG JSON 스키마 + Pydantic 모델 확정, 프론트 폼 자동 생성 경로 확인
- DB 스키마 초안 (Alembic)
- **산출물**: 더미 노드 3개로 DAG가 실행되는 통과 테스트

### Phase 1 — Core Engine & 코인 알림 E2E (2주)
- React Flow 캔버스, 노드 팔레트, 파라미터 폼
- 실행 엔진(위상정렬·병렬·에러 정책·`node_runs` 기록)
- **업비트** 한 곳으로 `MarketData → MA Filter → Telegram` E2E 동작
- APScheduler, 캔들 마감 처리, 실행 이력 뷰어
- ⚠️ 이 단계에선 노드가 직접 API를 호출해도 되지만, **인터페이스는 "노드는 `ohlcv_cache`를 읽는다"로 고정**합니다. Phase 2에서 워커만 끼워 넣도록
- ✅ 여기서 "동작하는 물건"이 나옵니다

### Phase 2 — 멀티 마켓 확장 (3주) ★
- `MarketCalendar` 3종(24x7 / KRX / US) + `exchange_calendars` 연동
- **Connections 화면** — `credential_schema` 기반 폼 자동 생성 + [연결 테스트]
- **Routing Table** — `(venue, timeframe) → 소스 우선순위` 편집 UI + 폴백 동작
- 일봉 소스: `PykrxProvider`, `YFinanceProvider`, `FdrProvider` (무인증, 즉시 가능)
- 분봉 소스: `KisProvider`, `AlpacaProvider`
- **`TossProvider`** (⚠️ 스펙 확인 선행). 제약 시 시세는 위 소스로, 주문만 Toss로 분리
- **Ingestion Worker** + `ohlcv_cache` 영구 보관, 수정주가 정책
- `instruments` 심볼 마스터 + 자동완성 UI
- **Fresh Bar Gate** 및 혼합 파이프라인 검증 (코인+한국+미국 동시)
- 서머타임 전환일 회귀 테스트, 두 소스 종가 정합성 검증
- ✅ 두 번째·세 번째 어댑터를 붙여봐야 추상화가 맞는지 검증됩니다

### Phase 3 — AI 노드 & 분기 (2~3주)
- LLM Provider 추상화 + 구조화 출력 + `llm_cache`
- Condition Splitter / Merge / Alert Cooldown / Sort·Limit
- 스킵 전파, 에러 라우팅 브랜치
- 레이트 리밋 · LLM 비용 상한
- ⚠️ Celery/Redis는 **asyncio로 감당 안 될 때** 도입. 조기 도입은 복잡도만 늘립니다

### Phase 4 — 백테스트 (3주)
- 캘린더 기반 시간 리플레이, look-ahead 방지 assert
- 성과 지표, 리포트 UI
- LLM 캐시 기반 재현성 검증

### Phase 5 — 실주문 (선택, 3주+)
- `BrokerProvider` 구현, RiskGuard(주문 상한·일일 한도·킬 스위치), idempotency
- `paper` 모드로 최소 2주 실거래 대조 검증 후 `live` 소액 전환
- 부분 체결·주문 거부·국내 거래세/미국 세금 처리

---

## 10. 주요 리스크와 대응

| 리스크 | 영향 | 대응 |
| :--- | :--- | :--- |
| ⚠️ Toss API 스펙 불확실 | Phase 2 지연 | 시세/주문 인터페이스 분리, 라우팅 폴백, KIS 대체 경로 (3.3 / 3.4) |
| 무료 소스 차단·중단 | 데이터 유실 | Ingestion Worker 단일 창구 + 라우팅 폴백 + 캐시 영구 보관 (3.9) |
| **수정주가 불일치** | **지표가 조용히 틀어짐** | 전역 정책 고정 + 캐시 키 포함 + 소스 간 종가 검증 (3.8) |
| 분봉 전략 미검증 | 무근거 신호 | 커버리지 게이트 + Forward Test(shadow) 모드 (4.8) |
| 미국장 서머타임 오처리 | 신호 시각 1시간 오차 | `ZoneInfo` 사용, 전환일 회귀 테스트 (3.2) |
| 장 마감 중 신호 재발생 | 알림 폭주 | Fresh Bar Gate + skip_stale (3.5) |
| 중복 알림 | 신뢰도 하락 | dedup_key UNIQUE + Cooldown 노드 (4.5) |
| 미완성 캔들 신호 | 잘못된 판단 | closed_only + 마감 후 지연 (4.4) |
| 백테스트 미래 참조 | 전략 과신 | Provider 시간 컷 + assert (4.8) |
| LLM 비용 폭증 | 운영비 | 캐시, 호출 상한, **필터 뒤에 배치** |
| API 키 유출 | 치명적 | 암호화 저장, DAG에서 분리, 출금 권한 미부여, 로컬 바인딩 (4.6 / 8) |
| 거래소 레이트 리밋 | 데이터 누락 | Ingestion Worker에서 단일 지점 관리 + 백오프 (3.9) |
| ⚠️ pandas-ta 유지보수 중단 | 지표 신뢰성 | 대체 라이브러리 확정 (2.1) |
| SQLite 잠금 경합 | 실행 실패 | WAL + 짧은 트랜잭션 + busy_timeout (4.7) |

---

## 11. 미결정 사항

1. ⚠️ **Toss 증권 오픈 API 스펙** — 공개 API 존재 여부, 인증 방식, 과거 봉 조회 범위, 미국주식 지원, 레이트 리밋. Phase 2 착수 전 확인 필요.
2. ⚠️ **지표 라이브러리** — `pandas-ta-classic` / TA-Lib / 직접 구현.
3. **Phase 2에서 먼저 구현할 소스 순서** — 무인증 소스(PyKRX·yfinance·FDR)만으로 일봉을 먼저 열지, KIS/Alpaca 분봉까지 한 번에 갈지. 어떤 Connection을 실제로 등록할지는 사용자가 나중에 정하면 되며, 시스템은 모두 지원합니다.
4. **Alpaca 데이터 피드** — 무료 IEX(거래량이 얇게 보일 수 있음) vs 유료 SIP. 일단 IEX로 시작하고 필요 시 Connection 설정에서 전환.
5. **코인 일봉 경계** — `UTC00`(= KST 09:00, 업비트 기준) vs `KST00`(한국 자정). 현재 기본값은 `UTC00`.
6. **미국주식 프리/애프터마켓** 포함 여부.
7. **알림 채널** — 텔레그램 외 Slack/Discord/이메일 필요 여부.
8. **LLM 노드 배치** — 필터 뒤(비용 절감, 현 권장) vs 앞(더 넓은 판단).

> 무료 데이터 API의 티어 정책(호출 한도, 이력 범위)은 자주 바뀝니다. 본 문서의 수치성 서술은 **착수 시점에 재확인**하세요.

---

## 부록 A. 원본 문서 대비 변경 요약

| # | 항목 | 원본 | 개정 | 이유 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 노드 간 타입 | DataFrame → Filtered List | 단일 `Bundle` 타입 통일 | 필터 체이닝 불가 문제 해소 |
| 2 | 심볼 표기 | `"market": "KRW-BTC"` | `InstrumentRef` (`venue:symbol`) | 3개 시장 혼용 시 티커 충돌·라우팅 |
| 3 | 거래 시간 | 언급 없음 | `MarketCalendar` 3종 | 주식 장 운영시간·휴장일·서머타임 |
| 4 | 시세/거래 소스 | "거래소 API" 단일 | `MarketDataProvider` / `BrokerProvider` 분리 | Toss·KIS·PyKRX 조합 대응 |
| 5 | 혼합 파이프라인 | 고려 없음 | Fresh Bar Gate (`skip_stale`) | 장 마감 중 신호 재발생 방지 |
| 6 | 중복 알림 | 없음 | `dedup_key` UNIQUE + Cooldown 노드 | 재시도·중복 트리거 대응 |
| 7 | 자격 증명 | 없음 (DAG 노출 우려) | `credential_id` 참조 + 암호화 | 키 유출 방지 |
| 8 | 캔들 마감 | 언급 없음 | `closed_only` + 마감 후 지연 | 신호 진동 방지 |
| 9 | 시간 처리 | 언급 없음 | `ctx.now` 주입 강제, UTC 저장 | 백테스트 동치성 |
| 10 | 백테스트 | "순수 함수로 작성" | 시간 리플레이 + look-ahead assert + LLM 캐시 | LLM은 순수 함수가 아님 |
| 11 | DAG 스키마 | 핸들/버전/에러 없음 | `source_handle`, `version`, `on_error` | 분기·에러 브랜치 표현 |
| 12 | 큐 인프라 | Phase 2에 Celery | asyncio 우선, 필요 시 도입 | 조기 복잡도 회피 |
| 13 | 실행 이력 | 없음 | `runs` / `node_runs` + SSE | 디버깅 필수 |
| 14 | 로드맵 | 3단계, Phase 3에 실주문 | Phase 0 추가, 멀티마켓을 Phase 2로, 실주문은 Phase 5 | 알림 전용 우선 결정 반영 |
| 15 | 상태관리 | Zustand / Redux | Zustand 확정 | React Flow 권장 |
| 16 | 데이터 소스 | 소스 하드코딩 암시 | **Provider 플러그인 + Connections + Routing Table** | 사용자가 키 직접 구성, 소스 교체·폴백 가능 |
| 17 | 소스 선택 UI | — | 소스를 **노드로 만들지 않음**. 노드는 `source: auto` 참조 | 파이프라인의 소스 비종속성·멀티마켓 유지 |
| 18 | 시세 수집 | 노드가 직접 API 호출 | **Ingestion Worker → ohlcv_cache → 노드(읽기 전용)** | 레이트 리밋·중복 호출·장애 격리 |
| 19 | 수정주가 | 언급 없음 | 전역 정책 + 캐시 키 포함 + 정합성 검증 | 소스별 조정 방식 차이로 지표가 틀어짐 |
| 20 | 백테스트 범위 | 제한 없음 | **일봉 이상만** + 커버리지 게이트, 분봉은 shadow 모드 | 분봉 과거 이력 확보 비용 회피 |
