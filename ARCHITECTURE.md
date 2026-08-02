# ARCHITECTURE.md

**marketscan** — 멀티마켓 횡단면 스크리너 · 신호 알림 CLI 아키텍처 설계서

> 프로젝트명은 v0.5에서 `tradeflow` → `assetscan` → **`marketscan`** 으로 정리되었습니다.
> `trade`는 매매를 암시하는데 이 시스템은 매매하지 않고, `flow`는 폐기된 캔버스 은유였습니다.
> `asset`은 중립어이긴 하나 부동산·채권까지 포괄해 범위가 흐릿했습니다.
> **`market`은 이 프로젝트의 정체성을 그대로 말합니다** — 3장(멀티 마켓 추상화)이 존재 이유이고,
> "여러 시장을 하나의 유니버스로 훑는다"가 1.1의 목적 그 자체입니다. `scan`은 1.2의
> "값은 예측 정확도가 아니라 **훑는 범위**와 규율에서 나온다"를 담고 있어 유지했습니다.
>
> 검토했으나 기각한 후보 — `stockscan`은 코인을 부록으로 만들어 10장 리스크 표의
> "멀티마켓이 유일한 차별점"과 충돌하고, `signal-discovery`는 **1.3이 비목표로 못박은
> "전략 탐색"을 이름이 권하는 꼴**이 됩니다(10장 리스크 1순위가 과적합입니다).
> **파이썬 패키지는 `app`이라 이름이 바뀌어도 임포트는 그대로입니다.**

> 상태: **v0.5 (draft)** — `docs/trading_bot_node_handoff.md`의 아이디어를 구현 가능한 수준으로 구체화한 문서입니다.
> ⚠️ 표시는 **미결정 또는 외부 확인이 필요한 항목**입니다.

**확정된 전제**

| 항목 | 결정 |
| :--- | :--- |
| 배포 형태 | **개인용 / Self-hosted**. 서비스화·멀티유저 없음 |
| 대상 시장 | **암호화폐 + 한국 주식 + 미국 주식**. 세 시장을 **하나의 유니버스**로 다루는 것이 이 프로젝트의 존재 이유 |
| 1차 범위 | **신호 알림 전용**. 실주문은 검증 후 별도 단계 |
| 봉 단위 | **일봉·주봉 전용.** 분봉은 백테스트뿐 아니라 **판단 자체에서 제외** (v0.5 변경) |
| 전략의 축 | **횡단면 랭킹**이 1급. 종목별 시계열 필터는 보조 (v0.5 변경) |
| 전략 표현 | **파이썬 클래스 1개.** 지표를 노드로 쪼개지 않는다 (v0.5 변경) |
| 인터페이스 | **CLI.** 웹 서버·캔버스 없음 (v0.5 변경). ⚠️ 자동 실행을 무엇이 맡는지는 미결정 (11장) |
| 배포 | **`uv` + `[project.scripts]`.** Docker 없음 (v0.5 변경) |
| 데이터 소스 | **사용자가 Connection으로 직접 구성**. 소스는 플러그인, 파이프라인은 소스에 비종속 |

---

## 1. 개요

### 1.1 목적

**암호화폐·한국주식·미국주식을 하나의 유니버스로 매일 훑어, 볼 만한 소수의 후보를 사람에게 올려주는** 개인용 도구. 전략은 파이썬 클래스로 쓰고, 파이프라인은 그 전략을 데이터·LLM·알림과 엮는 얇은 배선이다.

**형태는 CLI다.** 웹 서버도 캔버스도 없고, 하루 몇 번 `marketscan run --commit`이 돌면 결과가 **stdout과 정적 HTML 리포트**로 나온다. 사람과 LLM은 같은 CLI로 그 결과에 질문한다(12장).

⚠️ **그 실행을 무엇이 거는지는 아직 정하지 않았다.** OS 스케줄러에 맡기는 쪽과 스케줄·알림을 함께 갖는 `serve` 명령을 두는 쪽이 후보다(11장). **어느 쪽이든 CLI 표면은 바뀌지 않으므로** 지금 정하지 않는다 — `run --commit`을 누가 부르든 동작은 같다.

**이 시스템은 예측 기계가 아니라 주의력 기계다.** 시장을 맞히는 것이 아니라, 혼자서는 볼 수 없는 범위를 대신 보고 정해둔 규칙을 대신 지키는 것이 목적이다. 이 문서의 거의 모든 결정이 이 한 줄에서 파생된다.

### 1.2 설계 원칙

| 원칙 | 의미 |
| :--- | :--- |
| **주의력 우선 (Coverage over Prediction)** | 값은 예측 정확도가 아니라 **훑는 범위와 규율**에서 나온다. 최종 판단은 사람이 한다. 기계는 후보를 좁히고 근거를 붙인다 |
| **횡단면 우선 (Cross-Section First)** | 1급 연산은 "한 시점에 유니버스를 줄 세우는 것"이다. 종목별 시계열 조건은 그 앞뒤에 붙는 보조 필터다. 표본 수·시장 방향 상쇄·팩터의 정의가 모두 횡단면에서 나온다 |
| **시장 중립 코어 (Market-Neutral Core)** | 엔진·노드는 "코인/주식"을 모르며, 거래소별 차이는 Provider와 Calendar 뒤로 숨긴다. 새 증권사 추가 = 어댑터 1개 작성 |
| **결정성 (Determinism)** | 같은 입력 + 같은 시각 → 같은 출력. 백테스트와 실행이 동일 코드 경로를 쓴다 |
| **시간 주입 (Injected Clock)** | 노드는 `datetime.now()`를 직접 호출하지 않는다. 모든 시각은 실행 컨텍스트가 제공한다 |
| **격리된 실패 (Isolated Failure)** | 노드 하나의 실패가 파이프라인 전체를 중단시키지 않는다 |
| **기본은 안전 (Safe by Default)** | 실주문은 명시적으로 켜야만 동작한다. 기본 모드는 `notify` |
| **관측 가능성 (Observability)** | 모든 실행의 노드별 입/출력이 저장되어 사후 재현이 가능하다 |

### 1.3 비목표 (Non-goals)

- 초저지연(HFT) — **일봉 기준** 전략을 대상으로 한다.
- **장중(intraday) 판단** — 분봉으로 진입 타이밍을 재지 않는다. 3.6 참조.
  왕복 비용(국내 거래세 + 수수료 + 스프레드)이 분 단위 전략의 기대 수익을 넘어서고,
  그 구간의 상대는 호가창을 보고 있는 참여자다. **못 이기는 게임이라 안 하는 것**이지
  데이터가 없어서 미루는 것이 아니다.
- **전략 탐색** — 지표 조합·파라미터를 백테스트로 뒤져 좋은 것을 찾는 용도로 쓰지 않는다.
  탐색 공간은 수백만인데 일봉 10년은 2,500행이라, 우연히 맞는 조합이 반드시 나오고
  그것을 구분할 표본이 없다. 백테스트의 용도는 4.8에서 재정의한다.
- 멀티테넌시·과금·회원 관리 — Self-hosted 단일 사용자 전제.
- 범용 워크플로 엔진 — n8n의 일반 자동화 기능을 목표로 하지 않는다.
- **비주얼 전략 편집** — 지표를 노드로 조합해 전략을 만드는 UI를 목표로 하지 않는다. 5장 참조.
- 실시간 호가/체결 스트리밍 — 초기 범위는 봉(OHLCV) 기반.
- **수익률 측정** — 이 시스템은 **진입 신호만** 만들고 청산(exit) 개념이 없다.
  총수익률·MDD·샤프는 청산 없이는 정의 자체가 불가능하므로 계산하지 않는다.
  대신 신호 품질 지표를 쓴다 (4.8). Phase 5에서 실주문이 들어오기 전까지 이 선은 유지한다.

---

## 2. 시스템 구성

```text
      사람                 자동 실행 ⚠️미정          LLM (Claude Code 등)
       │                          │                        │
       │ explain · stats          │ ingest                 │ explain --json
       │ backtest                 │ run --commit           │ strategy check
       ▼                          ▼                        ▼
┌──────────────────────────────────────────────────────────────┐
│  marketscan CLI (Typer) — 웹 서버 없음. 스케줄 방식은 미결정      │
│  run · ingest · backtest · explain · signals · stats ·        │
│  strategy · verify · describe                     (12장)      │
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
│Strategy│ │AI/LLM  │ │Logic   │ │Action        │
│Runner  │ │정성 필터│ │쿨다운  │ │Telegram/Slack│
│(클래스) │ │+캐시   │ │Sort/컷 │ │(→ Broker P5) │
└────┬───┘ └────────┘ └────────┘ └──────────────┘
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
  Upbit  Binance  PyKRX  yfinance  FDR   Alpaca   KIS/Toss
 (CCXT)  (CCXT)  (무인증)(무인증) (무인증) (P3+)   (P5 주문)
 └────────── Phase 2 · 일봉 · 키 불필요 ──────────┘
                                                          
┌───────────────────────────────────────────────────────────────┐
│  Storage: SQLite(WAL) — 단일 프로세스라 잠금 경합도 사라짐       │
│  pipelines · pipeline_versions · strategy_versions · runs ·   │
│  node_runs · signals · alerts_sent · instruments ·            │
│  market_calendar · connections(암호화) · source_routes ·       │
│  llm_cache · ohlcv_cache · ingestion_jobs · backtest_runs     │
│  [Phase 5] orders · positions                                 │
└───────────────────────────────────────────────────────────────┘
```

### 2.1 기술 스택

| 레이어 | 선택 | 비고 |
| :--- | :--- | :--- |
| **인터페이스** | **CLI (Typer)** | 웹 서버·프론트엔드 없음. 12장 참조 |
| 리포트 | **정적 HTML 파일 생성** + uPlot vendoring | 서빙하지 않고 `reports/`에 파일로 떨어뜨린다. 파일로 남아 나중에 비교할 수 있는 쪽이 낫다 |
| 스케줄 | ⚠️ **미결정** — OS 스케줄러 vs `serve` 명령 | OS 쪽은 상주 프로세스가 죽어도 같이 죽지 않고 재부팅을 견딘다. `serve` 쪽은 알림·재시도·백오프를 한곳에 모을 수 있다. **판단을 미룰 수 있는 이유는 CLI 표면이 양쪽에서 같기 때문이다** (11장) |
| 런타임 | Python 3.11+, Pydantic v2 | 언어 선택 근거는 아래 참조 |
| DAG | NetworkX (위상정렬·사이클 검출) + 자체 asyncio 러너 | 파이프라인이 얕아져 존재감은 줄었지만 에러 브랜치·팬아웃에 계속 쓴다 |
| 지표 | ⚠️ `pandas-ta-classic` 또는 TA-Lib, 혹은 pandas 직접 구현 | 원본 `pandas-ta`(twopirllc)는 사실상 유지보수 중단. **전략이 파이썬 클래스가 되면서 라이브러리 선택은 전략 작성자의 자유**가 되어 결정의 무게가 크게 줄었다 |
| 시세 (일봉) | **PyKRX(한국), yfinance(미국), FDR(종목 마스터), CCXT 공개 OHLCV(코인)** | **전부 무인증.** 일봉 고정의 최대 이득 (3.6) |
| 시세 (분봉) | 한국투자증권 OpenAPI, Alpaca | **범위 밖.** Phase 5에서 주문 집행이 생기면 그때 재검토 |
| 거래 (Phase 5) | CCXT(코인), Toss⚠️ / KIS(주식) | 3.3 Provider 참조 |
| 거래 캘린더 | `exchange_calendars` 또는 `pandas_market_calendars` | KRX·NYSE·NASDAQ 휴장일/조기폐장 내장 |
| DB | SQLite (WAL) | 단일 사용자 + 일봉 + 단일 프로세스면 **확정**. 3.9 / 4.7 참조 |
| LLM | **API · 로컬 커맨드 · 로컬 모델** (Provider 추상화) | 종류별로 결정성 보증이 다르다. 5장 참조 |
| 패키징 | **uv** (`uv.lock` + `requires-python`) + `[project.scripts]` | Docker 없이 재현성 확보. 8절 참조 |

**언어를 파이썬으로 유지하는 이유** — CLI라서 Go/Rust를 떠올리기 쉽지만, 그 직관은 하루 수백 번 호출되는 **개발 도구**에서 온 것입니다. 이 시스템은 하루 3회 도는 배치이고 실행 시간이 수초~수분이라 시작 오버헤드가 0.1% 미만입니다. 결정적으로 **3장(멀티 마켓 추상화)이 파이썬 전용 라이브러리 위에 서 있습니다.**

| 의존 | 다른 언어 대안 |
| :--- | :--- |
| PyKRX (국내 일봉·수급·펀더멘털) | **없음** |
| FinanceDataReader (종목 마스터·상장폐지 목록) | **없음** |
| exchange_calendars (KRX·NYSE 휴장일·조기폐장) | **없음** |
| pandas / polars / TA 라이브러리 | Go에는 없음. Rust는 polars(파이썬에서도 동일하게 사용 가능) |

앞의 세 줄이 결정타입니다. **다른 언어를 고르면 3장을 처음부터 다시 짓는 것부터 시작하는데, 그 3장이 이 프로젝트의 존재 이유입니다.** 성능도 반대 방향입니다 — 4.8의 피처 행렬은 numpy/pandas가 C로 도는 구간이라, Go로 옮기면 rolling 연산을 손으로 짜면서 오히려 느려집니다.

> polars 전환은 **측정 전에 하지 않습니다.** pykrx·yfinance가 pandas를 돌려주고 `Item.ohlcv` 계약도 pandas입니다. Phase 3에서 피처 행렬이 실제로 느리면 그 계산 구간에만 넣습니다.

---

## 3. 멀티 마켓 추상화 ★

**이 시스템의 난이도는 대부분 여기에 있고, 동시에 존재 이유도 여기에 있습니다.** 코인과 주식을 하나의 유니버스로 다루려면 심볼 표기, 거래 시간, 캔들 마감, 통화가 전부 달라지기 때문입니다. 아래 4개 개념으로 흡수합니다.

> **이 장이 freqtrade에도, 유사한 국내 도구에도 없는 부분입니다.** 멀티마켓이 요건에서 빠지는 순간
> 이 프로젝트를 직접 만들 이유의 대부분이 사라집니다 (10장 리스크 표 마지막 항목).

### 3.1 InstrumentRef — 통일 심볼 식별자

거래소마다 표기가 다르고(`KRW-BTC` / `BTC/USDT` / `005930` / `AAPL`), 티커가 시장 간 충돌할 수 있습니다. 항상 **venue를 붙인 정규 문자열**로 다룹니다.

```python
@dataclass(frozen=True)
class InstrumentRef:
    venue: str              # "upbit" | "binance" | "krx" | "nasdaq" | "nyse"
    symbol: str             # "KRW-BTC" | "BTC/USDT" | "005930" | "AAPL"
    asset_class: str        # "crypto" | "equity"
    quote_currency: str     # "KRW" | "USDT" | "USD" — venue 상수가 아니라 symbol에서 유도
    display_name: str       # "비트코인" | "삼성전자" | "Apple Inc."

    @property
    def key(self) -> str:   # "upbit:KRW-BTC", "krx:005930", "nasdaq:AAPL"
        return f"{self.venue}:{self.symbol}"
```

- UI에서는 `display_name`으로 검색하고 내부는 `key`로 다룬다.
- `instruments` 테이블에 심볼 마스터를 캐시해 자동완성을 제공한다 (주식은 종목 수가 많음).

**`quote_currency`는 venue에 고정하지 않는다.** 업비트에는 KRW 말고 BTC·USDT 마켓이
있고 바이낸스도 USDT 전용이 아니다. venue에 상수로 박으면 `upbit:BTC-ETH`의 결제 통화가
KRW로 잘못 붙어 3.7(통화 보존)과 알림 템플릿의 통화 기호가 함께 조용히 틀어진다.
`VenueSpec.quote_style`이 symbol에서 통화를 뽑는 방법을 선언한다.

| `quote_style` | 대상 | 규칙 |
| :--- | :--- | :--- |
| `fixed` | 주식 (krx, nasdaq, nyse) | venue의 통화가 곧 결제 통화 |
| `dash_prefix` | 업비트 원본 표기 | `KRW-BTC` → 앞이 결제 통화 |
| `slash_suffix` | CCXT 통합 표기 | `BTC/USDT` → 뒤가 결제 통화 |

형식이 어긋나면 폴백 없이 파싱 단계에서 거부한다 — 통화를 추측으로 채우면 나중에
어디서 틀렸는지 찾을 수 없다.

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

**`OrderRequest`는 수량과 금액을 모두 표현할 수 있어야 합니다.** 업비트·빗썸의 원화 마켓
**시장가 매수는 수량이 아니라 금액 기준**입니다(CCXT의 `createMarketBuyOrderRequiresPrice`와
같은 지점). 수량만 받는 형태로 굳으면 이 주문을 아예 표현할 수 없습니다. 구현은 Phase 5지만
**인터페이스는 지금 확정합니다.**

```python
@dataclass
class OrderRequest:
    instrument: InstrumentRef
    side: Literal["buy", "sell"]
    quantity: Decimal | None = None   # 수량 지정
    notional: Decimal | None = None   # 금액 지정 (KRW 마켓 시장가 매수)
    # 둘 중 정확히 하나만 설정 — validator로 강제한다
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

| 어댑터 | venue | 일봉 | 주문 | 인증 | 단계 | 비고 |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `CcxtProvider` | upbit, binance, … | ✅ | (P5) | **불필요**\* | **P1 ✅** | 공개 OHLCV는 키 없이 조회된다. **거래소당 파일을 만들지 않는다** |
| `PykrxProvider` | krx | ✅ | ❌ | 없음 | **P2** | 국내 일봉 + **수급(외국인·기관)** + 펀더멘털(PER·PBR·ROE) |
| `YFinanceProvider` | nasdaq, nyse, krx | ✅ | ❌ | 없음 | **P2** | 미국 일봉 이력. 비공식 |
| `FdrProvider` | krx, nasdaq, nyse | ✅ | ❌ | 없음 | **P2** | 종목 마스터 + **상장폐지 목록**(4.8 서바이버십) |
| `AlpacaProvider` | nasdaq, nyse | ✅ | (P5) | 키 | P3+ | yfinance 폴백용. 필수 아님 |
| `KisProvider` | krx, nasdaq, nyse | ✅ | ✅ | 키+토큰 | P5 | 주문 경로. 시세는 무인증 소스로 충분 |
| `TossProvider` | krx, nasdaq, nyse | ⚠️ | ⚠️ | ⚠️ | P5 | 주문 경로 후보. 아래 주의 참조 |

\* 주문(P5)에는 키가 필요하다. 시세만 쓰는 현 범위에서는 불필요.

**일봉 고정(3.6)의 결과로 Phase 2에 필요한 소스가 전부 무인증이 되었다.** 시세 때문에 증권사
API 키를 보관할 이유가 사라지므로, 4.6의 자격 증명 관리는 **LLM·텔레그램에만** 적용된다.
리스크 표(10장)의 "API 키 유출"이 크게 줄어드는 지점이다 — 갖고 있지 않은 키는 새지 않는다.

**코인 거래소는 `CcxtProvider` 하나로 통합합니다.** CCXT를 쓰는 이유가 정확히 이 통합이므로,
`UpbitProvider` / `BinanceProvider`처럼 파일을 쪼개면 이득이 사라집니다. 거래소별 예외만
quirk 모듈로 뺍니다.

```
providers/
  ccxt_base.py        # CcxtProvider(exchange_id=...) — 공통 구현
  ccxt_quirks/
    upbit.py          # KRW 마켓 시장가 매수(금액 기준), 일봉 경계
    bithumb.py        # OHLCV 폴백 경로
```

- **거래소당 CCXT 인스턴스는 하나만 만들어 재사용합니다.** `enableRateLimit=True`는 인스턴스
  단위라, Ingestion Worker가 여러 파이프라인 몫을 몰아 호출하면 프로세스 전역 쿼터가 깨집니다.
  Provider 레지스트리에서 싱글턴으로 관리합니다 (3.9의 "레이트 리밋 단일 지점"과 같은 원칙).
- **CCXT 소스의 `capabilities`는 손으로 선언하지 않고 `ex.has` / `ex.timeframes`에서 유도합니다.**
  수기 표는 언젠가 실제 능력과 어긋나고, 그러면 라우팅이 못 주는 소스로 계속 흘러갑니다.

  ```python
  capabilities = ProviderCapabilities(
      timeframes=tuple(normalize(tf) for tf in ex.timeframes) if ex.has["fetchOHLCV"] else (),
      supports_orders=ex.has["createOrder"],
      ...
  )
  ```

  OHLCV를 못 주는 거래소는 `timeframes`가 비어 라우팅 표(3.4)에서 **자동으로 제외**됩니다.
  ⚠️ 빗썸은 `fetchOHLCV` 지원 여부가 자료마다 엇갈립니다. 문서를 믿지 말고 착수 시
  `ccxt.bithumb().has['fetchOHLCV']`로 런타임 확인하고, 미지원이면 공개 API 캔들
  엔드포인트를 감싸는 별도 어댑터를 씁니다 — `MarketDataProvider` 인터페이스가 있으므로
  CCXT를 거치지 않는 어댑터도 구조를 해치지 않습니다.

> ⚠️ **Toss 증권 API 확인 필요 항목**: 공개 오픈 API 제공 여부와 스펙(인증 방식, 시세 제공 범위, 과거 봉 조회 가능 여부, 레이트 리밋, 미국주식 지원 범위, 개인 개발자 신청 절차)을 확인하지 못했습니다. 어댑터 인터페이스는 시장 중립적이므로 스펙 확인 후 `TossProvider`만 채우면 됩니다. **과거 봉 조회가 제한적이면 시세는 PyKRX/KIS/Alpaca로, 주문만 Toss로 나누는 구성**이 가능하도록 인터페이스를 분리해 두었습니다.

### 3.4 Connection과 라우팅 — 소스는 노드가 아니다

**설계 결정**: 데이터 소스를 파이프라인의 노드로 만들지 **않습니다.** 사용자가 API 키를 직접 구성하는 통제권은 유지하되, 아래 이유로 소스를 파이프라인 바깥으로 뺍니다.

| 소스를 노드로 만들면 | 결과 |
| :--- | :--- |
| 파이프라인이 소스에 종속 | yfinance→Alpaca 교체 시 파이프라인을 뜯어고쳐야 함 |
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

**폴백은 조용히 넘어가면 안 됩니다.** 폴백이 발동하면 소스가 바뀌고, 소스가 바뀌면 수정주가
정책 차이로 지표가 불연속해지며(3.8), 같은 `ctx.now`에 다른 결과가 나와 **`ctx.now` 주입으로
확보한 백테스트–실행 동치성이 소스 레이어에서 무너집니다.** 그래서 폴백 발동은 파이썬 로그가
아니라 **실행 이력에** 남깁니다.

- `FetchResult.failed_sources` — 성공한 소스 앞에서 실패한 소스 목록.
- Fetcher 노드가 이를 `ctx.log.warning`과 `Item.meta["fallback_from"]`으로 올려
  `node_runs`·UI에서 보이게 합니다.
- `ohlcv_cache.source_id`(4.7)와 함께 보면 "어느 구간이 어느 소스로 채워졌는지"가 복원됩니다.
- ⚠️ **미래 참조(`LookAheadError`)만은 폴백하지 않고 그대로 터뜨립니다.** 다음 소스로 넘어가면
  버그가 정상 결과로 위장됩니다.

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

**봉의 소비는 실행이 성공한 뒤에만 확정합니다.** Fetcher가 봉을 읽는 시점에 바로 "봤다"고
기록하면, 하류의 알림 노드가 실패했을 때도 봉이 소비된 것으로 남아 **재실행하면 stale로
걸러지고 그 신호는 영영 사라집니다.** 그래서 Fetcher는 `bar_state.stage()`로 예약만 하고,
실행 엔진이 `RunStatus.SUCCESS`일 때만 `commit()`합니다(실패·`partial`이면 `discard()`).

**봉 상태는 SQLite에 남습니다** (`bar_state` 테이블 · Phase 1에서 추가). CLI로 전환하면서
프로세스가 매 실행마다 새로 뜨게 되어, 메모리 저장소로는 `last_seen`이 항상 비어 **게이트가
사실상 무동작**이었습니다. 실행마다 CLI가 저장소를 갈아 끼웁니다.

| 실행 | 저장소 |
| :--- | :--- |
| `--commit` | `SqlBarState` — 봉을 실제로 소비한다 |
| dry-run (DB 있음) | `SqlBarState(readonly)` — **읽기만** 한다 |
| dry-run (DB 없음) | `InMemoryBarState` — DB 파일조차 만들지 않는다 (12.1) |

dry-run도 **읽습니다.** 읽지 않으면 `run`과 `run --commit`이 서로 다른 종목 집합을 보게 되어
dry-run이 실제 실행을 예측하지 못합니다. 쓰지 않는 것과 읽지 않는 것은 다릅니다.

`bar_key`는 `(노드, 심볼, 타임프레임)`이라 파이프라인이 달라도 노드 id가 같으면 충돌하므로,
저장 시 `pipeline_id`로 한 겹 더 나눕니다.

- `partial`도 커밋하지 않습니다 — 실패한 노드가 하필 알림 노드였을 수 있습니다.
- 커밋을 미뤄서 생기는 최악은 **알림 중복**인데, 그건 `alerts_sent.dedup_key` UNIQUE가
  이미 막습니다(4.5). **유실을 막을 장치는 없으므로**, 겹치는 쪽이 잃는 쪽보다 안전합니다.

**Schedule Trigger 옵션**

| 옵션 | 동작 |
| :--- | :--- |
| `always` | 호출될 때마다 실행 (혼합 파이프라인 기본값 — Fresh Bar Gate가 걸러줌) |
| `market_open` | 지정 캘린더 장중에만 실행 |
| `after_close` | 장 마감 + N분 후 1회 (일봉 전략에 적합) |
| `on_bar_close` | 해당 타임프레임 봉 마감 + N초 후 |

### 3.6 타임프레임 — 일봉 고정 ★ (v0.5 변경)

**판단 단위는 `1d`와 `1w`뿐입니다.** v0.4는 "백테스트는 일봉 이상, 실매매 알림은 분봉 허용"으로 나눴지만, v0.5는 **분봉을 판단에서 통째로 제외**합니다.

**이유는 데이터가 아니라 비용 구조입니다.**

| 시장 | 왕복 비용 (수수료 + 세금 + 스프레드) |
| :--- | :--- |
| 업비트 | 약 0.15% 이상 |
| KRX | 약 0.25% 이상 (매도 시 거래세⚠️) |
| 미국 | 약 0.02~0.05% |

> ⚠️ 국내 증권거래세는 단계적 인하 중이라 시점에 따라 다릅니다. 착수 시 확인하세요.

하루 왕복 1회면 연 250회고, KRX 기준 **연 60% 이상을 비용으로 먼저 냅니다.** 그리고 그 구간의 상대는 호가창을 보는 참여자입니다. **못 이기는 게임이므로 안 하는 것이지, 분봉 이력을 못 구해서 미루는 것이 아닙니다.** 1.3의 비목표에 명시했습니다.

**세 가지 결정을 분리합니다.** 하나로 뭉치면 판단이 흐려집니다.

| | 결정 | v0.5 |
| :--- | :--- | :--- |
| **a** | 전략이 판단에 쓰는 타임프레임 | **`1d` / `1w`만** |
| **b** | Ingestion Worker가 수집·보관하는 것 | **일봉만.** 안 쌓으면 4.8의 "시간이 지나면 열린다"도 안 일어난다 — 의도적으로 닫는다 |
| **c** | 스케줄러 실행 빈도 | 시장별 마감 후, 하루 3회 수준 (코인 · KRX · US) |

**c 때문에 Fresh Bar Gate(3.5)는 그대로 필요합니다.** 미국장 마감 후 실행에서 한국 종목은 stale이기 때문입니다. 일봉이 되었다고 없애면 안 됩니다.

**되돌릴 수 있게 막습니다 — 타입 계층이 아니라 정책 계층에서.**

- ✅ `Item.timeframe: str` 유지, `ProviderCapabilities.timeframes` 유지, 아래 정규화 표 유지
- ✅ 파이프라인 검증기와 폼 선택지에서만 `1d` / `1w`로 제한
- ❌ `Literal["1d"]`로 타입을 굳히거나, 캘린더의 분봉 분기를 삭제하는 것

이렇게 두면 나중에 여는 것이 **정책 변경 + Provider 추가**지 재설계가 아닙니다.

**정규화·리샘플**

내부 표기는 `1m · 5m · 15m · 30m · 1h · 4h · 1d · 1w`로 통일하고 어댑터가 각 거래소 표기로 변환합니다(구조는 유지). `1w`는 **`1d`에서 리샘플**하므로 주봉 소스가 따로 필요 없습니다.

**일봉 마감 기준** — 주식은 캘린더의 장 마감, 코인은 파이프라인 설정 `daily_boundary`로 명시합니다.

⚠️ **KST 09:00은 UTC 00:00과 같은 순간입니다.** 업비트 일봉 기준(KST 09:00)이 곧 `UTC00`이며, 실제 선택지는 `UTC00`과 한국 자정(`KST00` = UTC 15:00) 둘입니다. **일봉이 유일한 판단 단위가 되면서 이 선택의 중요도가 올라갔습니다** — 코인 신호 전체가 이 경계에 좌우됩니다 (11장 참조).

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

- 수집 대상은 **활성 파이프라인이 참조하는 instrument의 합집합**에서 자동 도출한다.
- **일봉 전용이므로 저장소 논쟁이 끝났다 — SQLite로 확정.** 2,000종목 × 10년 일봉이면 약 500만 행으로
  SQLite가 여유롭게 감당한다. v0.4가 미결정으로 남겼던 Parquet/DuckDB 분리는 **분봉을 쌓을 때만
  발생하는 문제**였고(200종목 × 1분봉 × 3년 = 2억 행 이상), 3.6에서 분봉 수집을 닫으면서 사라졌다.
- **수집 주기가 하루 1회가 되면서 레이트 리밋 압박도 크게 줄었다.** "스크리너 200종목을 매 실행마다
  호출하면 무료 API가 차단된다"는 원래 동기는 남지만, 강도가 다르다.
- ★ **상장폐지·거래정지 종목도 수집 대상이다.** 살아 있는 종목만 쌓으면 4.8의 서바이버십 편향을
  데이터 레이어에서 이미 만들어 놓는 셈이 된다. `FdrProvider`의 상장폐지 목록을 기준으로 **폐지 시점까지의
  일봉을 확보하고 지우지 않는다.** ⚠️ 폐지 종목의 과거 가격을 어디까지 받아올 수 있는지는 소스별로
  확인이 필요하다 (11장).
- **노드는 캐시 구현을 모른다.** 노드가 보는 것은 "캐시를 읽는다"는 인터페이스뿐이므로 뒤를 교체할 수 있다.
  스키마를 SQLite 전용 문법으로 굳히지 않는다.
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
- ★ **`Bundle`은 곧 횡단면이다.** `items`가 여러 개인 상태가 예외가 아니라 **기본**이다(1.2 횡단면 우선).
  "한 시점의 유니버스 전체"가 하나의 `Bundle`이고, 여기에 순위·백분위를 매기는 것이 이 시스템의
  1급 연산이다. item을 하나씩 독립 처리하는 필터만 있으면 `len(items)`를 실제로 쓰는 곳이 없어지므로,
  **`rank` 계열 연산이 카탈로그의 중심에 있어야 한다**(5장).
- DataFrame은 프로세스 내 참조로 전달하고, 저장 시에는 요약(shape·마지막 행·해시)만 기록한다.
- **Bundle 안에서 item의 식별 키는 `(instrument.key, timeframe)`이다.** 종목만으로 식별하면
  Merge 노드가 같은 종목의 일봉 item과 시간봉 item 중 한쪽을 소리 없이 덮어쓴다. 이 키가
  "일봉 추세 + 시간봉 진입" 형태의 멀티 타임프레임 전략을 나중에 표현할 수 있게 하는 전제다
  (`Multi-Timeframe Join` 노드는 5장 참조).

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

#### 전략 인터페이스 ★ (v0.5 신설)

**v0.4는 전략을 지표 노드의 조합으로 표현했습니다. v0.5는 파이썬 클래스 하나로 표현합니다.**

노드 조합 방식은 "쓸 수 있는 모든 조건을 시스템이 미리 제공해야 한다"는 짐을 집니다. 지표 × 파라미터 × 조합은 끝이 없고, 실제로 같은 접근을 택한 도구들이 지표 7~10개 수준에서 멈춰 있습니다. 그리고 이 시스템의 사용자는 **파이썬을 쓰는 본인 한 명**입니다. 조건 세 개를 AND로 묶는 데 노드 4개와 핸들 연결이 필요할 이유가 없습니다.

```python
class Strategy(Protocol):
    id: ClassVar[str]                            # "krx_momentum_12_1"
    display_name: ClassVar[str]
    timeframe: ClassVar[Literal["1d", "1w"]]     # 3.6
    startup_candles: ClassVar[int]               # 지표 워밍업에 필요한 봉 수 → lookback 산출
    Params: ClassVar[type[BaseModel]]            # ★ 폼 자동 생성이 그대로 살아남는 지점

    def compute(self, item: Item, p: BaseModel, ctx: RunContext) -> Item: ...
    def rank(self, bundle: Bundle, p: BaseModel, ctx: RunContext) -> Bundle: ...
    def select(self, bundle: Bundle, p: BaseModel, ctx: RunContext) -> Bundle: ...
```

| 훅 | 축 | 역할 |
| :--- | :--- | :--- |
| `compute` | 시계열 | 종목별 지표를 `features`에 채운다. item을 버리지 않는다 |
| `rank` | **횡단면** | 유니버스 내 순위·백분위를 매긴다. **이 훅이 중심이다** |
| `select` | 횡단면 | 최종 컷 (상위 N, 임계값). 여기서만 item을 버린다 |

**규칙**

1. **`compute`는 인과적(causal)이어야 한다.** `rolling` · `ewm` · `shift(+n)`은 안전하고,
   **`shift(-n)` · `center=True` · `bfill`은 미래를 본다.** 런타임에 강제할 수 없는 지점이지만
   **`marketscan strategy check`가 AST로 상당 부분 잡아냅니다**(12장) — `shift(음수)`, `center=True`,
   `bfill`, `datetime.now`, 네트워크 라이브러리 임포트, `Params` 미선언. 정적 검사를 통과했다고
   인과성이 보장되는 것은 아니므로, 4.8의 난수 신호 테스트가 사후 방어선으로 남는다.
2. **전략에는 Provider·Cache 핸들을 주지 않는다.** 이미 `end`로 잘린 DataFrame만 받으므로
   데이터를 통한 미래 참조가 구조적으로 불가능하다. 멀티 타임프레임이 필요해지면 엔진이
   `Bundle`에 미리 채워 주는 형태로만 지원한다 (규칙 1 / 4.2 `ctx.now`).
3. **`Params`는 Pydantic 모델로 선언한다.** 노드 방식의 유일한 실질적 이득 —
   JSON Schema → 폼 자동 생성 — 이 그대로 유지된다. 코드를 고치지 않고 대시보드에서
   기간·컷 비율을 바꿀 수 있어야 한다.
4. **`rank` / `select`는 기본 구현을 제공한다.** 단일 종목 전략은 `compute`만 채우면 된다.

> **freqtrade 호환은 목표가 아니다.** `IStrategy`를 그대로 실행하려면 `self.dp`·`Trade`·
> hyperopt 파라미터·`@informative`까지 구현해야 하고, 그건 사실상 freqtrade를 임포트한다는
> 뜻이다. 게다가 freqtrade 전략의 값어치는 대부분 `minimal_roi`·`custom_stoploss` 같은
> **청산 로직**에 있는데 이 시스템에는 청산이 없다(1.3). 진입 로직만 떼면 원본과 다른 물건이므로,
> **호환을 흉내 내기보다 횡단면에 맞는 형태를 택한다.** freqtrade는 페어 단위 시계열 루프라
> `rank`를 애초에 표현할 수 없다.

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

SQLite + **WAL 모드**. SQLAlchemy를 써서 SQLite 전용 문법을 피합니다.

**v0.5에서 잠금 경합 문제가 사라졌습니다.** v0.4의 우려는 "스케줄러와 API가 동시에 쓴다"였는데, CLI로 전환하면서 **쓰는 프로세스가 한 번에 하나**가 되었습니다. 자동 실행이 겹칠 수 있으므로 WAL과 `busy_timeout`은 유지하되, 리스크 표(10장)에서는 강등합니다.

| 테이블 | 역할 |
| :--- | :--- |
| `pipelines` | 메타 (이름, 활성 버전, 활성화 여부) |
| `pipeline_versions` | DAG JSON 스냅샷 **(불변)**. 실행은 항상 특정 버전 참조 |
| `strategy_versions` | **전략 소스 해시 + 스냅샷**. 아래 참조 |
| `runs` | 실행 단위 (트리거·mode·시작/종료·상태) |
| `node_runs` | 노드별 입/출력 요약, 로그, 에러, duration |
| `signals` | 생성된 신호 (instrument, 방향, 근거 tags, as_of) |
| `alerts_sent` | 발송 알림 + `dedup_key` UNIQUE |
| `instruments` | 심볼 마스터 캐시 (자동완성용) |
| `market_calendar` | 휴장일·조기폐장 캐시 |
| `connections` | 소스별 사용자 API 키 (암호화). provider_id + 라벨 + 자격증명 |
| `source_routes` | `(venue, timeframe) → 우선순위 소스 목록` 라우팅 표 |
| `llm_cache` | 프롬프트 해시 → 응답. **`deterministic=false` provider의 응답은 캐시하지 않는다** (5장) |
| `backtest_runs` | **전략 해시 × 파라미터 조합 × 실행 시각.** 다중검정 카운터의 근거 (4.8) |
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

**버전 불변성**: 실행 중 파이프라인을 편집해도 진행 중인 Run은 영향받지 않습니다. 저장 시 새 버전을 만들고 Run은 `pipeline_version_id`를 고정합니다.

**★ 전략 코드도 버전에 묶어야 합니다 (v0.5 신설)**

전략이 파이썬 클래스가 되면서 새로 생긴 구멍입니다. 파이프라인이 `strategies/momentum.py`를 **이름으로만** 참조하면, 그 파일을 고치는 순간 **과거 버전이 무엇이었는지가 소급으로 바뀝니다.** "그때 그 신호가 어떤 전략에서 나왔는지"를 잃는 것이고, `pipeline_versions`를 불변으로 둔 이유가 그대로 무너집니다.

- 전략의 **정본은 `strategies/` 디렉터리의 파일**로 둔다. IDE·git·리뷰를 쓸 수 있는 쪽이 실사용에 낫다.
- 저장 시 소스 텍스트의 **SHA-256을 `pipeline_versions`에 함께 기록**하고, 전문을 `strategy_versions`에 스냅샷한다.
- 실행·리플레이 시점에 **해시가 다르면 경고를 남긴다.** 백테스트 결과 리포트에도 표시한다 —
  "이 리포트는 현재 코드가 아니라 버전 N의 코드로 계산됨"을 알 수 있어야 한다.
- 웹 에디터에서 코드를 편집하게 만들면 버전 불변성이 공짜로 해결되지만, 편집 경험을 잃는다.
  **파일 + 해시 기록**을 택한다.

### 4.8 백테스트

**★ 용도를 먼저 못박습니다 (v0.5 신설)**

> **"이 전략이 돈이 되나?" (❌) → "내 구현이 안 틀렸나?" (✅)**

백테스트를 전략 탐색에 쓰면 집니다. 탐색 공간은 지표 × 파라미터 × 조합으로 수백만인데 일봉 10년은 2,500행이라, **우연히 잘 맞는 조합이 반드시 나오고 그것을 잡음과 구분할 표본이 없습니다.** 파라미터를 튜닝하는 순간 그 성과는 in-sample이 되어 의미를 잃습니다.

대신 **이미 공개되어 있고 오래 검증된 팩터를 표준값 그대로** 쓰고(모멘텀 12-1, 밸류 PBR/PER/EV-EBIT, 퀄리티 ROE·부채비율, 저변동성), 백테스트는 **수정주가 처리·상장폐지 반영·미래 참조 여부를 확인하는 디버깅 도구**로 씁니다. 남의 논문 값을 쓰면 그것은 나에게 out-of-sample이지만, 내가 고른 값은 아닙니다. 1.3의 비목표에 명시했습니다.

**리플레이** — 동일 코드를 시각만 바꿔 재생합니다.

```python
for bar_time in calendar.bars(start, end, timeframe):   # timeframe ∈ {1d, 1w}
    ctx = RunContext(mode="backtest", now=bar_time, ...)
    await engine.execute(pipeline_version, ctx)
```

| 이슈 | 대응 |
| :--- | :--- |
| **미래 참조 (look-ahead)** | Provider는 `end` 이후 캔들을 절대 반환하지 않는다. backtest 모드에서 assert로 강제 |
| **LLM 비용/비결정성** | `(model, prompt_hash, input_digest)` 키로 `llm_cache` 저장 → 재실행 무료·결정적. `temperature=0` 권장. 캐시 미스 정책: 호출 / 스킵 / 중단 |
| **LLM 학습 데이터 누출** | 캐시로는 못 막는 별개 문제. 아래 참조 |
| **유니버스 서바이버십** | 유니버스를 현재 시점으로 산출하면 상폐·편출 종목이 통째로 빠진다. 아래 참조 |
| **시장별 캘린더** | 백테스트 루프도 캘린더 기준으로 봉을 생성 (휴장일 건너뜀) |
| **체결 가정 (Phase 5)** | 다음 봉 시가 체결 기본, 슬리피지·수수료·세금(국내 거래세) 파라미터화 |
| **성과 지표** | 아래 "신호 품질 지표" 참조. **수익률 계열 지표는 계산하지 않는다** |

**성과 지표 — 수익률이 아니라 신호 품질을 측정한다**

이 시스템에는 **청산(exit) 개념이 없습니다.** 진입 신호만 있는 상태에서 총수익률·MDD·샤프를
계산하려면 청산 규칙을 가정해야 하고, 그 순간 체결 가정·수수료·세금이 줄줄이 딸려 와
Phase 5(실주문)를 앞으로 끌어옵니다. 알림 전용 시스템의 목적과도 맞지 않습니다.

대신 **신호 이후 무슨 일이 일어났는지**를 청산 가정 없이 측정합니다. `runs`에 요약 저장:

| 지표 | 정의 |
| :--- | :--- |
| **Forward return 분포** | 신호 N봉 후 수익률의 median·IQR (N은 복수로 산출: 1·5·20) |
| **Hit rate** | N봉 후 수익률이 양수인 비율 |
| **IC (Information Coefficient)** | 신호 점수 ↔ 후속 수익률의 순위상관. 점수를 내는 노드(LLM 등)가 있을 때만 |
| **벤치마크 대비 초과수익** | 같은 구간의 KOSPI / S&P500 / BTC 대비 |
| **신호 건수·종목 분산** | 알림 폭주와 특정 종목 편중을 잡는다 |
| ★ **오버라이드 성과** | **사용자가 무시한 신호**의 사후 수익률. 아래 참조 |

`Forward Return Evaluator` 노드가 `signals` 기록 후 N봉 뒤 수익률을 소급 채웁니다(5장).
Phase 5에서 실주문과 청산이 들어오면 그때 수익률 지표를 **추가**합니다 — 지금 넣지 않습니다.

**★ 오버라이드 추적 (v0.5 신설)**

정체성이 "주의력 기계 + 규율 기계"(1.2)라면, **측정해야 할 것은 전략 성과만이 아니라 사용자가 규율을 지켰는지**입니다. `signals`에 `acted: bool | null` 한 컬럼을 두고, 알림 메시지에서 "실행함 / 무시함"을 기록할 수 있게 합니다.

| 결과 | 해석 |
| :--- | :--- |
| 무시한 신호가 평균적으로 **나빴다** | 사용자의 재량에 값이 있다. 시스템을 후보 필터로 쓴다 |
| 무시한 신호가 평균적으로 **좋았다** | 재량이 손해다. 시스템을 더 믿거나, 왜 못 믿는지를 규칙으로 바꿔야 한다 |

`signals`와 Forward Return Evaluator가 이미 산출원을 만들어 두므로 **컬럼 하나와 알림의 응답 버튼이면 끝납니다.** 이 시스템에서 가장 확실한 가치가 여기서 나옵니다 — 개인 투자자가 이 숫자를 보는 일이 거의 없기 때문입니다.

**⚠️ LLM 노드가 있는 파이프라인의 백테스트는 낙관 편향된다**

`llm_cache`는 **재실행 결정성**을 보장할 뿐, 학습 데이터 누출은 막지 못합니다. 2023년 시점
캔들을 2026년 모델에게 물으면, 그 모델은 이미 해당 종목의 후속 주가를 학습했을 수 있습니다.
Provider의 `end` 컷과 look-ahead assert는 **가격 데이터만** 보므로 이걸 못 잡습니다.

- LLM 노드가 포함된 백테스트 결과에는 **경고 배지**를 붙이고 리포트에 낙관 편향 가능성을 명시한다.
- **LLM 전략의 1급 검증 경로는 `shadow` 모드다.** 백테스트는 참고 수단으로 격하한다.

**⚠️ 유니버스는 백테스트에서 point-in-time으로 산출한다**

`Symbol Universe`(5장)의 "시총 상위 N"을 현재 시점 기준으로 뽑으면, 그 구간에 상장폐지되거나
지수에서 편출된 종목이 통째로 빠져 성과가 부풀려집니다. 가격 데이터만 보는 look-ahead assert로는
잡히지 않습니다.

- 백테스트 모드에서 유니버스는 **각 `bar_time` 기준으로** 산출한다 (PyKRX는 날짜별 시총 조회 가능).
- point-in-time 산출이 불가능한 소스는 백테스트 모드에서 **사유를 명시하고 거부**한다.
  고정 목록은 예외 — 사용자가 직접 적은 것이므로.
- `Item.meta`에 유니버스 산출 기준일을 남긴다.

**커버리지 게이트** (v0.5에서 축소)

백테스트 시작 전 `ohlcv_cache` 커버리지를 확인해 요청 구간을 채울 수 없으면 **명확한 사유와 함께 거부**합니다.

```
요청: krx:005930 · 1d · 2016-01-01 ~ 2026-01-01
결과: ❌ 거부 — 일봉 커버리지 2021-04-12 이후 (요청 구간의 47%)
      제안: 시작일을 2021-04-12 이후로 하거나, 수집을 먼저 돌릴 것
```

v0.4에서 이 게이트의 주 역할은 "분봉 백테스트를 데이터가 쌓이면 자동으로 열어 주는 것"이었지만, 3.6에서 분봉을 닫으면서 **일봉 이력이 실제로 있는지 확인하는 단순한 사전 점검**으로 축소됩니다. 무인증 소스로 수년치 일봉을 받을 수 있으므로 대개 통과합니다.

**★ 성능 — 피처 행렬을 미리 계산한다 (v0.5 신설)**

위 리플레이 루프를 순진하게 구현하면 **매 봉마다 지표를 처음부터 다시 계산**합니다.

```
2,000종목 × 2,500일 = 500만 회 × (200봉 슬라이스 + 지표 계산) → 수 시간
```

백테스트 한 번에 반나절이 걸리면 아무도 쓰지 않습니다. 해법은 **전 구간 피처를 벡터 연산으로 한 번만 계산해 두고, 리플레이는 그 행렬의 행을 읽기만 하는 것**입니다.

```
features[종목 × 날짜 × 피처]   ← groupby(종목).rolling() 등으로 일괄 계산 (수십 초)
리플레이는 features.loc[bar_time] 한 줄을 꺼내 쓴다
```

- **전제는 `compute`의 인과성입니다** (4.2 규칙 1). `shift(-n)` 하나가 섞이면 전체가 조용히 무너집니다.
- 대신 **look-ahead 위험이 피처 계산 한 곳에 갇힙니다.** 감사할 지점이 하나면 지킬 수 있습니다.
- 일봉 + 횡단면이면 어차피 "한 날짜의 전 종목 단면"이 필요하므로 **이 행렬이 원래 필요한 자료 구조**입니다.
- ⚠️ **계약을 바꿔서 속도를 얻지 않습니다.** 전략에 전체 DataFrame을 넘겨 주는 식의 최적화는
  미래 참조를 다시 열어 줍니다. 느리면 캐싱으로 풉니다.

**★ 엔진 자체를 검증하는 법 (v0.5 신설)**

백테스트 엔진에는 **정답을 알려 줄 오라클이 없습니다.** 결과가 그럴듯하면 맞는 줄 압니다. 그래서 아래를 회귀 테스트로 박아 둡니다. 전략 성과가 아니라 **엔진의 정직성**을 재는 테스트입니다.

| 테스트 | 기대 | 깨지면 |
| :--- | :--- | :--- |
| **난수 신호** | hit rate가 유니버스 기저율과 일치 | 70%가 나오면 **미래 참조가 있다.** 가장 값싸고 강력한 방어선 |
| **전량 매수** | forward return이 유니버스 평균 수익률과 일치 | 수익률 계산·정렬·조인 버그 |
| **신호 1일 밀기** | 성과가 기저율 쪽으로 떨어짐 | 안 떨어지면 신호가 무의미하거나 엔진이 새고 있음 |
| **상장폐지 포함 여부** | 폐지 종목이 유니버스에 등장 | 서바이버십 편향이 데이터 레이어에 있음 (3.9) |

**★ 다중검정 카운터 — 과적합을 기계가 세게 한다 (v0.5 신설)**

용도를 "구현 검증"으로 선언해도(위), 실제로는 파라미터를 조금씩 바꿔 가며 다시 돌리게 됩니다. **특히 LLM에게 CLI를 주면 파라미터 200조합을 순식간에 돌려 보고 제일 좋은 것을 추천합니다** — 1.3에서 비목표로 선언한 바로 그 행동을 사람보다 1000배 빠르게 합니다.

사람은 47번 돌린 것을 잊지만 카운터는 잊지 않습니다. `backtest_runs`에 기록하고 **매번 출력**합니다.

```
⚠️  이 전략에 대한 47번째 백테스트입니다 (최초 2026-07-14).
    파라미터를 12회 변경했습니다. 이 시점의 성과는 사실상 in-sample입니다.
    → 검증된 팩터의 표준값을 쓰고 있는지 확인하세요.
```

- 실행 횟수와 **파라미터 변경 횟수를 구분해서** 셉니다. 같은 파라미터를 재실행하는 것은 무해합니다.
- 임계를 넘으면 `--i-know-this-is-in-sample` 없이 거부합니다.
- 백테스트 리포트 상단에도 같은 경고를 박습니다. **나중에 그 리포트를 다시 볼 때 맥락이 남아야 합니다.**

> 이건 LLM 호출을 허용했기 때문에 **오히려 가능해진 안전장치**입니다. 카운터가 출력되면
> 에이전트가 그것을 읽고 스스로 멈출 근거가 생깁니다.

**shadow 모드 — 존재 이유의 재정의** (v0.5 변경)

v0.4에서 shadow는 "분봉은 백테스트를 못 하니 대신 실시간으로 검증한다"는 용도였습니다. 분봉이 사라지면서 그 이유는 없어졌지만, **shadow는 남습니다. 이유가 바뀐 것뿐입니다.**

파이프라인을 실시간으로 돌리되 **알림을 보내지 않고 `signals`에만 기록**합니다. 이제 주 용도는:

1. **LLM 노드의 1급 검증 경로.** 위에서 말한 학습 데이터 누출은 백테스트로 못 잡고 shadow로만 잡힙니다.
2. **실전 투입 전 관찰 기간.** 몇 달 돌려 신호 건수·종목 분산·forward return을 확인한 뒤 알림을 켭니다.
3. **구현 검증.** 백테스트와 shadow의 신호가 어긋나면 둘 중 하나가 틀린 것입니다.

### 4.9 관측성

- 실행 진행은 **stdout으로 출력**한다 (`--json`이면 stderr). 자동 실행의 로그에 그대로 남는다.
  하루 3회 도는 배치에 실시간 스트리밍은 필요 없다.
- `node_runs`의 입출력 스냅샷으로 **"왜 이 신호가 나왔는가"를 사후 재현**.
- **전략 노드는 `rank` 결과 상위 N개의 점수·순위를 `node_runs`에 남긴다.** 전략이 한 덩어리가 되면서
  중간 판단이 노드 경계에 드러나지 않으므로, 이 스냅샷이 없으면 "왜 이 종목이 뽑혔는가"를 잃는다.
- 구조화 로그(JSON) + `run_id` / `node_id` 상관 필드.

---

## 5. 노드 카탈로그 (v0.5 전면 개정)

**Indicator 범주가 통째로 사라지고 Strategy 범주가 들어옵니다.** 노드는 이제 "전략을 조립하는 블록"이 아니라 **"데이터 → 전략 → LLM → 알림"을 잇는 배선**입니다.

| 범주 | 노드 | 입력 → 출력 | 주요 파라미터 |
| :--- | :--- | :--- | :--- |
| **Trigger** | Schedule Trigger | — → `main` | cron 식, 실행 조건(always / after_close), 지연. ⚠️ 이 노드를 **누가 읽는지**는 11장 4b에 달려 있다 — OS 스케줄러면 문서로만 남고, `serve`면 실제 실행 주체가 된다 |
| | Manual Trigger | — → `main` | 테스트·디버깅용 |
| **Input** | Symbol Universe | — → `main` | 고정 목록 / 거래소 조회(거래대금 상위 N) (venue 혼합 가능). ★ **동적 유니버스는 backtest 모드에서 하드 차단** — 아래 참조 |
| | Market Data Fetcher | `main` → `main` | timeframe(`1d`/`1w`), lookback, closed_only, **skip_stale**, **source**(auto / 연결 ID) |
| **Strategy** ★ | **Strategy Runner** | `main` → `main` | `strategy_id` + 전략의 `Params`(폼 자동 생성). `compute` → `rank` → `select`를 순서대로 실행 (4.2) |
| **AI** | **LLM Screen** | `main` → `main` | provider, model, 프롬프트 템플릿, 출력 스키마, 캐시 정책. **가격 예측이 아니라 정성 정보 필터** — 아래 참조 |
| **Logic** | Condition Splitter | `main` → `true` / `false` | 조건식 |
| | Merge | `a`, `b` → `main` | union / intersection / append |
| | **Rank / Percentile** | `main` → `main` | 유니버스 내 상대 순위·백분위를 `features`에 기록. 전략 밖에서 여러 전략의 점수를 합칠 때 |
| | Sort / Limit | `main` → `main` | 점수 상위 N개만 통과 (알림 폭주 방지) |
| | **Alert Cooldown** | `main` → `main` | 종목당 재알림 금지 기간 |
| **Action** | Telegram Alert ⚠️`serve` | `main` → `main` | credential_id, chat_id, 템플릿. **`sends_external_messages = True`라 `run`에서는 실행되지 않는다** (12.2) |
| | Log Alert | `main` → `main` | 신호를 실행 로그의 한 줄로 남긴다. 바깥으로 나가지 않는다 |
| | Persist Signal | `main` → `main` | `signals` 테이블 기록 |
| | **Forward Return Evaluator** | `main` → `main` | `signals` 기록 후 N봉 뒤 수익률을 소급 채운다. 4.8 신호 품질 지표의 산출원 |
| | *Broker Order* | `main` → `main` | **Phase 5**. RiskGuard 필수 |

**사라진 노드와 그 이유**

| 노드 | 처분 |
| :--- | :--- |
| MA / Bollinger / RSI / MACD Filter | **삭제.** 전략 클래스의 `compute` 안으로 흡수 (4.2) |
| Custom Expression | **삭제.** v0.4는 이 노드의 사용 비중을 "노드 UI의 전제가 무너지는 계기판"으로 삼았는데, **그 계기판이 착수 전에 켜졌다.** 노드를 더 만드는 대신 표현 방식을 바꾼 것이 v0.5다 |
| LLM Decision | **LLM Screen으로 개명·축소.** 아래 참조 |

> 기존 `maFilter` 구현은 **지우지 않되 동결합니다.** 단순한 단일 종목 조건의 예시로 값이 있고,
> `Bundle` 계약의 참조 구현이기도 합니다. 다만 **Indicator 범주에 새 노드를 추가하지 않습니다** —
> 이 선을 명시적으로 긋지 않으면 두 방식이 공존하다 노드 쪽이 썩습니다.

**Symbol Universe — AST 검사가 잡지 못하는 look-ahead** ★ (Phase 1에서 확정)

거래소가 주는 종목 목록은 언제나 **"지금"** 입니다. 거래대금 상위 30개를 오늘 뽑아 2년치를
리플레이하면, 2년 전에는 알 수 없었던 정보로 종목을 고른 것이 됩니다 — 2년간 살아남아 상위에
든 종목만 보게 되므로 성과가 구조적으로 부풀려집니다(4.8 서바이버십).

**이 경로는 `strategy check`가 잡지 못합니다.** 전략 코드는 완전히 인과적이고 미래 참조는
유니버스 쪽에 있기 때문입니다. 규칙 3(`shift(-n)` 금지)이 겨누는 것과 같은 사고인데 AST에
흔적이 남지 않으므로, **차단을 노드가 명시적으로 맡습니다.**

- `venue`가 지정된(= 거래소를 조회하는) Symbol Universe는 `backtest` 모드에서 **거부**합니다.
- **조용히 고정 목록으로 물러서지 않습니다.** 그러면 사용자가 적지 않은 유니버스로 백테스트가
  돌아가고, 그 사실이 어디에도 남지 않습니다.
- 산출 근거(`venue` · `top_by_turnover` · `point_in_time`)를 `Bundle.context`에 실어
  `node_runs`에 남깁니다 — "그날 왜 이 종목들이었나"가 사후에 복원되어야 합니다.
- Phase 3에서 point-in-time 스냅샷이 생기면 그때 backtest 경로가 열립니다.

산출물은 items가 아니라 **`context["universe"]`의 심볼 목록**입니다. 봉을 받기 전이라
`Item`을 만들 수 없습니다 — `Item.as_of`는 "마감된 캔들의 종료 시각"인데(4.1) 그 값은 Market
Data가 캘린더로 판정하기 전까지 존재하지 않습니다. 없는 `as_of`를 지어내면 그 거짓말이
신호까지 따라갑니다.

**LLM Screen — 가격 예측기가 아니다** ★

LLM이 캔들을 보고 가격을 예측한다는 근거는 없고, 4.8의 학습 데이터 누출 때문에 검증도 불가능합니다. **그러나 정성 정보 필터로는 다릅니다.**

숫자 스크리닝의 최대 위험은 **"지표는 좋은데 사실 위험한 회사"** 입니다. PBR 0.3에 ROE 15%인데 관리종목이거나, 감사의견 한정이거나, 유상증자를 앞두고 있거나, 횡령·배임 공시가 떴거나. 이건 가격 데이터로 못 거르고, 사람이 매일 수백 종목의 공시를 읽을 수도 없습니다.

```
숫자 스크리너 → 후보 30종목 → LLM이 공시·뉴스를 읽고 지뢰 제거 → 5종목 → 사람 검토
```

- **수익 기여 경로가 알파 생성이 아니라 손실 회피입니다.** 소형주 스크리닝에서는 이쪽이 더 큽니다.
- **비용상 반드시 필터 뒤에 배치합니다** (10장 리스크 표).
- ★ **판단 보류를 1급 출력으로 둡니다.** 출력 스키마에 `abstain: bool` 또는 `confidence`를 넣고,
  임계 미달이면 신호를 죽이는 것을 **기본값**으로 합니다. FreqAI가 예측값과 함께 `do_predict`·
  DI(Dissimilarity Index)를 내보내 "이 입력은 학습 분포 밖이니 믿지 말라"고 알리는 것과 같은 장치입니다.
  점수만 받고 신뢰도를 안 받으면 모델이 헛소리를 하는 중인지 알 방법이 없습니다.

**LLM Provider의 세 종류 — 결정성 보증이 다르다** ★ (v0.5 신설)

호출 방식을 API로 한정하지 않습니다. **로컬에 인증된 agent 커맨드를 호출하는 방식**이 이 용도에 특히 잘 맞습니다.

```python
class LlmProvider(Protocol):
    id: str
    deterministic: bool     # False면 backtest 모드에서 거부
    cacheable: bool         # False면 llm_cache를 쓰지 않는다
    async def complete(self, prompt: str, schema: type[BaseModel], ctx: RunContext) -> BaseModel: ...
```

| 구현 | 예 | `deterministic` | 백테스트 | 비고 |
| :--- | :--- | :---: | :---: | :--- |
| `ApiProvider` | Anthropic · OpenAI (`temperature=0`) | ✅ | 허용\* | 구조화 출력을 스키마로 강제할 수 있다 |
| `CommandProvider` | `claude -p …` · `codex` — **도구 사용 끔** | ✅ | 허용\* | **API 키 불필요** — 로컬 인증을 그대로 씀 |
| `CommandProvider` | 위와 같으나 **도구 사용 켬** | ❌ | **거부** | 공시·뉴스를 직접 찾아 읽는다. 가장 강력하지만 아래 참조 |
| `LocalModelProvider` | ollama 등 | ✅ | 허용\* | 무료·비공개·레이트리밋 없음 |

\* 학습 데이터 누출 경고 배지는 그대로 유지됩니다 (4.8).

**agent 호출을 넣는 이유**

- **비밀이 하나 더 사라집니다.** 시세는 이미 무인증이므로(3.3), 남는 것은 **텔레그램 토큰 하나**뿐입니다.
- **agent는 컨텍스트를 스스로 가져옵니다.** 이 노드의 용도가 공시·뉴스 읽기인데,
  미리 채워 넣은 프롬프트를 받는 API 호출보다 직접 찾아 읽는 쪽이 명백히 유리합니다.

**대신 두 가지를 강제합니다**

1. ⚠️ **백테스트에서 도구 사용 agent는 하드 차단.** 2023년 `bar_time`으로 리플레이하는데 agent가
   실시간 웹을 읽으면 **2026년 정보를 봅니다.** 4.8이 경고한 학습 데이터 누출보다 나쁩니다 —
   그건 기억이지만 이건 실제 미래 데이터입니다. `deterministic=False`면 backtest 모드에서 거부합니다.
2. ⚠️ **`cacheable=False`면 `llm_cache`를 쓰지 않습니다.** 캐시 키는 `(model, prompt_hash, input_digest)`인데
   agent가 웹을 뒤지면 **실제 입력이 프롬프트 해시에 안 잡힙니다.** 그대로 캐시하면 낡은 답을
   정답인 척 돌려주게 됩니다.

**구조화 출력**은 API처럼 스키마로 강제할 수 없으므로 텍스트를 Pydantic으로 검증하고, 실패하면 1회 재시도 후 **`abstain`으로 떨굽니다.** 위에서 설계한 판단 보류 장치가 파싱 실패까지 그대로 받아냅니다.

**보안** — 공시 텍스트를 프롬프트에 넣어 셸을 호출하므로, **문자열 보간이 아니라 argv 배열이나 stdin으로 전달**합니다.

**검토했으나 아직 넣지 않은 노드**

| 노드 | 상태 |
| :--- | :--- |
| `Multi-Timeframe Join` | 4.1에서 item 키를 `(instrument, timeframe)`으로 확정해 표현 가능한 상태. 다만 **일봉·주봉만 남으면서 우선순위가 크게 낮아졌다.** 남은 결정은 features 네임스페이스 규약(`w1.sma_20` 형태) |
| `Previous Signal Lookup` | "지난 신호 이후 N일 경과" — Cooldown의 일반형. 필요해지면 추가 |

> ⚠️ **새 계기판: `Strategy Runner`가 아닌 노드의 개수.** v0.4는 `Custom Expression` 사용 비중을
> 봤습니다. v0.5에서 볼 것은 반대입니다 — **배선용 노드가 계속 늘어난다면** 파이프라인이 다시
> 전략을 표현하려 들고 있다는 뜻입니다. 전략 로직은 전략 클래스 안에 있어야 합니다.

---

## 6. DAG JSON 스키마 (개정)

v0.4 대비: 지표 노드 체인이 **`strategyRunner` 하나**로 접혔고, 전략 소스 해시가 버전에 박히며(4.7), `position`이 사라졌습니다 — 캔버스가 없으므로 좌표를 저장할 이유가 없습니다.

> ⚠️ **이 형식 자체가 미결정입니다** (11장 4번). 아래를 보면 그래프가 거의 직선인데, 손으로 JSON을
> 적는 것은 고통스럽습니다. `marketscan.toml`로 평탄하게 적고 로더가 DAG로 변환하는 쪽이 나을 수
> 있습니다 — **엔진은 그대로 두고 입력 형식만 바꾸는 것**이므로 `runner.py`는 손대지 않습니다.
> Phase 1에서 실제로 적어 본 뒤 판단합니다.

```json
{
  "pipeline_id": "pipe_multimarket_v1",
  "version": 4,
  "name": "멀티마켓 모멘텀 스크리너 + 공시 필터",
  "settings": {
    "user_timezone": "Asia/Seoul",
    "default_mode": "shadow",
    "daily_boundary": "UTC00",
    "adjusted": true,
    "max_concurrency": 4
  },
  "nodes": [
    {
      "id": "node_0",
      "type": "scheduleTrigger",
      "params": { "cron": "0 7,16,22 * * *", "run_when": "always", "delay_seconds": 600 }
    },
    {
      "id": "node_1",
      "type": "symbolUniverse",
      "params": {
        "sources": [
          { "venue": "krx",    "rule": "market_cap_top", "n": 500 },
          { "venue": "nasdaq", "rule": "market_cap_top", "n": 500 },
          { "venue": "upbit",  "rule": "fixed", "symbols": ["KRW-BTC", "KRW-ETH"] }
        ],
        "point_in_time": true
      }
    },
    {
      "id": "node_2",
      "type": "marketData",
      "params": {
        "timeframe": "1d",
        "lookback": 300,
        "closed_only": true,
        "skip_stale": true,
        "source": "auto"
      },
      "on_error": { "policy": "retry", "max_attempts": 3, "fallback": "route" }
    },
    {
      "id": "node_3",
      "type": "strategyRunner",
      "params": {
        "strategy_id": "cross_momentum_12_1",
        "strategy_sha256": "9f2c…",
        "params": { "lookback_months": 12, "skip_months": 1, "top_pct": 0.05 }
      }
    },
    {
      "id": "node_4",
      "type": "llmScreen",
      "params": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "credential_id": "cred_llm",
        "prompt": "다음 종목의 최근 공시와 뉴스를 보고 매수 후보에서 제외할 사유가 있는지 판단하라.\n제외 사유 예: 관리종목, 감사의견 비적정, 유상증자 예정, 횡령·배임, 상장폐지 심사.\n종목: {{instrument.display_name}} ({{instrument.venue}})\n최근 공시: {{context.disclosures}}",
        "output_schema": {
          "exclude": "bool",
          "reason": "string",
          "confidence": "float(0..1)",
          "abstain": "bool"
        },
        "abstain_policy": "exclude",
        "temperature": 0,
        "cache": "always"
      },
      "on_error": { "policy": "route" }
    },
    {
      "id": "node_5",
      "type": "conditionSplitter",
      "params": { "expression": "not tags.exclude and tags.confidence >= 0.6" }
    },
    {
      "id": "node_6",
      "type": "alertCooldown",
      "params": { "per_instrument_hours": 168 }
    },
    {
      "id": "node_7",
      "type": "persistSignal",
      "params": {}
    },
    {
      "id": "node_8",
      "type": "actionTelegram",
      "params": {
        "credential_id": "cred_telegram",
        "chat_id": "@my_signal_channel",
        "template": "[{{instrument.venue}}] {{instrument.display_name}}\n유니버스 순위: {{features.rank}}/{{features.universe_size}} (상위 {{features.percentile}}%)\n현재가: {{features.close}} {{instrument.quote_currency}}\n공시 점검: {{tags.reason}}"
      }
    },
    {
      "id": "node_9",
      "type": "actionTelegram",
      "params": { "credential_id": "cred_telegram", "chat_id": "@ops_alerts",
                  "template": "[오류] {{node_id}}: {{error.message}}" }
    }
  ],
  "edges": [
    { "id": "e0", "source": "node_0", "source_handle": "main",  "target": "node_1", "target_handle": "main" },
    { "id": "e1", "source": "node_1", "source_handle": "main",  "target": "node_2", "target_handle": "main" },
    { "id": "e2", "source": "node_2", "source_handle": "main",  "target": "node_3", "target_handle": "main" },
    { "id": "e3", "source": "node_3", "source_handle": "main",  "target": "node_4", "target_handle": "main" },
    { "id": "e4", "source": "node_4", "source_handle": "main",  "target": "node_5", "target_handle": "main" },
    { "id": "e5", "source": "node_5", "source_handle": "true",  "target": "node_6", "target_handle": "main" },
    { "id": "e6", "source": "node_6", "source_handle": "main",  "target": "node_7", "target_handle": "main" },
    { "id": "e7", "source": "node_7", "source_handle": "main",  "target": "node_8", "target_handle": "main" },
    { "id": "e8", "source": "node_2", "source_handle": "error", "target": "node_9", "target_handle": "main" },
    { "id": "e9", "source": "node_4", "source_handle": "error", "target": "node_9", "target_handle": "main" }
  ]
}
```

**이 예시가 보여주는 것** — 그래프가 거의 직선입니다. 분기는 LLM 판정과 에러 라우팅뿐입니다. **이것이 v0.5에서 캔버스를 버린 이유이자, DAG 엔진을 남겨 둔 이유이기도 합니다** (분기와 팬아웃은 여전히 그래프이므로).

---

## 7. 디렉터리 구조 (제안)

**`backend/` 와 `frontend/` 구분이 사라집니다.** 프론트가 없으면 "백엔드"라는 이름도 의미가 없으므로 최상위로 폅니다.

```
marketscan/
├── docs/trading_bot_node_handoff.md
├── ARCHITECTURE.md
├── pyproject.toml               # [project.scripts] marketscan = "app.cli:main"
├── uv.lock                      # ★ Docker를 대신하는 재현성 장치
├── marketscan.toml               # 파이프라인 정의 (6장) · run이 참조
├── .env                         # 텔레그램 토큰 등. 남은 비밀은 매우 적다
├── data/                        # SQLite. 백업 대상
├── reports/                     # ★ 백테스트·실행 리포트 정적 HTML 산출물
├── strategies/                  # ★ 사용자 전략 파일 (정본). git 관리, 해시가 버전에 박힘
│   ├── cross_momentum_12_1.py
│   └── krx_value_quality.py
├── app/
│   │   ├── cli/                 # ★ Typer 명령 정의 (12장)
│   │   │   ├── main.py          # run · ingest · backtest · explain · signals · stats …
│   │   │   └── output.py        # --json 직렬화 · 종료 코드 · 출력 크기 제한
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
│   │   │   ├── registry.py      # 플러그인 등록 · 라우팅 · 폴백 (거래소 인스턴스 싱글턴)
│   │   │   ├── ccxt_base.py     # CcxtProvider — 코인 거래소 전부
│   │   │   ├── ccxt_quirks/     # upbit.py · bithumb.py — 거래소별 예외만
│   │   │   ├── toss.py  kis.py
│   │   │   ├── pykrx.py  yfinance.py  alpaca.py  fdr.py
│   │   │   └── llm/             # anthropic.py · openai.py
│   │   ├── nodes/               # ★ 배선용 노드만. indicators/ 는 삭제
│   │   │   ├── registry.py
│   │   │   └── triggers/ inputs/ strategy/ ai/ logic/ actions/
│   │   ├── strategies/          # ★ 전략 프로토콜 · 로더 · 소스 해시
│   │   │   ├── base.py          # Strategy Protocol (4.2)
│   │   │   ├── registry.py      # strategies/ 디렉터리 스캔 · 해시 계산
│   │   │   └── features.py      # 피처 행렬 사전 계산 (4.8 성능)
│   │   ├── backtest/            # replay.py · metrics.py · sanity.py(난수 신호 테스트)
│   │   ├── storage/             # SQLAlchemy 모델 · 리포지토리
│   │   ├── report/              # ★ 정적 HTML 생성 (uplot 인라인)
│   │   ├── risk/                # Phase 5
│   │   └── core/                # config · crypto · logging
└── tests/
```

**삭제되는 것** — `frontend/` 전체, `backend/` 계층, `docker-compose.yml`, `Dockerfile`, `app/main.py`(FastAPI), `app/api/`, `app/scheduler/`, `app/web/`. Node·pnpm·Vite·TypeScript 툴체인도 함께 사라집니다.

**`app/strategies/`(프레임워크)와 최상위 `strategies/`(사용자 전략)를 구분합니다.** 전자는 로더·프로토콜이고 후자는 데이터에 가깝습니다. 후자는 git으로 관리하고, 소스 해시가 파이프라인 버전에 박힙니다(4.7).

**`data/` · `reports/` · `strategies/` 세 디렉터리만 사용자 자산입니다.** 백업 대상이자, 코드를 지우고 다시 받아도 살아남아야 하는 것들입니다.

---

## 8. 설치와 운용 (v0.5 전면 개정)

**Docker를 쓰지 않습니다.** `docker run --rm -v …`를 하루 3번 호출하는 구조는 얻는 것보다 마찰이 큽니다 — 볼륨 3개(`data/` · `strategies/` · `reports/`) 마운트, 컨테이너 타임존, Windows에서의 Docker Desktop 부담이 전부 순손실입니다.

**Docker가 주던 재현성은 `uv`가 대신합니다.** `uv.lock`이 의존성을, `requires-python`이 인터프리터 버전까지 고정합니다. TA-Lib 같은 C 확장을 강제하지 않는 한(2.1에서 지표 라이브러리를 전략 작성자 자유로 넘겼으므로) 네이티브 설치에 문제가 없습니다.

```bash
uv sync
uv tool install .          # marketscan 이 PATH에 올라간다
marketscan describe         # 설치 확인
```

**⚠️ 자동 실행을 무엇이 맡을지는 아직 정하지 않았습니다** (11장 4b번).

지금 확정된 것은 **"무엇을 부르는가"뿐**입니다. 하루 몇 번, 시장 마감 뒤에 이런 호출이 일어나야 합니다.

```
marketscan ingest --venue upbit          # 코인 일봉 수집
marketscan run --market crypto --commit  # 코인 판정 + 알림
marketscan run --market krx    --commit  # 한국장 마감 후
marketscan run --market us     --commit  # 미국장 마감 후
```

- Fresh Bar Gate(3.5)가 있으므로 `--market` 없이 전부 돌려도 되지만, 명시하는 쪽이 로그를 읽기 편합니다.
- **`--commit`이 없으면 알림을 보내지 않고 봉도 소비하지 않습니다** (12장). 자동 실행에만 붙입니다.

**이 호출을 거는 주체는 두 후보가 있고, 둘 다 위 명령줄을 바꾸지 않습니다.**

| 후보 | 얻는 것 | 치르는 값 |
| :--- | :--- | :--- |
| **OS 스케줄러** (크론 / 작업 스케줄러) | 상주 프로세스가 없다. 죽을 것이 없고 재부팅을 견딘다. 지금 당장 쓸 수 있다 | 알림·재시도·백오프가 OS 설정으로 흩어진다. 실행 이력이 `runs` 밖에도 생긴다 |
| **`serve` 명령** (스케줄 + 알림 내장) | 스케줄·재시도·알림이 한곳에 모인다. 크로스 플랫폼이 하나로 끝난다 | **상주 프로세스가 돌아온다** — v0.4에서 APScheduler를 기각한 이유가 그대로 되살아난다 |

**지금 정하지 않는 이유는 정할 필요가 없기 때문입니다.** 어느 쪽이든 `run --commit`이 하는 일은 같고, 결정을 미뤄도 코드가 늘지 않습니다. 반대로 지금 한쪽으로 굳히면 나중에 되돌릴 때 문서와 코드가 함께 어긋납니다. **Phase 1에서 실제로 며칠 돌려 본 뒤 판단합니다** — "상주 프로세스가 죽는 것이 실제로 문제가 되는가"는 겪어 봐야 알 수 있습니다.

**보안** — 네트워크에 아무것도 열지 않으므로 v0.4가 걱정하던 노출 위험이 대부분 사라집니다. 리버스 프록시·API 토큰·세션 쿠키가 전부 불필요합니다. 남은 비밀은 **텔레그램 토큰과 (API를 쓴다면) LLM 키뿐**이며, 시세에는 자격 증명이 필요 없습니다(3.3).

**백업** — `data/`(SQLite) · `strategies/` · 마스터 키. `reports/`는 재생성 가능하므로 선택입니다. `ohlcv_cache`는 무료 소스가 막혀도 남는 유일한 자산이므로 반드시 포함합니다(3.9).

**타임존** — 프로세스는 UTC로 고정하고 표시만 `user_timezone`으로 변환합니다. 스케줄 시각은 로컬 기준으로 적되, **시장 마감 시각과의 관계를 함께 남겨** 서머타임 전환 때 확인할 수 있게 합니다 (미국장은 한국 기준 개장이 1시간 움직입니다 — 3.2).

> **나중에 리눅스 상시 가동 장비로 옮기더라도 `uv`가 동일하게 동작합니다.** Docker가 필요해지는
> 시점은 사실상 없고, 정말 필요하면 Dockerfile 하나를 그때 추가하면 됩니다. **쓰지 않을 것을
> 미리 유지하지 않습니다.**

---

## 9. 구현 로드맵

v0.5에서 순서가 바뀌었습니다. **백테스트가 LLM보다 앞으로 옵니다** — 4.8에서 백테스트의 용도를 "전략 탐색"이 아니라 "내 구현 검증"으로 재정의했으므로, 비결정적이고 비싼 LLM 계층을 얹기 **전에** 데이터·엔진의 정직성을 확인해야 합니다.

### Phase 0 — 계약 확정 ✅ 완료
- `Item` / `Bundle` / `RunContext` / `BaseNode` / `InstrumentRef` / `MarketCalendar` / `MarketDataProvider` 타입 확정
- DAG JSON 스키마 + Pydantic 모델 확정
- **산출물**: 더미 노드로 DAG가 실행되는 통과 테스트

### Phase 0.5 — v0.5 전환 (1주) ★ 신설
v0.4 전제 위에 쌓기 전에 방향을 먼저 돌립니다. 지금이 가장 싼 시점입니다 (노드 5개, 프론트 ~1,000줄).
- **개명**: `tradeflow` → `marketscan`. 저장소·`pyproject.toml`·환경변수 접두사·문서. **파이썬 패키지는 `app` 유지**라 임포트는 안 바뀜
- **웹 계층 제거**: `frontend/` · FastAPI · APScheduler · `docker-compose.yml` 삭제 → **Typer CLI** (12장)
- 디렉터리 평탄화: `backend/app/` → `app/` (7장)
- **`Strategy` 프로토콜 확정** (4.2) + `strategies/` 로더 + 소스 해시 기록 (4.7)
- `Indicator` 범주 동결 — `maFilter`는 남기고 신규 추가 금지 (5장)
- 타임프레임을 정책 계층에서 `1d`/`1w`로 제한 (3.6). **타입은 건드리지 않는다**
- **산출물**: `marketscan run --dry-run`이 더미 전략으로 끝까지 도는 상태

### Phase 1 — 전략 러너 & 단일 시장 E2E (2주) — 자동 실행 결정만 남음
- ✅ 실행 엔진 보강(에러 정책·`node_runs` 기록·stdout 진행 표시)
- ✅ **`--commit` 규약**과 종료 코드 확정 (12장). 기본이 dry-run이어야 사고가 안 난다
- ✅ `Symbol Universe` → `Market Data` → `Strategy Runner` → **stdout + HTML 리포트** E2E 동작
  (텔레그램은 12.2에서 `serve` 쪽으로 빠졌다 — Phase 1의 "동작하는 물건"은 리포트다)
- ✅ 첫 전략 **횡단면 모멘텀 12-1** — 표준값 고정(252/21), `rank`가 실제로 쓰이는 것 확인
- ✅ 시장 하나(업비트)로 좁혀서 완주 — `CcxtProvider` 실물 일봉
- ✅ **`bar_state` 영속화** (3.5) — 게이트가 프로세스를 넘어 남는다
- ✅ 캔들 마감 처리, `signals list` / `explain` / `signals ack`(acted 응답) (12장)
- ✅ 파이프라인 정의 형식 **YAML 확정** (11장 4번)
- ⬜ **자동 실행 방식 결정** (11장 4b) — OS 스케줄러 vs `serve`. **며칠 돌려 본 뒤 정한다**
- ⚠️ 인터페이스는 **"노드는 `ohlcv_cache`를 읽는다"로 고정**. Phase 2에서 워커만 끼워 넣도록
  — **아직 고정되지 않았다.** 현재 `Market Data`는 Provider를 직접 호출한다 (3.9)
- ✅ 여기서 "동작하는 물건"이 나옵니다

### Phase 2 — 멀티 마켓 확장 (2주) ★ 이 프로젝트의 존재 이유
- `MarketCalendar` 3종(24x7 / KRX / US) + `exchange_calendars` 연동
- 일봉 소스 4종: `PykrxProvider`, `YFinanceProvider`, `FdrProvider`, `CcxtProvider` — **전부 무인증**
- **Routing Table** — `(venue, timeframe) → 소스 우선순위` + 폴백 동작
- **Ingestion Worker** + `ohlcv_cache` 영구 보관, 수정주가 정책, **상장폐지 종목 수집**(3.9)
- `instruments` 심볼 마스터 + 자동완성
- **Fresh Bar Gate** 및 혼합 파이프라인 검증 (코인+한국+미국 동시)
- 서머타임 전환일 회귀 테스트, 두 소스 종가 정합성 검증
- ⚠️ Connections 화면은 **LLM·텔레그램용으로 축소**. 시세에는 키가 필요 없다 (3.3)
- ✅ 두 번째·세 번째 어댑터를 붙여봐야 추상화가 맞는지 검증됩니다. **v0.4의 3주 → 2주**로 준 것은
  분봉 소스(KIS·Alpaca·Toss)가 빠졌기 때문입니다

### Phase 3 — 백테스트 (1~2주) — v0.4의 Phase 4에서 이동
- 캘린더 기반 시간 리플레이, look-ahead 방지 assert
- **피처 행렬 사전 계산**(4.8 성능) — 여기가 유일한 실제 엔지니어링
- **엔진 검증 테스트**: 난수 신호 · 전량 매수 · 신호 1일 밀기 · 상장폐지 포함 여부 (4.8)
- 신호 품질 지표 + 리포트 화면
- ⚠️ 공수가 짧은 것은 **청산·포지션·체결 모델이 없기 때문**입니다(1.3). 그 선을 넘는 순간
  자체 구현을 접고 기성 엔진을 검토해야 합니다

### Phase 4 — LLM 스크리닝 & 분기 (2주) — v0.4의 Phase 3에서 이동
- LLM Provider 추상화 + 구조화 출력 + `llm_cache`
- **`LLM Screen`을 정성 정보 필터로 구현** (5장) — 공시·뉴스 기반 제외 판정
- `abstain` / `confidence`를 1급 출력으로, 임계 미달 시 신호 제거를 기본값으로
- Condition Splitter / Merge / Alert Cooldown / Sort·Limit
- 스킵 전파, 에러 라우팅 브랜치
- 레이트 리밋 · LLM 비용 상한
- ⚠️ Celery/Redis는 **asyncio로 감당 안 될 때** 도입. 조기 도입은 복잡도만 늘립니다

### Phase 4.5 — 운용 (기간 제한 없음)
- **`shadow` 모드로 최소 몇 달 관찰** (4.8). 알림을 켜기 전 신호 품질을 실측
- **오버라이드 추적** 도입 (4.8) — 무시한 신호의 사후 성과
- 소액으로 시작

### Phase 5 — 실주문 (선택, 3주+)
- `BrokerProvider` 구현, RiskGuard(주문 상한·일일 한도·킬 스위치), idempotency
- `paper` 모드로 최소 2주 실거래 대조 검증 후 `live` 소액 전환
- 부분 체결·주문 거부·국내 거래세/미국 세금 처리

---

## 10. 주요 리스크와 대응

| 리스크 | 영향 | 대응 |
| :--- | :--- | :--- |
| ★ **과적합 (전략 탐색)** | **가장 흔한 실패.** 백테스트만 좋고 실전에서 죽음 | 파라미터 튜닝을 비목표로 선언(1.3), 검증된 팩터의 표준값 사용, 백테스트의 용도를 구현 검증으로 재정의, **다중검정 카운터** (4.8) |
| ★ **LLM이 백테스트를 반복 호출** | 위 위험의 **자동화된 버전.** 사람보다 1000배 빠르게 과적합 | `backtest_runs` 카운터를 매 실행 출력, 임계 초과 시 명시적 플래그 없이 거부 (4.8 / 12장) |
| ★ **에이전트가 실수로 알림 발송·봉 소비** | 오발송, 그리고 **신호 영구 유실**(3.5) | `--commit` 없이는 부작용 없음을 기본값으로. 읽기 전용 명령과 분리 (12장) |
| ★ **도구 사용 agent가 백테스트에서 미래를 읽음** | **실제 미래 데이터 참조.** 학습 누출보다 나쁨 | `deterministic=False` provider는 backtest 모드에서 하드 차단, 캐시도 하지 않음 (5장) |
| ★ **전략 코드 버전 드리프트** | 과거 실행의 근거를 잃음 | 소스 SHA-256을 `pipeline_versions`에 기록 + 스냅샷, 불일치 시 경고 (4.7) |
| ★ **`compute`의 비인과성** | **미래 참조가 조용히 들어옴** | `shift(-n)`·`center=True`·`bfill` 금지를 리뷰 체크리스트로, 난수 신호 테스트를 사후 방어선으로 (4.2 / 4.8) |
| ★ **상장폐지 종목 데이터 미확보** | 서바이버십 편향이 데이터 레이어에 고착 | 폐지 종목도 수집·보존 (3.9), 유니버스 point-in-time 산출 (4.8) |
| 무료 소스 차단·중단 | 데이터 유실 | Ingestion Worker 단일 창구 + 라우팅 폴백 + 캐시 영구 보관 (3.9) |
| **수정주가 불일치** | **지표가 조용히 틀어짐** | 전역 정책 고정 + 캐시 키 포함 + 소스 간 종가 검증 (3.8) |
| 미국장 서머타임 오처리 | 신호 시각 1시간 오차 | `ZoneInfo` 사용, 전환일 회귀 테스트 (3.2) |
| 장 마감 중 신호 재발생 | 알림 폭주 | Fresh Bar Gate + skip_stale (3.5) |
| 중복 알림 | 신뢰도 하락 | dedup_key UNIQUE + Cooldown 노드 (4.5) |
| 미완성 캔들 신호 | 잘못된 판단 | closed_only + 마감 후 지연 (4.4) |
| 백테스트 미래 참조 | 전략 과신 | Provider 시간 컷 + assert (4.8) |
| **LLM 학습 데이터 누출** | **백테스트가 낙관 편향** | 캐시로는 못 막는다. 경고 배지 + `shadow` 모드를 1급 검증 경로로 (4.8) |
| **유니버스 서바이버십** | **백테스트 성과 부풀림** | 백테스트에서 point-in-time 산출, 불가 소스는 거부 (4.8) |
| 폴백으로 소스가 바뀜 | 지표 불연속·결정성 훼손 | `failed_sources`를 `ctx.log`·`Item.meta`·`node_runs`에 노출 (3.4) |
| 실패한 실행이 봉을 소비 | **신호 유실** | `stage()` 후 성공 시에만 `commit()` (3.5) |
| 결제 통화 오판 | 통화 표기·합산 오류 | `quote_style`로 symbol에서 유도, 형식 불일치는 거부 (3.1) |
| LLM 비용 폭증 | 운영비 | 캐시, 호출 상한, **반드시 필터 뒤에 배치** (5장) |
| LLM이 근거 없이 단정 | 잘못된 제외·포함 | `abstain` / `confidence`를 1급 출력으로, 임계 미달 시 신호 제거를 기본값으로 (5장) |
| 백테스트 리플레이 성능 | 반나절 걸리면 아무도 안 씀 | 피처 행렬 사전 계산. **계약을 바꿔서 속도를 얻지 않는다** (4.8) |
| API 키 유출 | 치명적 (**v0.5에서 크게 축소**) | **시세에 키가 필요 없어짐**(3.3). 남은 것은 LLM·텔레그램뿐. 암호화 저장, DAG에서 분리, 로컬 바인딩 (4.6 / 8) |
| 거래소 레이트 리밋 | 데이터 누락 | 일봉 하루 1회 수집으로 압박 감소 + Ingestion Worker 단일 지점 + 백오프 (3.9) |
| ⚠️ Toss API 스펙 불확실 | **Phase 5로 이연** | 시세를 무인증 소스로 해결하므로 주문 단계 전까지 영향 없음 (3.3) |
| ⚠️ pandas-ta 유지보수 중단 | 지표 신뢰성 (**영향 축소**) | 전략 클래스가 라이브러리를 직접 고르므로 전역 결정이 아니게 됨 (2.1) |
| SQLite 잠금 경합 | 실행 실패 (**v0.5에서 강등**) | CLI 전환으로 쓰는 프로세스가 하나뿐. 자동 실행 겹침 대비로 WAL + busy_timeout만 유지 (4.7) |
| **buylow 등 기존 도구와의 중복** | 만들 이유가 사라짐 | **멀티마켓(3장)이 유일한 차별점**임을 인지하고 유지. 한국 단일 시장으로 좁아지면 기성 도구 검토 |

---

## 11. 미결정 사항

**착수 전 결정이 필요한 것**

1. ★ **코인 일봉 경계** — `UTC00`(= KST 09:00, 업비트 기준) vs `KST00`(한국 자정). 현재 기본값은 `UTC00`.
   **v0.5에서 승격.** 일봉이 유일한 판단 단위가 되면서 코인 신호 전체가 이 경계에 좌우된다 (3.6).
2. ★ **상장폐지 종목의 과거 가격을 어디까지 확보할 수 있는가** — 목록은 FDR로 얻을 수 있지만
   폐지 시점까지의 일봉을 실제로 받을 수 있는지는 소스별 확인이 필요하다. **못 받으면 백테스트가
   구조적으로 부풀려지므로**(4.8 서바이버십), Phase 2의 실질적 선행 조건이다.
3. ★ **첫 전략을 무엇으로 할 것인가** — Phase 1에서 `rank`가 실제로 쓰이는지 검증할 대상.
   횡단면 모멘텀(12-1)을 권장하나, KRX 수급(외국인·기관 순매수)을 쓰는 쪽도 후보다.
4. ~~**파이프라인 정의 형식 — DAG JSON vs TOML**~~ — **YAML로 확정. Phase 1에서 해소.**
   손으로 적어 본 결과 JSON의 문제는 구조가 아니라 **주석을 달 수 없다는 것**이었다.
   파이프라인 파일에 적고 싶은 것의 절반은 "왜 이 종목인가" · "왜 이 값인가"인데 JSON에는
   그걸 적을 자리가 없다. 그래프가 직선이라 평탄화(TOML)로 얻을 것은 적었고, 평탄화는
   **변환 규칙이라는 새 개념**을 들여오는 대신 주석 문제는 풀지 못한다.
   - **6장 스키마는 그대로다.** YAML은 같은 구조를 다르게 적는 것뿐이고, 로더가 확장자로
     갈라 받아 같은 `PipelineSpec`을 만든다. 새 스키마도 변환기도 없고 `runner.py`는 손대지 않았다.
   - **`pipeline_versions`의 스냅샷은 JSON을 유지한다.** 그건 사람이 적는 형식이 아니라
     직렬화이고, 저장된 버전은 불변이어야 하므로 표현이 흔들리면 안 된다 (4.7).
   - 기존 `.json` 파일도 계속 읽힌다.

4b. ★ **자동 실행과 알림을 무엇이 맡는가 — OS 스케줄러 vs `serve` 명령** (v0.5 개정 중 재개방).
   v0.5 초안은 "OS 크론으로 확정"이라고 적었지만, **알림을 포함한 `serve` 명령이 후보로
   올라오면서 다시 열렸다.** 8장에 두 후보의 득실이 있다.
   - **알림 전송은 이미 `serve` 쪽으로 확정됐다** (12.2). 단일 실행(`run`)의 산출물은
     stdout과 HTML 리포트뿐이다. 남은 미결정은 **스케줄을 누가 거는가**뿐이다.
   - OS 스케줄러로 가더라도 전송 주체는 필요하므로, 그쪽을 택하면 `serve`가 아니라
     "알림만 담당하는 얇은 명령"이 대신 생긴다. **어느 쪽이든 `run`은 조용하다.**
   - 쟁점은 **상주 프로세스를 되살릴 값이 있는가**다. v0.4에서 APScheduler를 기각한 이유
     ("프로세스가 죽으면 스케줄도 같이 죽는다")는 `serve`에도 그대로 적용된다.
   - **미뤄도 비용이 없다** — `run --commit`이 하는 일은 양쪽에서 같고, 결정이 CLI 표면을
     바꾸지 않는다. 지금 한쪽으로 굳히면 되돌릴 때 문서와 코드가 함께 어긋난다.
   - **Phase 1에서 실제로 며칠 돌려 본 뒤 판단한다.** "프로세스가 죽는 것이 실제로 문제가
     되는가"는 겪어 봐야 안다.
   - **Phase 1 구현 후에도 열려 있다.** 코드는 준비됐다 — `run --commit`이 실물 시세로 돌고,
     `bar_state`가 영속화되어 **무엇이 몇 번 부르든 같은 봉을 두 번 판정하지 않는다.**
     스케줄러를 고르기 위한 선행 조건은 전부 끝났고, 남은 것은 **며칠 돌려 보는 일**뿐이다.
   - 관찰할 것 하나가 늘었다 — 현재 `Symbol Universe`는 봉이 전부 stale인 실행에서도 거래소
     목록을 다시 조회한다. 하루 여러 번 부르는 구성을 고르면 이 호출이 그대로 늘어난다.

**나중에 정해도 되는 것**

5. **알림 채널** — 텔레그램 외 Slack/Discord/이메일 필요 여부.
6. ⚠️ **지표 라이브러리** — `pandas-ta-classic` / TA-Lib / pandas 직접 구현.
   **v0.5에서 강등**: 전략 클래스가 각자 임포트하므로 전역 결정이 아니게 되었다 (2.1).
7. **멀티 타임프레임 features 네임스페이스** — `w1.sma_20` 형태로 접두할지, 별도 필드를 둘지.
   일봉·주봉만 남으면서 우선순위가 낮아졌다 (5장).
8. ⚠️ **빗썸 `fetchOHLCV` 지원 여부** — CCXT 런타임 확인 후 미지원이면 별도 어댑터 (3.3).
9. **Alpaca 데이터 피드** — 무료 IEX vs 유료 SIP. yfinance 폴백용이므로 Phase 3 이후.

**Phase 5로 이연된 것**

10. ⚠️ **Toss 증권 오픈 API 스펙** — 공개 API 존재 여부, 인증 방식, 레이트 리밋.
    **시세를 무인증 소스로 해결하면서 주문 단계 전까지 영향이 없어졌다** (3.3).

**v0.5에서 해소된 것**

- ~~분봉 보존 정책~~ — 분봉을 수집하지 않기로 하면서 소멸. `ohlcv_cache`는 SQLite로 확정 (3.9).
- ~~Phase 2 소스 구현 순서~~ — 무인증 일봉 4종으로 자명해짐 (3.3).
- ~~미국주식 프리/애프터마켓~~ — 일봉은 정규장 기준이므로 무관.
- ~~LLM 노드 배치~~ — 필터 뒤로 확정 (5장). 비용과 역할(정성 필터) 양쪽에서 결론이 같다.
- ~~프로젝트 이름~~ — **`marketscan`으로 확정.** 3장(멀티 마켓)이 존재 이유이므로 이름이
  그것을 그대로 말한다. `stockscan`(코인이 부록이 된다) · `signal-discovery`(1.3이 금지한
  "탐색"을 권한다)는 기각. 근거는 문서 서두.
- ~~배포 형태~~ — **Docker 제거, `uv` + `[project.scripts]`로 확정** (8장). 스케줄 방식은 위 4번으로 남았다.
- ~~구현 언어~~ — **파이썬 유지 확정.** 3장이 PyKRX·FDR·exchange_calendars 위에 서 있어
  대체재가 없다 (2.1).

> 무료 데이터 API의 티어 정책(호출 한도, 이력 범위)은 자주 바뀝니다. 본 문서의 수치성 서술은 **착수 시점에 재확인**하세요.

---

## 12. CLI 인터페이스 ★ (v0.5 신설)

사용자가 셋입니다 — **사람**, **자동 실행(스케줄러)**, 그리고 **CLI를 호출하는 LLM**. 셋의 요구가 다르므로 명령 표면을 그에 맞춰 설계합니다.

> **파이프라인 안의 `LLM Screen` 노드(5장)와 혼동하지 마세요.** 그건 결정성·캐시·비용 상한이
> 걸린 파이프라인 부품이고, 여기서 말하는 LLM은 **밖에서 CLI를 부르는 대화형 에이전트**입니다.

### 12.1 명령

| 명령 | 부작용 | 용도 |
| :--- | :---: | :--- |
| `run [--market M] [--commit]` | **`--commit` 시에만** | 파이프라인 실행. **기본은 dry-run.** 산출물은 stdout + HTML 리포트 |
| `ingest [--venue V]` | 캐시 쓰기 | 일봉 수집 (3.9) |
| `backtest --strategy S --from D` | `backtest_runs` 기록 | 리플레이 + 리포트 생성. **다중검정 카운터 출력** (4.8) |
| `explain <signal_id>` | 없음 | ★ **왜 이 신호가 났는가** — 아래 참조 |
| `signals list [--acted] [--strategy S]` | 없음 | 신호 이력 질의 |
| `stats [--group-by G] [--compare acted]` | 없음 | 신호 품질 지표. `--compare acted`가 오버라이드 분석 (4.8) |
| `strategy new <name>` | 파일 생성 | `Params` 포함 템플릿 |
| `strategy check <name>` | 없음 | ★ **AST 정적 검사** — 아래 참조 |
| `verify` | 없음 | 엔진 검증 4종 (난수 신호·전량 매수·신호 밀기·상장폐지) (4.8) |
| `describe` | 없음 | 전략 목록·유니버스 크기·캐시 커버리지·마지막 실행. **에이전트의 방향 잡기용** |
| `connections set` | 비밀 쓰기 | **사람만.** 텔레그램·LLM 자격 증명 |

### 12.2 부작용은 명시적 옵트인 ★

**에이전트가 실수로 봉을 소비하면 안 됩니다.** 3.5의 `commit()`이 발동하면 그 신호는 재실행에서 stale로 걸러져 **영영 사라집니다.**

```
marketscan run              # 계산만. signals 미기록, 봉 discard()
marketscan run --commit     # 실제 실행. 자동 실행에만 붙인다
```

`--commit`이 없으면 3.5의 `stage()` 이후 반드시 `discard()`합니다. **기본값이 안전한 쪽이어야 사고가 안 납니다.**

**★ 단일 실행은 외부로 아무것도 내보내지 않습니다 (v0.5 개정 중 추가)**

`--commit`이 열어 주는 것은 **`signals` 기록과 봉 소비 둘뿐**입니다. 텔레그램 같은 채널 전송은 `run`의 일이 아니라 상주 실행(`serve`)의 몫으로 미뤘습니다(11장 4b).

- **이유는 신뢰입니다.** 사람이 전략을 고치며 손으로 돌리는 실행과 자동으로 도는 실행은 오발송의 무게가 다릅니다. 손으로 돌릴 때마다 채널로 메시지가 나가면 **알림 자체를 믿지 않게 되고**, 그러면 1.2의 "주의력 기계"가 무너집니다. 무시하게 된 알림은 없는 알림입니다.
- **단일 실행의 산출물은 stdout과 정적 HTML 리포트입니다.** 리포트는 `reports/`에 파일로 떨어지며(2.1), dry-run은 `latest.html` 하나를 덮어쓰고 `--commit` 실행만 `run_<id>.html`로 남깁니다 — 실제로 나간 판단만 이력이 되면 됩니다.
- **차단은 노드 안이 아니라 실행 엔진에 둡니다.** 노드가 `sends_external_messages = True`를 선언하면 `ctx.sends_alerts`가 False일 때 엔진이 **아예 실행하지 않습니다.** 노드마다 `if ctx.sends_alerts:`를 심는 방식은 배선 노드가 늘어나면 언젠가 하나를 빠뜨리고, 그날 손으로 돌린 실행이 채널로 메시지를 쏩니다.
- `ctx.sends_alerts`는 **세 조건의 곱**입니다 — `allow_alerts`(`serve`만 켠다) × `--commit` × 모드가 backtest·shadow가 아닐 것.

> 리포트 파일 쓰기는 `--commit` 뒤에 두지 않았습니다. `reports/`는 재생성 가능하고(8장 백업 대상에서 제외) 무엇도 되돌릴 수 없게 만들지 않기 때문입니다. **되돌릴 수 없는 것만 `--commit`이 막습니다.**

### 12.3 종료 코드

4.1이 "빈 `Bundle`도 정상 출력"이라고 정했으므로, **"신호 0건"과 "실패"를 반드시 구분**합니다.

| 코드 | 의미 |
| :--- | :--- |
| `0` | 성공 (신호 0건 포함 — `{"signals": []}`) |
| `2` | 데이터 소스 실패 |
| `3` | 검증 실패 (전략 오류, 커버리지 부족, 다중검정 임계 초과) |

### 12.4 출력 규약

- **모든 명령에 `--json`.** 스키마를 안정적으로 유지합니다. LLM이 한국어 표를 파싱하게 두면 오독합니다.
- **`--limit` 기본값을 둡니다.** LLM은 좁은 JSON은 잘 읽고 큰 덤프에는 무너집니다.
- **OHLCV 원본을 기본 출력에 넣지 않습니다.** 필요하면 `--fields`로 명시 요청하게 합니다.
- 사람용 기본 출력은 사람이 읽기 좋은 표로 두되, **`--json`이 있으면 그것만** 내보냅니다(진행 로그는 stderr).

### 12.5 `explain` — 가장 값있는 명령

사람이 알림을 보고 던지는 질문은 언제나 하나입니다. **"이게 왜 떴어?"** 4.9는 `node_runs` 스냅샷으로 사후 재현이 된다고 하지만, 실제로 하려면 5개 테이블을 조인해야 합니다. 이걸 한 명령으로 접습니다.

```json
{
  "instrument": "krx:005930", "as_of": "2026-08-01T06:30:00Z",
  "universe": { "rule": "market_cap_top:500", "included": true, "point_in_time_date": "2026-08-01" },
  "strategy": { "id": "cross_momentum_12_1", "sha256": "9f2c…",
    "features": { "mom_12_1": 0.412, "rank": 7, "universe_size": 500, "percentile": 1.4 },
    "threshold": "top_pct <= 0.05", "passed": true },
  "llm_screen": { "exclude": false, "confidence": 0.82, "abstain": false, "provider": "command:claude" },
  "cooldown": { "last_alert": "2026-05-12", "blocked": false },
  "data": { "source": "pykrx", "adjusted": true, "fallback_from": [] }
}
```

`data.fallback_from`(3.4 폴백 가시화)과 `strategy.sha256`(4.7 전략 버전)이 들어 있는 것이 핵심입니다. **없으면 LLM이 4~5번 질의하다 틀립니다.**

### 12.6 `strategy check` — 규칙을 기계화한다

4.2 규칙 1의 인과성은 런타임에 강제할 수 없지만, **AST로 상당 부분 잡힙니다.**

```json
{ "ok": false, "violations": [
  { "rule": "causality", "line": 42,
    "detail": "shift(-5) — 미래 참조입니다. 타깃 계산이라면 백테스트 평가기로 옮기세요." },
  { "rule": "no_network", "line": 7,
    "detail": "import httpx — 전략은 데이터를 직접 가져올 수 없습니다 (4.2 규칙 2)." },
  { "rule": "injected_clock", "line": 55,
    "detail": "datetime.now() — ctx.now를 쓰세요." } ] }
```

검사 항목: `shift(음수)` · `center=True` · `bfill` · `datetime.now` · 네트워크 라이브러리 임포트 · `Params` 미선언.

**LLM이 전략을 쓰고 → `check`가 거르고 → `verify`로 엔진 검증까지 도는 루프**가 만들어집니다. LLM이 무심코 `shift(-1)`을 쓰는 것은 흔한 일이라 이 검사는 실제로 값을 합니다. ⚠️ **통과가 인과성을 보장하지는 않습니다** — 사후 방어선은 `verify`의 난수 신호 테스트입니다.

### 12.7 만들지 않을 것

- **MCP 서버** — 잘 설계된 CLI + 셸 접근이면 충분하고, 유지할 프로세스를 하나 늘립니다. 필요해지면
  CLI를 감싸는 얇은 껍데기라 언제든 붙일 수 있습니다.
- **LLM에게 매매 결정을 맡기는 것** — 1.2가 "최종 판단은 사람이 한다"고 정한 선입니다.
  여기서 LLM의 역할은 **읽고 설명하는 것**입니다.

이 층은 1.2의 "주의력 기계"와 어긋나지 않고 한 겹 더 쌓습니다 — 예측을 추가하는 것이 아니라 주의력을 한 번 더 증폭합니다. 다만 그 선을 넘는 두 가지(**백테스트 루프**, **매매 결정**)는 설계로 막습니다.

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

## 부록 B. v0.3 → v0.4 변경 요약

외부 리뷰 제안서를 검토해 반영한 항목입니다.

| # | 항목 | v0.3 | v0.4 | 이유 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 성과 지표 | 총수익률·MDD·승률·샤프 | **신호 품질 지표** (forward return·hit rate·IC·벤치마크 대비) | 청산 개념이 없어 수익률 지표는 정의 자체가 불가능 (4.8 / 1.3) |
| 2 | LLM 백테스트 | `llm_cache`로 결정성 확보 | + **학습 데이터 누출 경고**, `shadow`를 1급 검증 경로로 | 캐시는 재실행 결정성만 보장하고 누출은 못 막음 (4.8) |
| 3 | 유니버스 | 산출 시점 미규정 | 백테스트에선 **point-in-time**, 불가 소스는 거부 | 서바이버십 바이어스는 가격 assert로 안 잡힘 (4.8) |
| 4 | 코인 어댑터 | `UpbitProvider` / `BinanceProvider` | **`CcxtProvider` 단일** + quirks | 거래소별로 쪼개면 CCXT를 쓰는 이유가 사라짐 (3.3) |
| 5 | `capabilities` | 수기 선언 | CCXT는 `has`/`timeframes`에서 **자동 유도** | 수기 표는 언젠가 실제 능력과 어긋남 (3.3) |
| 6 | `OrderRequest` | 수량만 | `quantity` \| **`notional`** 택일 | 원화 마켓 시장가 매수는 금액 기준. Phase 5지만 인터페이스는 지금 확정 (3.3) |
| 7 | `quote_currency` | venue 상수 | `quote_style`로 **symbol에서 유도** | 업비트·바이낸스는 단일 통화 마켓이 아님 (3.1) |
| 8 | 폴백 | `source_id` 기록만 | `failed_sources`를 **실행 이력에 노출** | 조용한 폴백이 지표 불연속·결정성 훼손을 감춤 (3.4) |
| 9 | Fresh Bar Gate | 수집 시 즉시 소비 | `stage()` → 성공 시 `commit()` | 실패한 실행이 봉을 삼키면 그 신호가 영영 사라짐 (3.5) |
| 10 | Bundle item 키 | `instrument.key` | **`(instrument.key, timeframe)`** | 멀티 타임프레임에서 Merge가 한쪽을 덮어씀 (4.1) |

**검토했으나 채택하지 않은 제안**

| 제안 | 판단 |
| :--- | :--- |
| `ohlcv_cache`를 Parquet/DuckDB로 | **보류.** 일봉은 SQLite로 충분(200종목×10년 ≈ 50만 행). 실제 미결정은 저장소가 아니라 **분봉 보존 정책**이므로 3.9에서 그걸 먼저 정한다 |
| TypeScript 5.x로 다운그레이드해 ESLint 복구 | **기각.** 프론트 규모(약 1,000줄)에서 `tsc --noEmit` 위에 ESLint가 더할 이득이 적다. 린터가 필요하면 `typescript-eslint` peer에 묶이지 않는 **oxlint / Biome**을 쓴다 |
| 캔버스 UI를 걷어내고 YAML·DSL로 | **기각.** 유지비는 이미 지불됐고(폼 자동생성·SSE·상태관리 동작 중), 캔버스는 편집 UI인 동시에 **4.9 관측성의 표시면**이다. 대신 `Custom Expression` 사용 비중을 계기판으로 둔다 (5장)<br>→ ⚠️ **v0.5에서 뒤집힘.** 전략이 클래스로 옮겨가면서 캔버스가 편집 UI이기를 그만두었고, 기각 근거의 절반이 사라졌다 (부록 C) |
| 토스증권 Open API 스펙 확정 | **보류.** 제안서의 서술을 1차 출처로 확인하지 못했다. 11장 1번은 미결정으로 유지 |
| `runs.resolved_sources` 소스 pinning | **축소 채택.** 우선 폴백 가시화(3.4)만 반영. pinning은 `ohlcv_cache.source_id`와 정보가 겹치므로 Phase 2에서 캐시를 실제로 붙일 때 판단 |

## 부록 C. v0.4 → v0.5 변경 요약

v0.4는 "노드를 조합해 전략을 만드는 비주얼 도구"였습니다. v0.5는 **"멀티마켓 횡단면 스크리너 + 알림"** 입니다. 아래 변경은 서로 독립적이지 않고, 대부분 앞의 결정에서 연쇄적으로 따라 나옵니다.

| # | 항목 | v0.4 | v0.5 | 이유 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **전략 표현** | 지표 노드 조합 | **파이썬 클래스 1개** | 지표 × 파라미터 × 조합은 끝이 없다. 같은 접근의 도구들이 7~10개에서 멈춰 있고, 이 시스템의 사용자는 파이썬을 쓰는 본인 한 명이다 (4.2 / 5장) |
| 2 | **판단 단위** | 백테스트 일봉 / 알림 분봉 | **일봉·주봉 전용** | 분 단위 왕복 비용이 기대 수익을 넘고, 그 구간의 상대는 호가창을 본다. 못 이기는 게임 (1.3 / 3.6) |
| 3 | **전략의 축** | 시계열 필터 체인 | **횡단면 랭킹이 1급** | 표본 수 100배, 시장 방향 상쇄, 팩터의 정의 자체가 횡단면 (1.2 / 4.1) |
| 4 | **캔버스** | React Flow로 전략 조립 | **폐기** | 전략이 클래스로 옮겨가면서 캔버스가 편집 도구이길 그만두었다. 최종 인터페이스는 19번 참조 (2.1 / 7장) |
| 5 | **백테스트의 용도** | 전략 검증 | **구현 검증** | 2,500행으로 수백만 조합을 뒤지면 우연히 맞는 것이 반드시 나온다. 검증된 팩터를 표준값으로 쓰고, 백테스트는 데이터·엔진의 정직성을 잰다 (1.3 / 4.8) |
| 6 | **엔진 검증** | look-ahead assert만 | **+ 난수 신호 · 전량 매수 · 신호 밀기 테스트** | 백테스트 엔진에는 정답을 알려 줄 오라클이 없다. 난수 신호의 hit rate가 기저율을 넘으면 미래 참조가 있는 것 (4.8) |
| 7 | **LLM 노드** | LLM Decision — 매수 타당성 점수 | **LLM Screen — 정성 정보 필터** | 가격 예측 근거가 없고 학습 누출로 검증도 불가. 공시·뉴스로 지뢰를 거르는 쪽은 근거가 있고 수익 기여가 손실 회피로 명확 (5장) |
| 8 | **LLM 신뢰도** | 점수만 출력 | **`abstain` / `confidence`를 1급 출력으로** | 신뢰도를 안 받으면 모델이 헛소리 중인지 알 수 없다. FreqAI의 `do_predict`·DI와 같은 장치 (5장) |
| 9 | **전략 버저닝** | (없음) | **소스 SHA-256 + 스냅샷** | 전략이 파일이 되면서 생긴 구멍. 파일을 고치면 과거 버전의 의미가 소급으로 바뀐다 (4.7) |
| 10 | **백테스트 성능** | 매 봉 지표 재계산 | **피처 행렬 사전 계산** | 500만 회 재계산이면 수 시간. 대신 `compute`의 인과성이 전제가 되고, look-ahead 위험이 한 곳에 갇힌다 (4.8) |
| 11 | **시세 자격 증명** | 소스별 API 키 필요 | **불필요** | 일봉만 쓰면 PyKRX·yfinance·FDR·CCXT 공개 OHLCV로 충분. 갖고 있지 않은 키는 새지 않는다 (3.3) |
| 12 | **`ohlcv_cache` 저장소** | SQLite vs Parquet/DuckDB 미결정 | **SQLite 확정** | 2억 행 문제는 분봉에서만 발생했다. 일봉 2,000종목 × 10년 ≈ 500만 행 (3.9) |
| 13 | **상장폐지 종목** | 유니버스 산출에서만 고려 | **수집·보존 대상** | 살아 있는 종목만 쌓으면 서바이버십 편향이 데이터 레이어에 고착된다 (3.9) |
| 14 | **shadow 모드** | 분봉 전략의 대안 검증 | **LLM 검증 + 실전 전 관찰** | 분봉이 사라지며 원래 이유는 없어졌지만 용도가 바뀌어 남았다 (4.8) |
| 15 | **로드맵 순서** | LLM(P3) → 백테스트(P4) | **백테스트(P3) → LLM(P4)** | 백테스트가 구현 검증 도구라면, 비결정적이고 비싼 계층을 얹기 전에 돌려야 한다 (9장) |
| 16 | **오버라이드 추적** | (없음) | **`signals.acted` + 사후 성과** | 정체성이 규율 기계라면 측정할 것은 전략 성과만이 아니라 사용자가 규율을 지켰는지다 (4.8) |
| 17 | **`Custom Expression` 계기판** | 사용 비중이 높아지면 재검토 | **계기판이 켜져 재검토를 실행한 것이 v0.5** | 새 계기판은 반대 방향 — 배선용 노드가 늘어나면 파이프라인이 다시 전략을 표현하려 드는 것 (5장) |
| 18 | **프로젝트 이름** | `tradeflow` | **`marketscan`** | `trade`는 하지 않는 일을 암시하고 `flow`는 폐기된 캔버스 은유. `market`은 3장(존재 이유)을, `scan`은 1.2의 "훑는 범위"를 그대로 말한다. 중간에 거쳐 간 `assetscan`은 범위가 흐릿해 정리했다 (문서 서두) |
| 19 | **인터페이스** | 웹 UI (React SPA + REST) | **CLI (Typer)** | 하루 3회 도는 배치에 상주 서버가 필요 없다. FastAPI·APScheduler·SSE·템플릿이 함께 소멸. **개정 과정에서 React → HTMX → CLI 순으로 두 번 줄었고, HTMX는 계획 단계에서만 존재했다** (2 / 12장) |
| 19b | **시각화** | 서버가 렌더한 대시보드 | **정적 HTML 파일 생성** | 백테스트 리포트만 HTML이 필요한데, 서빙하지 않고 `reports/`에 떨어뜨리면 파일로 남아 나중에 비교할 수 있다 (2.1 / 7장) |
| 20 | **스케줄러** | APScheduler | ⚠️ **미결정으로 되돌림** | v0.5 초안은 OS 크론으로 확정했으나, **알림을 포함한 `serve` 명령이 후보로 올라오면서 다시 열렸다.** APScheduler를 되살리자는 뜻은 아니다 — 그때 기각한 이유(프로세스가 죽으면 스케줄도 죽는다)는 `serve`에도 그대로 적용되므로, 그 값을 치를 만한지가 11장 4b번의 실제 쟁점이다 (8장 / 11장) |
| 21 | **배포** | Docker Compose | **`uv` + `[project.scripts]`** | 스케줄러가 `docker run -v …`를 부르는 구조는 순손실. 재현성은 `uv.lock`이 준다 (8장) |
| 22 | **LLM 호출 방식** | API only | **API · 로컬 커맨드 · 로컬 모델** | agent 호출은 키가 필요 없고 자료를 스스로 찾는다. 대신 `deterministic` 플래그로 백테스트에서 차단 (5장) |
| 23 | **다중검정 카운터** | (없음) | **`backtest_runs` + 매 실행 경고** | LLM에게 CLI를 주면 파라미터 200조합을 순식간에 돌린다. 사람은 47번 돌린 걸 잊지만 카운터는 안 잊는다 (4.8) |
| 24 | **부작용 규약** | (없음) | **`--commit` 없이는 부작용 없음** | 에이전트가 실수로 알림을 쏘거나 봉을 소비하면 그 신호는 영영 사라진다 (12장) |
| 25 | **디렉터리** | `backend/` + `frontend/` | **최상위 평탄화** | 프론트가 없으면 "백엔드"라는 이름도 의미가 없다 (7장) |

**검토했으나 채택하지 않은 것**

| 제안 | 판단 |
| :--- | :--- |
| freqtrade 전략을 그대로 실행 | **기각.** freqtrade는 CCXT·24/7·무캘린더·무수정주가 전제라 주식과 양립하지 않는다. `IStrategy` 호환은 사실상 freqtrade 임포트를 뜻하고, 그러면 코인 런타임이 통째로 딸려온다. 게다가 공개 전략의 값어치는 대부분 청산 로직에 있는데 이 시스템에는 청산이 없다 (4.2) |
| freqtrade를 코인 전용으로 쓰고 사이드카만 붙이기 | **조건부 기각.** 코인만 할 것이라면 이쪽이 압도적으로 싸다. **멀티마켓이 요건이므로** 기각 (11장) |
| FreqAI 도입 | **기각.** lightgbm/torch/stable-baselines3가 딸려오고, 코인 모양이며, 재학습을 실제로 돌려 백테스트가 매우 느리다. **단 `do_predict`/DI의 계약은 LLM Screen에 이식했다** (5장) |
| DAG 엔진 제거 | **보류.** 파이프라인이 얕아져 위상정렬의 존재감은 줄었지만 `runner.py`는 이미 동작하고 테스트도 있다. 분기·팬아웃·에러 라우팅은 여전히 그래프다. **더 투자하지 않되 지우지도 않는다** (6장) |
| 청산·포지션을 넣어 수익률·샤프 측정 | **기각 유지.** 일봉으로는 장중에 손절이 닿았는지 알 수 없어 가정을 넣어야 하고, 그 순간 체결·수수료·세금이 딸려와 Phase 5를 끌어온다. 그 선을 넘으려면 자체 백테스트 구현을 접고 기성 엔진을 검토해야 한다 (1.3 / 4.8 / 9장) |
| `maFilter` 등 기존 지표 노드 삭제 | **기각.** 단순 조건의 예시이자 `Bundle` 계약의 참조 구현으로 값이 있다. **동결**하되 신규 추가만 금지 (5장) |
| CLI니까 Go/Rust로 재작성 | **기각.** 그 직관은 하루 수백 번 호출되는 개발 도구에서 온 것인데 이건 하루 3회 배치다. 결정적으로 **3장이 PyKRX·FDR·exchange_calendars 위에 서 있고 대체재가 없다.** 성능도 반대 방향 — 피처 행렬은 numpy가 C로 도는 구간이다 (2.1) |
| MCP 서버 제공 | **기각(보류).** 잘 설계된 CLI + 셸 접근이면 충분하고 유지할 프로세스만 늘어난다. 필요해지면 얇은 껍데기로 언제든 붙인다 (12.7) |
| Docker를 선택지로 남겨 두기 | **기각.** 리눅스 상시 가동 장비로 옮겨도 `uv`가 동일하게 동작한다. **쓰지 않을 것을 미리 유지하지 않는다** (8장) |
| 백테스트를 LLM이 자유롭게 호출 | **조건부 허용.** 막지는 않되 `backtest_runs` 카운터를 매번 출력하고 임계 초과 시 거부한다. 카운터가 출력되면 에이전트가 스스로 멈출 근거가 생긴다 (4.8) |
