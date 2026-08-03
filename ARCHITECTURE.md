# ARCHITECTURE.md

**marketscan** — 멀티마켓 횡단면 스크리너 · 신호 알림 CLI 설계서 (v0.5)

> **이 문서가 설계의 단일 출처다.** 구조를 바꾸는 작업 전에 읽고, 설계를 바꿨다면 함께 갱신한다.
> ⚠️ 표시는 **미결정 또는 외부 확인이 필요한 항목**이다.
> **남은 작업의 체크박스는 `README.md`가 갖는다.** 여기는 "왜 그렇게 정했나"만 적는다.
>
> 각 절의 번호는 소스 주석에서 참조된다 (`ARCHITECTURE.md 3.9` 형태). **번호를 바꾸지 않는다.**

**확정된 전제**

| 항목 | 결정 |
| :--- | :--- |
| 배포 형태 | **개인용 / Self-hosted**. 서비스화·멀티유저 없음 |
| 대상 시장 | **암호화폐 + 한국 주식 + 미국 주식**. 세 시장을 **하나의 유니버스**로 다루는 것이 존재 이유 |
| 1차 범위 | **신호 알림 전용**. 실주문은 검증 후 별도 단계 |
| 봉 단위 | **일봉·주봉 전용.** 분봉은 백테스트뿐 아니라 **판단 자체에서 제외** |
| 전략의 축 | **횡단면 랭킹**이 1급. 종목별 시계열 필터는 보조 |
| 전략 표현 | **파이썬 클래스 1개.** 지표를 노드로 쪼개지 않는다 |
| 인터페이스 | **CLI (Typer).** 웹 서버·캔버스 없음. ⚠️ 자동 실행 주체는 미결정 (11장) |
| 배포 | **`uv` + `[project.scripts]`.** Docker 없음 |
| 데이터 소스 | **플러그인 + 라우팅 표.** 파이프라인은 소스에 비종속 |

---

## 1. 개요

### 1.1 목적

**암호화폐·한국주식·미국주식을 하나의 유니버스로 매일 훑어, 볼 만한 소수의 후보를 사람에게 올려주는** 개인용 도구. 전략은 파이썬 클래스로 쓰고, 파이프라인은 그 전략을 데이터·LLM·알림과 엮는 얇은 배선이다.

**형태는 CLI다.** 하루 몇 번 `marketscan run --commit`이 돌면 결과가 stdout과 정적 HTML 리포트로 나온다. 사람과 LLM은 같은 CLI로 그 결과에 질문한다(12장).

⚠️ **그 실행을 무엇이 거는지는 아직 정하지 않았다** (11장 4b). **어느 쪽이든 CLI 표면은 바뀌지 않으므로** 지금 정하지 않는다 — `run --commit`을 누가 부르든 동작은 같다.

**이 시스템은 예측 기계가 아니라 주의력 기계다.** 시장을 맞히는 것이 아니라, 혼자서는 볼 수 없는 범위를 대신 보고 정해둔 규칙을 대신 지키는 것이 목적이다. **이 문서의 거의 모든 결정이 이 한 줄에서 파생된다.**

### 1.2 설계 원칙

| 원칙 | 의미 |
| :--- | :--- |
| **주의력 우선** | 값은 예측 정확도가 아니라 **훑는 범위와 규율**에서 나온다. 최종 판단은 사람이 한다 |
| **횡단면 우선** | 1급 연산은 "한 시점에 유니버스를 줄 세우는 것". 표본 수·시장 방향 상쇄·팩터의 정의가 모두 여기서 나온다 |
| **시장 중립 코어** | 엔진·노드는 "코인/주식"을 모른다. 거래소별 차이는 Provider와 Calendar 뒤로 숨긴다 |
| **결정성** | 같은 입력 + 같은 시각 → 같은 출력. 백테스트와 실행이 동일 코드 경로를 쓴다 |
| **시간 주입** | 노드는 `datetime.now()`를 호출하지 않는다. 모든 시각은 실행 컨텍스트가 준다 |
| **격리된 실패** | 노드 하나의 실패가 파이프라인 전체를 중단시키지 않는다 |
| **기본은 안전** | 부작용은 명시적 옵트인. 기본 모드는 `notify`, 기본 실행은 dry-run |
| **관측 가능성** | 모든 실행의 노드별 입/출력이 저장되어 사후 재현이 가능하다 |

### 1.3 비목표 (Non-goals)

- 초저지연(HFT) — **일봉 기준** 전략을 대상으로 한다.
- **장중(intraday) 판단** — 분봉으로 진입 타이밍을 재지 않는다(3.6). 왕복 비용이 분 단위 전략의
  기대 수익을 넘고, 그 구간의 상대는 호가창을 보는 참여자다. **못 이기는 게임이라 안 하는 것**이지
  데이터가 없어서 미루는 것이 아니다.
- **전략 탐색** — 지표 조합·파라미터를 백테스트로 뒤져 좋은 것을 찾는 용도로 쓰지 않는다.
  탐색 공간은 수백만인데 일봉 10년은 2,500행이라, 우연히 맞는 조합이 반드시 나오고
  그것을 구분할 표본이 없다. 백테스트의 용도는 4.8에서 재정의한다.
- **수익률 측정** — 이 시스템은 **진입 신호만** 만들고 청산(exit) 개념이 없다.
  총수익률·MDD·샤프는 청산 없이는 정의 자체가 불가능하므로 계산하지 않는다. 대신 신호 품질
  지표를 쓴다(4.8). Phase 5에서 실주문이 들어오기 전까지 이 선은 유지한다.
- 멀티테넌시·과금 · 범용 워크플로 엔진 · 비주얼 전략 편집 · 실시간 호가 스트리밍.

---

## 2. 시스템 구성

```text
      사람                 자동 실행 ⚠️미정          LLM (Claude Code 등)
       │ explain · stats          │ ingest --commit        │ explain --json
       │ backtest                 │ run --commit           │ strategy check
       ▼                          ▼                        ▼
┌──────────────────────────────────────────────────────────────┐
│  marketscan CLI (Typer) — 웹 서버 없음                        │
│  run · ingest · explain · signals · stats · strategy ·       │
│  describe · (backtest · verify: Phase 3)          (12장)     │
└───────────────┬──────────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────────┐
│  DAG Execution Engine                                         │
│  NetworkX 위상정렬 → 레벨별 asyncio 병렬 실행                   │
│  RunContext 주입 (now · mode · providers · ohlcv · log)        │
└──┬──────────┬──────────┬──────────┬──────────────────────────┘
   ▼          ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐
│Strategy│ │AI/LLM  │ │Logic   │ │Action        │
│Runner  │ │정성필터 │ │쿨다운  │ │Telegram/Log  │
│(클래스) │ │(P4)    │ │Sort/컷 │ │(→ Broker P5) │
└────┬───┘ └────────┘ └────────┘ └──────────────┘
┌────▼─────────────────────────────────────────────────────────┐
│  Market Abstraction Layer          ★ 멀티 마켓의 핵심          │
│  InstrumentRef · MarketCalendar · Routing Table · Connections │
└────┬─────────────────────────────────────────────────────────┘
     │ 읽기 전용 (ctx.ohlcv)
┌────▼──────────────┐
│  ohlcv_cache      │◀── 주기 수집 ── [ Ingestion Worker ]
│  (데이터 자산)      │                 레이트리밋·폴백·재시도
└───────────────────┘                        │
    ┌───────┬───────┬───────┬────────────────┴──┬─────────┐
    ▼       ▼       ▼       ▼                   ▼         ▼
  Upbit  Binance  PyKRX  yfinance   FDR      Alpaca   KIS/Toss
 (CCXT)  (CCXT)  (무인증)(무인증)  (무인증)   (P3+)    (P5 주문)
 └────────────── 일봉 · 키 불필요 ──────────────┘

┌───────────────────────────────────────────────────────────────┐
│  Storage: SQLite(WAL)                                         │
│  pipelines · pipeline_versions · strategy_versions · runs ·   │
│  node_runs · signals · bar_state · alerts_sent ·              │
│  ohlcv_cache · ingestion_jobs · llm_cache · backtest_runs     │
└───────────────────────────────────────────────────────────────┘
```

### 2.1 기술 스택

| 레이어 | 선택 | 비고 |
| :--- | :--- | :--- |
| **인터페이스** | **CLI (Typer)** | 웹 서버·프론트엔드 없음 (12장) |
| 리포트 | **정적 HTML 파일 생성** | 서빙하지 않고 `reports/`에 떨어뜨린다. 파일로 남아 나중에 비교할 수 있다. 차트는 Phase 3에서 uPlot vendoring |
| 스케줄 | ⚠️ **미결정** — OS 스케줄러 vs `serve` | 판단을 미룰 수 있는 이유는 **CLI 표면이 양쪽에서 같기 때문** (11장 4b) |
| 런타임 | Python 3.12, Pydantic v2 | 3장이 PyKRX·FDR·exchange_calendars 위에 서 있어 대체 언어가 없다 (부록 C) |
| DAG | NetworkX + 자체 asyncio 러너 | 파이프라인이 얕아져 존재감은 줄었지만 에러 브랜치·팬아웃에 계속 쓴다 |
| 지표 | **전략 작성자 자유** | 전략이 파이썬 클래스가 되면서 전역 결정이 아니게 됐다 |
| 시세 (일봉) | **PyKRX · yfinance · FDR · CCXT 공개 OHLCV** | **전부 무인증.** 일봉 고정(3.6)의 최대 이득 |
| 시세 (분봉) | — | **범위 밖.** Phase 5에서 주문 집행이 생기면 재검토 |
| 거래 캘린더 | `exchange_calendars` (XKRX · XNYS) | 휴장일·조기폐장·서머타임이 실제 값으로 들어온다 |
| DB | SQLite (WAL) | 단일 사용자 + 일봉 + 단일 프로세스면 **확정** (3.9 / 4.7) |
| LLM | **API · 로컬 커맨드 · 로컬 모델** (Provider 추상화) | 종류별로 결정성 보증이 다르다 (5장) |
| 패키징 | **uv** (`uv.lock` + `requires-python`) | Docker 없이 재현성 확보 (8장) |

> polars 전환은 **측정 전에 하지 않는다.** pykrx·yfinance가 pandas를 돌려주고 `Item.ohlcv`
> 계약도 pandas다. Phase 3에서 피처 행렬이 실제로 느리면 그 계산 구간에만 넣는다.

---

## 3. 멀티 마켓 추상화 ★

**이 시스템의 난이도는 대부분 여기에 있고, 동시에 존재 이유도 여기에 있다.** 코인과 주식을 하나의 유니버스로 다루려면 심볼 표기, 거래 시간, 캔들 마감, 통화가 전부 달라진다.

> **이 장이 freqtrade에도, 유사한 국내 도구에도 없는 부분이다.** 멀티마켓이 요건에서 빠지는
> 순간 이 프로젝트를 직접 만들 이유의 대부분이 사라진다 (10장 마지막 항목).

### 3.1 InstrumentRef — 통일 심볼 식별자

거래소마다 표기가 다르고(`KRW-BTC` / `BTC/USDT` / `005930` / `AAPL`), 티커가 시장 간 충돌할 수 있다. 항상 **venue를 붙인 정규 문자열**로 다룬다.

```python
@dataclass(frozen=True)
class InstrumentRef:
    venue: str              # "upbit" | "binance" | "krx" | "nasdaq" | "nyse"
    symbol: str             # "KRW-BTC" | "BTC/USDT" | "005930" | "AAPL"
    asset_class: str        # "crypto" | "equity"
    quote_currency: str     # venue 상수가 아니라 symbol에서 유도
    display_name: str

    @property
    def key(self) -> str:   # "upbit:KRW-BTC", "krx:005930"
        return f"{self.venue}:{self.symbol}"
```

**`quote_currency`는 venue에 고정하지 않는다.** 업비트에는 KRW 말고 BTC·USDT 마켓이 있고 바이낸스도 USDT 전용이 아니다. venue에 상수로 박으면 `upbit:BTC-ETH`의 결제 통화가 KRW로 잘못 붙어 3.7(통화 보존)과 알림 템플릿의 통화 기호가 함께 조용히 틀어진다. `VenueSpec.quote_style`이 symbol에서 통화를 뽑는 방법을 선언한다.

| `quote_style` | 대상 | 규칙 |
| :--- | :--- | :--- |
| `fixed` | 주식 (krx, nasdaq, nyse) | venue의 통화가 곧 결제 통화 |
| `dash_prefix` | 업비트 원본 표기 | `KRW-BTC` → 앞이 결제 통화 |
| `slash_suffix` | CCXT 통합 표기 | `BTC/USDT` → 뒤가 결제 통화 |

형식이 어긋나면 폴백 없이 파싱 단계에서 거부한다 — 통화를 추측으로 채우면 나중에 어디서 틀렸는지 찾을 수 없다.

### 3.2 MarketCalendar — 거래 시간

`as_of` 계산의 단일 출처다.

```python
class MarketCalendar(ABC):
    id: str
    tz: ZoneInfo
    def is_open(self, t: datetime) -> bool: ...
    def last_closed_bar(self, now: datetime, timeframe: str) -> datetime | None: ...
```

| 구현 | 대상 | 특징 |
| :--- | :--- | :--- |
| `Crypto24x7Calendar` | 코인 | 항상 열림. 일봉 경계(`UTC00` vs `KST00`)만 정하면 된다 |
| `ExchangeSessionCalendar` | 주식 (XKRX · XNYS) | **운영 기본값.** 휴장일·조기폐장이 실제 값 |
| `SessionCalendar` | 테스트·오프라인 | 주말만 제외. 운영에 쓰면 없는 세션의 신호가 난다 |

- ⚠️ 미국 시장에 **고정 오프셋(UTC-5)을 쓰면 안 된다.** 서머타임 때문에 한국 기준 개장이
  22:30 ↔ 23:30으로 움직인다. 반드시 `ZoneInfo("America/New_York")`.
- 캘린더가 아는 범위 밖을 물으면 `CalendarRangeError`로 터뜨린다. 조용히 `None`을 주면
  "오늘은 장이 없다"와 구분되지 않아 **패키지가 낡아 미래를 모르는 상황이 휴장으로 오해된다.**
- ★ **일봉의 인덱스는 세션 마감 시각이다** (규칙 15). 주식 소스는 하나같이 날짜만 주므로
  어댑터가 읽자마자 옮기고, 그 변환의 출처는 캘린더다 — `15:30 KST`를 상수로 박으면
  조기폐장 날 조용히 어긋난다.

### 3.3 Provider — 플러그인 구조

**시세만 주는 소스**와 **주문까지 되는 소스**가 섞이므로 인터페이스를 분리한다. 각 Provider가 자기 인증 스키마를 스스로 선언해 새 소스 추가를 파일 1개로 끝낸다.

```python
class MarketDataProvider(ABC):
    id: str
    venues: tuple[str, ...]
    credential_schema: type[BaseModel] | None   # None이면 무인증 내장 소스
    capabilities: ProviderCapabilities          # timeframes · adjusted · rate_limit …

    async def fetch_ohlcv(self, inst, timeframe, end, limit) -> pd.DataFrame: ...
    async def list_instruments(self, venue) -> list[UniverseEntry]: ...
    async def health_check(self) -> HealthStatus: ...

class BrokerProvider(Protocol):                 # Phase 5에서 활성화
    async def place_order(self, req: OrderRequest) -> OrderResult: ...
```

- ⚠️ **`end` 이후 캔들은 절대 반환하지 않는다** (규칙 2). 반환 직전 `assert_no_future()`.
- `list_instruments`의 **기본 구현은 거부한다.** 목록을 **모르는 것과 비어 있는 것은 다르다** —
  빈 리스트를 주면 Symbol Universe가 "0종목"이라 조용히 결론지어 그날 신호가 통째로 사라진다.

**`OrderRequest`는 수량과 금액을 모두 표현해야 한다.** 업비트·빗썸 원화 마켓의 **시장가 매수는 수량이 아니라 금액 기준**이다(CCXT의 `createMarketBuyOrderRequiresPrice`와 같은 지점). 수량만 받는 형태로 굳으면 이 주문을 아예 표현할 수 없다. 구현은 Phase 5지만 **인터페이스는 지금 확정한다** — `quantity | notional` 택일.

**어댑터 목록**

| 어댑터 | venue | 일봉 | 주문 | 인증 | 상태 |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `CcxtProvider` | upbit, binance, … | ✅ | (P5) | **불필요**\* | ✅ |
| `PykrxProvider` | krx | ✅ | ❌ | 없음 | ✅ |
| `YFinanceProvider` | nasdaq, nyse | ✅ | ❌ | 없음 | ✅ |
| `FdrProvider` | krx, nasdaq, nyse | ✅ | ❌ | 없음 | ✅ (+ **상장폐지 목록**) |
| `SyntheticProvider` | 전부 | ✅ | ❌ | 없음 | ✅ (테스트용) |
| `AlpacaProvider` | nasdaq, nyse | ✅ | (P5) | 키 | P3+ (yfinance 폴백용, 필수 아님) |
| `KisProvider` / `TossProvider` | krx, us | ⚠️ | ✅ | 키 | P5 주문 경로. ⚠️ Toss 오픈 API 스펙 미확인 |

\* 주문(P5)에는 키가 필요하다. 시세만 쓰는 현 범위에서는 불필요.

**일봉 고정(3.6)의 결과로 시세에 필요한 소스가 전부 무인증이 되었다.** 시세 때문에 증권사 API 키를 보관할 이유가 사라지므로 4.6의 자격 증명 관리는 **LLM·텔레그램에만** 적용된다 — 갖고 있지 않은 키는 새지 않는다.

**코인 거래소는 `CcxtProvider` 하나로 통합한다.** CCXT를 쓰는 이유가 정확히 이 통합이므로 `UpbitProvider` / `BinanceProvider`처럼 파일을 쪼개면 이득이 사라진다. 거래소별 예외만 `ccxt_quirks/`로 뺀다.

- **거래소당 CCXT 인스턴스는 하나만 만들어 재사용한다.** `enableRateLimit=True`는 인스턴스
  단위라, Ingestion Worker가 여러 몫을 몰아 호출하면 프로세스 전역 쿼터가 깨진다.
- **`capabilities`는 손으로 선언하지 않고 `ex.has` / `ex.timeframes`에서 유도한다.** 수기 표는
  언젠가 실제 능력과 어긋나고, 그러면 라우팅이 못 주는 소스로 계속 흘러간다. OHLCV를 못 주는
  거래소는 `timeframes`가 비어 라우팅 표에서 **자동 제외**된다.
- ⚠️ 빗썸의 `fetchOHLCV` 지원 여부는 자료마다 엇갈린다. 문서를 믿지 말고 런타임 확인한다.

### 3.4 Connection과 라우팅 — 소스는 노드가 아니다

**설계 결정**: 데이터 소스를 파이프라인의 노드로 만들지 **않는다.**

| 소스를 노드로 만들면 | 결과 |
| :--- | :--- |
| 파이프라인이 소스에 종속 | yfinance→Alpaca 교체 시 파이프라인을 뜯어고쳐야 함 |
| 멀티마켓 혼합이 깨짐 | 시장마다 소스 노드+Merge를 매번 배치. Fresh Bar Gate(3.5) 무력화 |
| 폴백 불가 | 소스 하나가 죽으면 파이프라인 실패 |
| 캐시 공유 어려움 | 파이프라인마다 같은 종목을 중복 호출 |

**대신 3계층으로 나눈다.**

**① Connections** — 소스별 자격 증명. 무인증 소스(PyKRX·yfinance·FDR·CCXT 공개)는 등록 없이 항상 활성이므로, 현 범위에서 이 계층은 사실상 비어 있다.

**② Routing Table** — `(venue, timeframe) → 소스 우선순위`. 앞 소스가 실패하면 다음으로 폴백한다.

```
upbit  · *  → [ccxt.upbit]          # 코인은 폴백 없음 — 거래소마다 상장 종목이 달라 대체가 성립하지 않는다
krx    · *  → [pykrx, fdr]
nasdaq · *  → [yfinance, fdr]
```

**폴백은 조용히 넘어가면 안 된다.** 폴백이 발동하면 소스가 바뀌고, 소스가 바뀌면 수정주가 정책 차이로 지표가 불연속해지며(3.8), 같은 `ctx.now`에 다른 결과가 나와 **`ctx.now` 주입으로 확보한 백테스트–실행 동치성이 소스 레이어에서 무너진다.** 그래서 폴백 발동은 파이썬 로그가 아니라 **실행 이력에** 남긴다.

- `FetchResult.failed_sources` → `ctx.log.warning` + `Item.meta["fallback_from"]` → `node_runs`.
- `ohlcv_cache.source_id`(4.7)와 함께 보면 "어느 구간이 어느 소스로 채워졌는지"가 복원된다.
- ⚠️ **미래 참조(`LookAheadError`)만은 폴백하지 않고 그대로 터뜨린다.** 다음 소스로 넘어가면
  버그가 정상 결과로 위장된다.
- **유니버스 조회는 폴백하지 않는다.** 다른 소스에서 목록을 받으면 그날의 후보 집합이 통째로
  달라진다 — 시세 한 종목이 폴백되는 것과 무게가 다르다.

**③ 노드의 `source` 파라미터** — 기본 `"auto"`(라우팅 표). 특정 소스 ID를 적으면 그것만 쓴다. 노드화로 얻으려던 명시적 통제권은 이 파라미터 하나로 확보된다.

이 구조에서 **새 소스 추가 = Provider 파일 1개**이며, 기존 파이프라인은 수정할 필요가 없다.

### 3.5 혼합 파이프라인 사용성 — Fresh Bar Gate

**코인과 주식을 한 파이프라인에 넣었을 때 가장 흔한 문제**: 미국장이 닫혀 있는 시간에 파이프라인이 돌면 주식 쪽은 어제와 똑같은 캔들을 다시 읽고, 같은 신호를 매번 재발생시킨다.

해결: **item 단위로 "새로 마감된 캔들이 있는지" 판정**하고, 없으면 조용히 제외한다.

```
Market Data
  ├─ item별로 calendar.last_closed_bar(ctx.now, timeframe) 계산
  ├─ 직전 실행의 as_of와 같으면 → stale
  └─ skip_stale: true (기본) → stale item 제외
```

덕분에 하나의 파이프라인에 코인·한국·미국을 함께 넣고 **매시간 실행**해도 각 시장이 장중에만 판정된다. 사용자는 캘린더를 신경 쓸 필요가 없다.

**★ 봉의 소비는 실행이 성공한 뒤에만 확정한다.** 읽는 시점에 바로 "봤다"고 기록하면, 하류의 알림 노드가 실패했을 때도 봉이 소비된 것으로 남아 **재실행하면 stale로 걸러지고 그 신호는 영영 사라진다.** 그래서 `bar_state.stage()`로 예약만 하고, 엔진이 `SUCCESS`일 때만 `commit()`한다.

- `partial`도 커밋하지 않는다 — 실패한 노드가 하필 알림 노드였을 수 있다.
- 커밋을 미뤄서 생기는 최악은 **알림 중복**인데 `dedup_key` UNIQUE가 이미 막는다(4.5).
  **유실을 막을 장치는 없으므로**, 겹치는 쪽이 잃는 쪽보다 안전하다.

**봉 상태는 SQLite에 남는다** (`bar_state`). CLI로 전환하며 프로세스가 매 실행마다 새로 떠서, 메모리 저장소로는 `last_seen`이 항상 비어 **게이트가 사실상 무동작**이었다.

| 실행 | 저장소 |
| :--- | :--- |
| `--commit` | `SqlBarState` — 봉을 실제로 소비 |
| dry-run (DB 있음) | `SqlBarState(readonly)` — **읽기만** |
| dry-run (DB 없음) | `InMemoryBarState` — DB 파일조차 만들지 않는다 (12.1) |

dry-run도 **읽는다.** 읽지 않으면 `run`과 `run --commit`이 서로 다른 종목 집합을 보게 되어 dry-run이 실제 실행을 예측하지 못한다. **쓰지 않는 것과 읽지 않는 것은 다르다.** `bar_key`는 `(노드, 심볼, 타임프레임)`이라 파이프라인이 달라도 노드 id가 같으면 충돌하므로 `pipeline_id`로 한 겹 더 나눈다.

### 3.6 타임프레임 — 일봉 고정 ★

**판단 단위는 `1d`와 `1w`뿐이다.** v0.4는 "백테스트는 일봉 이상, 실매매 알림은 분봉 허용"으로 나눴지만, v0.5는 **분봉을 판단에서 통째로 제외한다.**

**이유는 데이터가 아니라 비용 구조다.** 왕복 비용은 업비트 약 0.15%, KRX 약 0.25%(매도 시 거래세⚠️), 미국 0.02~0.05%다. 하루 왕복 1회면 연 250회고, KRX 기준 **연 60% 이상을 비용으로 먼저 낸다.** 그리고 그 구간의 상대는 호가창을 보는 참여자다.

**세 가지 결정을 분리한다.** 하나로 뭉치면 판단이 흐려진다.

| | 결정 | v0.5 |
| :--- | :--- | :--- |
| **a** | 전략이 판단에 쓰는 타임프레임 | **`1d` / `1w`만** |
| **b** | Ingestion Worker가 수집·보관하는 것 | **일봉만.** 안 쌓으면 나중에 열 수도 없다 — 의도적으로 닫는다 |
| **c** | 실행 빈도 | 시장별 마감 후, 하루 3회 수준 |

**c 때문에 Fresh Bar Gate(3.5)는 그대로 필요하다.** 미국장 마감 후 실행에서 한국 종목은 stale이기 때문이다. 일봉이 되었다고 없애면 안 된다.

**되돌릴 수 있게 막는다 — 타입 계층이 아니라 정책 계층에서.**

- ✅ `Item.timeframe: str` 유지, `ProviderCapabilities.timeframes` 유지, 정규화 표 유지
- ✅ 파이프라인 검증기에서만 `1d` / `1w`로 제한
- ❌ `Literal["1d"]`로 타입을 굳히거나 캘린더의 분봉 분기를 삭제하는 것

이렇게 두면 나중에 여는 것이 **정책 변경 + Provider 추가**지 재설계가 아니다.

내부 표기는 `1m · 5m · 15m · 30m · 1h · 4h · 1d · 1w`로 통일하고 어댑터가 변환한다. `1w`는 **`1d`에서 리샘플**하므로 주봉 소스가 따로 필요 없다.

⚠️ **KST 09:00은 UTC 00:00과 같은 순간이다.** 업비트 일봉 기준이 곧 `UTC00`이며, 실제 선택지는 `UTC00`과 한국 자정(`KST00` = UTC 15:00) 둘이다. **일봉이 유일한 판단 단위가 되면서 이 선택의 중요도가 올라갔다** — 코인 신호 전체가 이 경계에 좌우된다 (11장 1번).

### 3.7 통화

`Item.quote_currency`를 항상 보존하고, 알림 템플릿은 통화 기호와 함께 출력한다. 서로 다른 통화 금액을 **합산하는 로직은 넣지 않는다** (환율 도입 전까지). 유동성 컷도 venue별로 따로 건다 — 원과 달러를 섞어 한 번에 자르면 비교 자체가 성립하지 않는다.

### 3.8 수정주가 (Adjusted Price) ★

**소스를 여러 개 쓸 때 지표를 조용히 틀어지게 만드는 1순위 원인이다.**

액면분할·유상증자가 일어나면 과거 가격이 소급 조정되는데 **소스마다 조정 방식과 기본값이 다르다.** yfinance는 조정가가 기본이고 PyKRX는 옵션이다. 같은 종목의 20일 이동평균이 소스에 따라 다른 값이 나오고, 폴백이 발동해 소스가 바뀌면 **어제와 오늘의 지표가 불연속**해진다.

**규칙**

- `adjusted` 정책을 **파이프라인 전역으로 하나 고정**한다 (기본 `true`).
- **캐시 키에 `adjusted`를 포함**한다 (규칙 8). 섞이면 원인 추적이 불가능해진다.
- **기록하는 값은 설정이 아니라 소스가 실제로 준 것이다.** 코인에는 액면분할·배당이 없어
  adjusted 개념 자체가 없는데 설정을 그대로 베끼면 조정가를 받은 것처럼 남는다.
  `capabilities.adjusted`가 `always`/`never`면 소스가 결정하고, `optional`일 때만 설정이 의미를 갖는다.
- 두 소스가 같은 날 종가를 다르게 주면 경고한다 (정합성 검증 — 3.9의 캐시 쓰기가 잡는다).
- 분할·병합 이벤트를 감지하면 해당 종목 캐시를 무효화하고 재수집한다.

### 3.9 Ingestion Worker — 캐시는 성능이 아니라 데이터 자산

노드가 매 실행마다 외부 API를 직접 호출하면 **스크리너로 200종목을 돌리는 순간 무료 API가 차단된다.** 수집을 실행 경로에서 분리한다.

```
[Ingestion Worker] ──주기 수집──▶ [ohlcv_cache] ◀──읽기 전용── [MarketData 노드]
```

| 이점 | 설명 |
| :--- | :--- |
| 레이트 리밋 단일 지점 | 소스별 쿼터를 한 곳에서만 관리 |
| 중복 호출 제거 | 파이프라인 3개가 같은 종목을 써도 API 호출은 1회 |
| 장애 격리 | 소스가 죽어도 캐시된 봉으로 파이프라인은 계속 동작 |
| 백테스트 가속 | 외부 호출 없이 캐시만 재생 |

**핵심 관점**: 무료 소스는 언제든 깨진다는 전제로, `ohlcv_cache`를 "성능 최적화"가 아니라 **영구 보관하는 데이터 자산**으로 다룬다. yfinance가 막혀도 이미 쌓인 이력으로 백테스트는 계속 돈다. **그래서 삭제 경로를 두지 않았다** (규칙 16).

- 수집 대상은 **활성 파이프라인이 참조하는 instrument의 합집합**에서 자동 도출한다. 유니버스
  노드를 실제로 **실행해서** 얻는다 — 컷 조건을 다시 구현하면 파이프라인이 훑는 것과 캐시가
  담는 것이 갈라지고, 갈라진 종목은 실행 때 캐시 미스로 조용히 소스를 두드린다.
- **일봉 전용이므로 저장소 논쟁이 끝났다 — SQLite 확정.** 2,000종목 × 10년이면 약 500만 행.
  v0.4가 미결정으로 남겼던 Parquet/DuckDB 분리는 **분봉을 쌓을 때만 발생하는 문제**였고
  (200종목 × 1분봉 × 3년 = 2억 행), 3.6에서 분봉 수집을 닫으면서 사라졌다.
- ★ **상장폐지·거래정지 종목도 수집 대상이다.** 살아 있는 종목만 쌓으면 4.8의 서바이버십
  편향을 데이터 레이어에서 이미 만들어 놓는 셈이다. **폐지 종목은 폐지 시점을 `end`로 잡는다** —
  오늘 기준으로 조회하면 그 구간에 봉이 없어 **빈 결과가 성공처럼 보인다.**
- **노드는 캐시 구현을 모른다.** 보는 것은 "캐시를 읽는다"는 인터페이스뿐이므로 뒤를 교체할 수
  있다. 스키마를 SQLite 전용 문법으로 굳히지 않는다.

**구현 (Phase 2)**

노드는 `ctx.ohlcv.load(...)` 하나만 부른다. 뒤에는 캐시를 먼저 보는 `CachedSource`나 소스를 직접 부르는 `DirectSource`가 꽂힌다. `marketData`의 `cache` 파라미터가 `auto`(캐시 우선, 부족하면 소스) / `off`(항상 소스) / `only`(외부 호출 없음)를 고른다.

- **쓰기는 `--commit`에서만, 읽기는 언제나.** dry-run이 캐시를 채우면 12.1의 "읽기 전용 실행은
  DB 파일조차 만들지 않는다"가 깨지고, 반대로 읽지 않으면 dry-run이 실제 실행을 예측하지
  못한다. `bar_state`(3.5)와 같은 판단이다.
- ★ **`adjusted`를 두 단계로 정한다.** 캐시 키에 들어가므로 읽기 **전에** 값이 정해져야 하는데,
  실제 값은 어느 소스가 응답했는지에 달려 있다. 그래서 **읽을 때는 라우팅 후보의 capability로
  예측**하고, **쓸 때는 실제로 응답한 소스의 값**으로 쓴다. 예측이 틀리면 다음 실행이 캐시를
  못 맞히는 것으로 끝나지만(성능 손해), 쓸 때 예측값을 쓰면 조정가와 비조정가가 한 키에 섞인다.
- **정합성 검증이 캐시 쓰기에 얹혀 있다.** 기존 봉과 새 봉을 비교하는 김에, 종가가 다른데
  `source_id`도 다르면 경고를 만든다(3.8). 같은 소스가 값을 바꾼 것은 수정주가 재계산이므로
  경고하지 않는다.
- **재수집은 `ingestion_jobs.last_success_at`으로 건너뛴다.** `last_bar_time`으로 판정하면
  거래정지·폐지 종목은 그 봉이 애초에 없어서 **영원히 다시 수집한다.**

---

## 4. 실행 코어

### 4.1 노드 간 데이터 모델 — `Bundle` / `Item`

노드 간 타입이 `DataFrame → Filtered List`로 바뀌면 필터를 두 개 이상 연결할 수 없다(두 번째가 받을 DataFrame이 없음). 모든 노드가 **같은 봉투(envelope)** 를 주고받는다.

```python
@dataclass
class Item:
    instrument: InstrumentRef
    timeframe: str
    as_of: datetime                # 기준이 되는 "마감된" 캔들 시각 (UTC 저장)
    ohlcv: pd.DataFrame            # index=UTC datetime, [open,high,low,close,volume]
    features: dict[str, Any]       # 지표: {"sma_20": 98_400_000}
    tags: dict[str, Any]           # 판단: {"ma_cross": "golden"}
    meta: dict[str, Any]           # source, adjusted, fallback_from …

@dataclass
class Bundle:
    items: list[Item]
    context: dict[str, Any]        # 파이프라인 전역 값 (universe, 시장 지수 …)
```

**규칙**

- 필터 노드는 `ohlcv`를 **보존한 채** `items`만 걸러내고 근거를 `features`/`tags`에 남긴다
  → 필터 체이닝 가능 (규칙 4).
- **빈 `Bundle`도 정상 출력이다.** 하위 노드는 빈 입력 시 no-op이고, 종료 코드도 `0`이다(12.3).
- 단일 심볼 전략과 다중 심볼 스크리너를 **같은 구조로** 표현한다. `len(items)`만 다르다.
- ★ **`Bundle`은 곧 횡단면이다.** `items`가 여러 개인 상태가 예외가 아니라 **기본**이다.
  "한 시점의 유니버스 전체"가 하나의 `Bundle`이고 여기에 순위를 매기는 것이 1급 연산이므로,
  **`rank` 계열 연산이 카탈로그의 중심에 있어야 한다**(5장).
- **item의 식별 키는 `(instrument.key, timeframe)`이다.** 종목만으로 식별하면 Merge가 같은
  종목의 일봉 item과 시간봉 item 중 한쪽을 소리 없이 덮어쓴다.
- ⚠️ **`Bundle.merge`는 `context`를 덮어쓴다.** items와 달리 합집합이 아니다. 같은 context 키를
  쓰는 노드 둘을 한 노드에 물리면 앞엣것이 조용히 사라진다 — 이것이 `symbolUniverse`가
  venue를 **목록으로** 받아야 하는 이유다(5장). 여기에 키별 예외를 넣지 않는다: context를
  쓰는 노드가 늘 때마다 같은 고민이 반복된다.
- DataFrame은 프로세스 내 참조로 전달하고, 저장 시에는 요약만 기록한다.

### 4.2 노드 인터페이스

```python
class BaseNode(ABC):
    type: ClassVar[str]                     # "marketData"
    ParamsModel: ClassVar[type[BaseModel]]
    inputs / outputs: ClassVar[tuple[str, ...]]
    requires_input: ClassVar[bool]          # False면 상류 없이도 실행된다
    sends_external_messages: ClassVar[bool] # True면 엔진이 run에서 아예 실행하지 않는다 (12.2)

    async def run(self, inputs: dict[str, Bundle],
                  params: BaseModel, ctx: RunContext) -> dict[str, Bundle]: ...
```

```python
@dataclass
class RunContext:
    run_id: str
    mode: ExecutionMode             # backtest | shadow | notify | paper | live
    now: datetime                   # ⚠️ 노드는 반드시 이 값만 사용. datetime.now() 금지
    settings: PipelineSettings      # user_timezone · adjusted · daily_boundary …
    providers: ProviderRegistry
    calendars: dict[str, MarketCalendar]
    ohlcv: OhlcvSource              # 봉을 얻는 유일한 창구 (3.9)
    bar_state: BarStateStore        # Fresh Bar Gate (3.5)
    signals: SignalSink             # --commit 여부로 갈아 끼운다 (12.2)
    commit: bool                    # 기본 False
    log: NodeLogger
```

`ctx.now` 강제가 **백테스트–실행 동치성의 핵심**이다. 백테스트는 이 값만 과거로 되돌려 같은 노드 코드를 재생한다.

**★ 부작용 분기를 노드에 심지 않는다.** `--commit` 여부는 CLI가 `ctx.signals` 배출구를 갈아 끼우는 것으로 표현한다(`CollectingSink` ↔ `SqlSignalSink`). 노드마다 `if ctx.commit:`을 두면 언젠가 하나를 빠뜨리고, 그날 봉이 소리 없이 소비된다.

#### 전략 인터페이스 ★

**v0.4는 전략을 지표 노드의 조합으로 표현했다. v0.5는 파이썬 클래스 하나로 표현한다.**

노드 조합 방식은 "쓸 수 있는 모든 조건을 시스템이 미리 제공해야 한다"는 짐을 진다. 지표 × 파라미터 × 조합은 끝이 없고, 같은 접근을 택한 도구들이 지표 7~10개 수준에서 멈춰 있다. 그리고 이 시스템의 사용자는 **파이썬을 쓰는 본인 한 명**이다.

```python
class Strategy(Protocol):
    id: ClassVar[str]
    timeframe: ClassVar[Literal["1d", "1w"]]     # 3.6
    startup_candles: ClassVar[int]               # 워밍업 부족 종목의 제외 기준
    score_feature: ClassVar[str | None]          # 기본 rank가 줄 세울 값
    Params: ClassVar[type[BaseModel]]            # ★ 폼 자동 생성이 살아남는 지점

    def compute(self, item, p, ctx) -> Item: ...    # 시계열 — 종목별 지표
    def rank(self, bundle, p, ctx) -> Bundle: ...   # 횡단면 — ★ 이 훅이 중심
    def select(self, bundle, p, ctx) -> Bundle: ... # 횡단면 — 최종 컷. 여기서만 item을 버린다
```

**규칙**

1. **`compute`는 인과적이어야 한다.** `rolling` · `ewm` · `shift(+n)`은 안전하고,
   **`shift(-n)` · `center=True` · `bfill`은 미래를 본다.** 런타임에 강제할 수 없지만
   `marketscan strategy check`가 AST로 상당 부분 잡는다(12.6). **통과가 보장은 아니므로**
   4.8의 난수 신호 테스트가 사후 방어선으로 남는다.
2. **전략에 Provider·Cache 핸들을 주지 않는다.** 이미 `end`로 잘린 DataFrame만 받으므로
   데이터를 통한 미래 참조가 구조적으로 불가능하다.
3. **`Params`는 Pydantic 모델로 선언한다.** 노드 방식의 유일한 실질적 이득 — JSON Schema →
   폼·`--param` 자동 생성 — 이 그대로 유지된다.
4. **`rank` / `select`는 기본 구현을 제공한다.** 단일 종목 전략은 `compute`만 채우면 된다.
5. **컷은 `top_n` / `top_pct` 헬퍼를 쓴다** — 절삭 경고가 함께 남는다 (조용한 절삭 금지).

> **freqtrade 호환은 목표가 아니다.** 근거는 부록 C의 기각 표.

### 4.3 실행 엔진

1. **검증** — 사이클 검출, 핸들 연결 유효성, 파라미터 Pydantic 검증. 실패 시 실행 거부.
2. **위상 정렬** — `nx.topological_generations()`로 레벨 분할.
3. **레벨별 병렬** — 같은 레벨은 `asyncio.gather`. 동시 실행 수 제한.
4. **노드 상태** — `pending → running → (success | error | skipped)`.
5. **분기** — Condition Splitter는 한쪽에만 출력. 미선택 브랜치는 `skipped` 전파.
6. **에러 정책** (노드별 `on_error`)

| 정책 | 동작 |
| :--- | :--- |
| `fail` | 실행 중단 |
| `skip` | 해당 노드 skip + 하위 전파 |
| `route` | `error` 핸들로 오류를 내보내 별도 브랜치 실행 |
| `retry` | 지수 백오프 재시도 후 위 정책으로 폴백 (기본: 외부 API 노드) |

7. **기록** — 노드 진입/종료마다 `node_runs`에 입출력 요약·소요시간·에러 저장.

### 4.4 캔들 마감 처리

미완성 캔들로 판단하면 지표가 흔들려 **신호가 생겼다 사라지는** 전형적 버그가 난다.

- Market Data 기본 `closed_only: true` — 마지막 미완성 봉 제거.
- `Item.as_of`는 항상 **마감된 마지막 캔들 시각** (캘린더가 판정).
- 모든 시각은 **tz-aware UTC 저장**, 표시할 때만 `user_timezone` 변환 (규칙 5).

### 4.5 중복 알림 방지 (Dedup / Cooldown)

알림 전용 단계에서도 중복 발화는 실사용을 망친다(재시도·스케줄 중복·수동 재실행).

```
dedup_key = sha256(pipeline_id | node_id | instrument.key | as_of | signal_kind)
```

- `signals` · `alerts_sent`의 `dedup_key`에 UNIQUE → 같은 캔들 기준 신호·알림은 **한 번만**.
- **Cooldown 노드**: "같은 종목은 N시간 내 재알림 금지" 같은 완화 조건도 별도 제공.
- Phase 5에서 이 키 스킴이 그대로 `orders.idempotency_key`가 된다 (`| side` 추가).

### 4.6 자격 증명

키를 **파이프라인 정의에 직접 넣지 않는다** (export·공유 시 유출). 노드는 Connection ID만 참조한다(규칙 7).

- `connections` 테이블에 암호화 저장. 마스터 키는 환경변수 / OS 키체인.
- 로그·`node_runs` 저장 시 키 패턴 자동 마스킹. `SecretStr`로 실수 노출 방지.
- 증권사·거래소 키는 **읽기 전용/거래 전용을 분리**하고 **출금 권한은 절대 부여하지 않는다.**
- **시세에는 자격 증명이 필요 없으므로**(3.3) 이 계층의 대상은 텔레그램 토큰과 LLM 키뿐이다.

### 4.7 저장소

SQLite + **WAL 모드**. SQLAlchemy를 써서 SQLite 전용 문법을 피한다 — `ohlcv_cache`가 커지면 뒤를 갈아 끼울 수 있어야 한다.

| 테이블 | | 역할 |
| :--- | :---: | :--- |
| `pipelines` / `pipeline_versions` | ✅ | 메타 / DAG 스냅샷 **(불변)**. 실행은 항상 특정 버전 참조 |
| `strategy_versions` | ✅ | **전략 소스 해시 + 전문 스냅샷**. 아래 참조 |
| `runs` / `node_runs` | ✅ | 실행 단위 / 노드별 입·출력 요약·로그·에러 |
| `signals` | ✅ | 생성된 신호 + `dedup_key` UNIQUE + `acted`(오버라이드 추적) |
| `bar_state` | ✅ | Fresh Bar Gate의 직전 `as_of` (3.5). DDL이 `bar_state.py`에 있다 — 아래 참조 |
| `ohlcv_cache` | ✅ | **데이터 자산.** 아래 참조 |
| `ingestion_jobs` | ✅ | 수집 대상·마지막 성공 시각·연속 실패 카운트 |
| `alerts_sent` | ⬜ | 발송 알림 + `dedup_key` UNIQUE. `serve`가 생길 때 (11장 4b) |
| `connections` / `source_routes` | ⬜ | 자격 증명(암호화) / 라우팅 표. 현재 라우팅은 코드 상수 |
| `llm_cache` | ⬜ P4 | 프롬프트 해시 → 응답. **`deterministic=false`는 캐시하지 않는다** (5장) |
| `backtest_runs` | ⬜ P3 | **전략 해시 × 파라미터 조합 × 실행 시각.** 다중검정 카운터의 근거 (4.8) |
| `instruments` / `market_calendar` | ⬜ | 심볼 마스터 / 휴장일 캐시 |
| `orders` / `positions` | ⬜ P5 | |

> **`bar_state`만 SQLAlchemy가 아니라 `sqlite3`을 직접 쓴다.** `BarStateStore` 프로토콜의
> 메서드가 동기인데(엔진이 `execute()` 한복판에서 `commit()`을 부른다) async로 바꾸면
> 프로토콜·노드·러너가 줄줄이 딸려 온다. 반대로 이 테이블은 키 하나에 시각 하나뿐이라 ORM이
> 줄 것이 없다. 컬럼 표현이 두 곳에 생기면 언젠가 어긋나고, 어긋난 날 Fresh Bar Gate가
> **조용히** 무동작이 되므로 DDL도 같은 파일에 둔다.

**`ohlcv_cache` 스키마 주의점**

```
PK: (venue, symbol, timeframe, adjusted, bar_time)
    + source_id     어느 소스에서 받았는지 (정합성 추적용)
    + ingested_at
```

- `adjusted`를 **키에 포함**해야 조정가/비조정가가 섞이지 않는다 (3.8 / 규칙 8).
- `source_id`를 남겨야 폴백으로 소스가 바뀐 구간을 사후에 찾을 수 있다.
- `bar_time`은 **마감 시각**이다. 시가 시각이 아니다 (규칙 15).
- **이 테이블은 삭제하지 않는다.** 무료 소스가 막혀도 남는 유일한 자산이다. 백업 대상.

**⚠️ `UtcDateTime`을 쓴다.** SQLite는 `DateTime(timezone=True)`를 줘도 tzinfo를 저장하지 않아서, 그냥 두면 읽을 때 naive로 새어 나온다 (규칙 5).

**버전 불변성**: 실행 중 파이프라인을 편집해도 진행 중인 Run은 영향받지 않는다. 저장 시 새 버전을 만들고 Run은 버전을 고정한다.

**★ 전략 코드도 버전에 묶는다**

전략이 파이썬 클래스가 되면서 새로 생긴 구멍이다. 파이프라인이 `strategies/momentum.py`를 **이름으로만** 참조하면, 그 파일을 고치는 순간 **과거 버전이 무엇이었는지가 소급으로 바뀐다.** "그때 그 신호가 어떤 전략에서 나왔는지"를 잃는 것이고, `pipeline_versions`를 불변으로 둔 이유가 그대로 무너진다.

- 전략의 **정본은 `strategies/` 디렉터리의 파일**로 둔다. IDE·git·리뷰를 쓸 수 있는 쪽이 낫다.
- 소스의 **SHA-256을 `pipeline_versions`에 기록**하고, 전문을 `strategy_versions`에 스냅샷한다.
- 실행·리플레이 시점에 **해시가 다르면 경고**한다. 백테스트 리포트에도 표시한다 —
  "이 리포트는 현재 코드가 아니라 버전 N의 코드로 계산됨"을 알 수 있어야 한다.

### 4.8 백테스트

**★ 용도를 먼저 못박는다**

> **"이 전략이 돈이 되나?" (❌) → "내 구현이 안 틀렸나?" (✅)**

백테스트를 전략 탐색에 쓰면 진다. 탐색 공간은 수백만인데 일봉 10년은 2,500행이라, **우연히 잘 맞는 조합이 반드시 나오고 그것을 잡음과 구분할 표본이 없다.** 파라미터를 튜닝하는 순간 그 성과는 in-sample이 되어 의미를 잃는다.

대신 **이미 공개되어 있고 오래 검증된 팩터를 표준값 그대로** 쓰고(모멘텀 12-1, 밸류 PBR/PER, 퀄리티 ROE, 저변동성), 백테스트는 **수정주가 처리·상장폐지 반영·미래 참조 여부를 확인하는 디버깅 도구**로 쓴다. 남의 논문 값은 나에게 out-of-sample이지만 내가 고른 값은 아니다.

**리플레이** — 동일 코드를 시각만 바꿔 재생한다.

```python
for bar_time in calendar.bars(start, end, timeframe):   # timeframe ∈ {1d, 1w}
    ctx = RunContext(mode="backtest", now=bar_time, ...)
    await engine.execute(pipeline_version, ctx)
```

| 이슈 | 대응 |
| :--- | :--- |
| **미래 참조** | Provider는 `end` 이후 캔들을 절대 반환하지 않는다. backtest 모드에서 assert |
| **LLM 비용/비결정성** | `(model, prompt_hash, input_digest)` 키로 `llm_cache` 저장 → 재실행 무료·결정적 |
| **LLM 학습 데이터 누출** | 캐시로는 못 막는 별개 문제. 아래 참조 |
| **유니버스 서바이버십** | 현재 시점으로 산출하면 상폐·편출 종목이 통째로 빠진다. 아래 참조 |
| **시장별 캘린더** | 백테스트 루프도 캘린더 기준으로 봉을 생성 (휴장일 건너뜀) |
| **커버리지** | 시작 전 `ohlcv_cache` 커버리지를 확인해 구간을 못 채우면 **사유와 함께 거부** |
| **체결 가정 (Phase 5)** | 다음 봉 시가 체결 기본, 슬리피지·수수료·세금 파라미터화 |

**성과 지표 — 수익률이 아니라 신호 품질을 측정한다**

청산(exit) 개념이 없는 상태에서 총수익률·MDD·샤프를 계산하려면 청산 규칙을 가정해야 하고, 그 순간 체결 가정·수수료·세금이 줄줄이 딸려 와 Phase 5를 앞으로 끌어온다. 대신 **신호 이후 무슨 일이 일어났는지**를 청산 가정 없이 측정한다.

| 지표 | 정의 |
| :--- | :--- |
| **Forward return 분포** | 신호 N봉 후 수익률의 median·IQR (N = 1·5·20) |
| **Hit rate** | N봉 후 수익률이 양수인 비율 |
| **IC** | 신호 점수 ↔ 후속 수익률의 순위상관 |
| **벤치마크 대비 초과수익** | 같은 구간의 KOSPI / S&P500 / BTC 대비 |
| **신호 건수·종목 분산** | 알림 폭주와 특정 종목 편중을 잡는다 |
| ★ **오버라이드 성과** | **사용자가 무시한 신호**의 사후 수익률. 아래 참조 |

**★ 오버라이드 추적**

정체성이 "주의력 기계 + 규율 기계"(1.2)라면, **측정해야 할 것은 전략 성과만이 아니라 사용자가 규율을 지켰는지**다. `signals.acted`에 응답을 기록한다.

| 결과 | 해석 |
| :--- | :--- |
| 무시한 신호가 평균적으로 **나빴다** | 사용자의 재량에 값이 있다. 시스템을 후보 필터로 쓴다 |
| 무시한 신호가 평균적으로 **좋았다** | 재량이 손해다. 더 믿거나, 왜 못 믿는지를 규칙으로 바꿔야 한다 |

**이 시스템에서 가장 확실한 가치가 여기서 나온다** — 개인 투자자가 이 숫자를 보는 일이 거의 없기 때문이다.

**⚠️ LLM 노드가 있는 파이프라인의 백테스트는 낙관 편향된다**

`llm_cache`는 **재실행 결정성**만 보장할 뿐 학습 데이터 누출은 못 막는다. 2023년 캔들을 2026년 모델에게 물으면, 그 모델은 이미 해당 종목의 후속 주가를 학습했을 수 있다. Provider의 `end` 컷은 **가격 데이터만** 보므로 이걸 못 잡는다.

- LLM 노드가 포함된 백테스트 결과에 **경고 배지**를 붙인다.
- **LLM 전략의 1급 검증 경로는 `shadow` 모드다.** 백테스트는 참고 수단으로 격하한다.

**⚠️ 유니버스는 백테스트에서 point-in-time으로 산출한다**

거래소가 주는 목록은 언제나 "지금"이다. 거래대금 상위 30개를 오늘 뽑아 2년치를 리플레이하면, 2년간 살아남아 상위에 든 종목만 보게 되어 성과가 구조적으로 부풀려진다. **가격만 보는 look-ahead assert로는 잡히지 않고, `strategy check`의 AST에도 흔적이 남지 않는다** (규칙 14).

- 백테스트에서 유니버스는 **각 `bar_time` 기준으로** 산출한다 (PyKRX는 날짜별 시총 조회 가능).
- point-in-time 산출이 불가능한 소스는 backtest 모드에서 **사유를 명시하고 거부**한다.
  **조용히 고정 목록으로 물러서지 않는다** — 그러면 사용자가 적지 않은 유니버스로 돌아간다.
- `Bundle.context`에 유니버스 산출 근거를 남긴다.

**★ 성능 — 피처 행렬을 미리 계산한다**

리플레이 루프를 순진하게 구현하면 매 봉마다 지표를 처음부터 다시 계산한다. `2,000종목 × 2,500일 = 500만 회`면 수 시간이고, 백테스트 한 번에 반나절이 걸리면 아무도 쓰지 않는다.

```
features[종목 × 날짜 × 피처]   ← groupby(종목).rolling() 등으로 일괄 계산 (수십 초)
리플레이는 features.loc[bar_time] 한 줄을 꺼내 쓴다
```

- **전제는 `compute`의 인과성이다** (4.2 규칙 1). `shift(-n)` 하나가 섞이면 전체가 무너진다.
- 대신 **look-ahead 위험이 피처 계산 한 곳에 갇힌다.** 감사할 지점이 하나면 지킬 수 있다.
- 일봉 + 횡단면이면 어차피 "한 날짜의 전 종목 단면"이 필요하므로 **원래 필요한 자료 구조**다.
- ⚠️ **계약을 바꿔서 속도를 얻지 않는다.** 전략에 전체 DataFrame을 넘겨 주는 식의 최적화는
  미래 참조를 다시 열어 준다. 느리면 캐싱으로 푼다.

**★ 엔진 자체를 검증하는 법**

백테스트 엔진에는 **정답을 알려 줄 오라클이 없다.** 결과가 그럴듯하면 맞는 줄 안다. 그래서 아래를 회귀 테스트로 박는다 — 전략 성과가 아니라 **엔진의 정직성**을 재는 테스트다.

| 테스트 | 기대 | 깨지면 |
| :--- | :--- | :--- |
| **난수 신호** | hit rate가 유니버스 기저율과 일치 | 70%가 나오면 **미래 참조가 있다.** 가장 값싸고 강력한 방어선 |
| **전량 매수** | forward return이 유니버스 평균과 일치 | 수익률 계산·정렬·조인 버그 |
| **신호 1일 밀기** | 성과가 기저율 쪽으로 떨어짐 | 안 떨어지면 신호가 무의미하거나 엔진이 새고 있음 |
| **상장폐지 포함 여부** | 폐지 종목이 유니버스에 등장 | 서바이버십 편향이 데이터 레이어에 있음 (3.9) |

**★ 다중검정 카운터 — 과적합을 기계가 세게 한다**

용도를 "구현 검증"으로 선언해도 실제로는 파라미터를 조금씩 바꿔 가며 다시 돌리게 된다. **특히 LLM에게 CLI를 주면 파라미터 200조합을 순식간에 돌려 보고 제일 좋은 것을 추천한다** — 1.3이 비목표로 선언한 행동을 사람보다 1000배 빠르게 한다. 사람은 47번 돌린 것을 잊지만 카운터는 잊지 않는다.

```
⚠️  이 전략에 대한 47번째 백테스트입니다 (최초 2026-07-14).
    파라미터를 12회 변경했습니다. 이 시점의 성과는 사실상 in-sample입니다.
```

- 실행 횟수와 **파라미터 변경 횟수를 구분해서** 센다. 같은 파라미터 재실행은 무해하다.
- 임계를 넘으면 `--i-know-this-is-in-sample` 없이 거부한다.
- 리포트 상단에도 박는다. **나중에 다시 볼 때 맥락이 남아야 한다.**

> 이건 LLM 호출을 허용했기 때문에 **오히려 가능해진 안전장치**다. 카운터가 출력되면
> 에이전트가 그것을 읽고 스스로 멈출 근거가 생긴다.

**shadow 모드** — 파이프라인을 실시간으로 돌리되 **알림을 보내지 않고 `signals`에만 기록**한다. 용도는 셋: ① LLM 노드의 1급 검증 경로(학습 누출은 shadow로만 잡힌다) ② 실전 투입 전 관찰 기간 ③ 구현 검증(백테스트와 shadow의 신호가 어긋나면 둘 중 하나가 틀린 것).

### 4.9 관측성

- 실행 진행은 **stdout으로 출력**한다 (`--json`이면 stderr). 하루 3회 도는 배치에 실시간
  스트리밍은 필요 없다.
- `node_runs`의 입출력 스냅샷으로 **"왜 이 신호가 나왔는가"를 사후 재현**한다.
- **전략 노드는 `rank` 결과 상위 N개의 점수·순위를 `node_runs`에 남긴다.** 전략이 한 덩어리가
  되면서 중간 판단이 노드 경계에 드러나지 않으므로, 이 스냅샷이 없으면 "왜 이 종목이
  뽑혔는가"를 잃는다.
- **0종목을 수집한 노드도 상태는 `success`다.** 어느 노드에서 0이 됐는지는 상태가 아니라
  `nodes[].items`가 답한다.

---

## 5. 노드 카탈로그

**Indicator 범주는 동결이고 Strategy 범주가 중심이다.** 노드는 "전략을 조립하는 블록"이 아니라 **"데이터 → 전략 → LLM → 알림"을 잇는 배선**이다. 지표 조건이 필요하면 노드가 아니라 전략 클래스에 넣는다.

| 범주 | 노드 | 상태 | 주요 파라미터 |
| :--- | :--- | :---: | :--- |
| **Trigger** | Manual Trigger | ✅ | — |
| | Schedule Trigger | ⬜ | ⚠️ 이 노드를 **누가 읽는지**는 11장 4b에 달려 있다 |
| **Input** | Symbol Universe | ✅ | 고정 목록 / 거래소 조회(거래대금 상위 N). ★ **동적 유니버스는 backtest에서 하드 차단** |
| | Market Data | ✅ | timeframe(`1d`/`1w`), lookback, closed_only, **skip_stale**, source, **cache** |
| **Strategy** ★ | **Strategy Runner** | ✅ | `strategy_id` + 전략의 `Params`. `compute` → `rank` → `select` |
| **AI** | **LLM Screen** | ⬜ P4 | provider, model, 프롬프트, 출력 스키마, 캐시 정책. **정성 정보 필터** |
| **Logic** | Condition Splitter | ✅ | 조건식 |
| | Merge / Rank·Percentile / Sort·Limit / Alert Cooldown | ⬜ | 전략 밖에서 점수를 합치거나 알림 폭주를 막을 때 |
| **Action** | Log Alert / Persist Signal | ✅ | 바깥으로 나가지 않는다 |
| | Telegram Alert | ⬜ `serve` | **`sends_external_messages = True`라 `run`에서는 실행되지 않는다** (12.2) |
| | Forward Return Evaluator | ⬜ P3 | `signals` 기록 후 N봉 뒤 수익률을 소급 채운다 (4.8의 산출원) |
| | *Broker Order* | ⬜ P5 | RiskGuard 필수 |

> `maFilter`는 **동결한다** — 단순 조건의 예시이자 `Bundle` 계약의 참조 구현으로 값이 있다.
> 다만 **Indicator 범주에 새 노드를 추가하지 않는다.** 이 선을 긋지 않으면 두 방식이 공존하다
> 노드 쪽이 썩는다.
>
> ⚠️ **계기판: `Strategy Runner`가 아닌 노드의 개수.** 배선용 노드가 계속 늘어난다면
> 파이프라인이 다시 전략을 표현하려 드는 것이다.

**Symbol Universe — AST 검사가 잡지 못하는 look-ahead** ★

거래소가 주는 종목 목록은 언제나 **"지금"** 이다(4.8 서바이버십). **이 경로는 `strategy check`가 잡지 못한다** — 전략 코드는 완전히 인과적이고 미래 참조는 유니버스 쪽에 있기 때문이다. AST에 흔적이 남지 않으므로 **차단을 노드가 명시적으로 맡는다**(규칙 14).

- `venue`가 지정된 Symbol Universe는 `backtest` 모드에서 **거부**한다.
- 산출 근거(`venue` · `top_by_turnover` · `point_in_time`)를 `Bundle.context`에 실어
  `node_runs`에 남긴다 — "그날 왜 이 종목들이었나"가 사후에 복원되어야 한다.
- Phase 3에서 point-in-time 스냅샷이 생기면 그때 backtest 경로가 열린다.

산출물은 items가 아니라 **`context["universe"]`의 심볼 목록**이다. 봉을 받기 전이라 `Item`을 만들 수 없다 — `as_of`는 Market Data가 캘린더로 판정하기 전까지 존재하지 않고, **없는 `as_of`를 지어내면 그 거짓말이 신호까지 따라간다.**

⚠️ **`venue`는 목록으로 받아야 한다 (미구현).** 단수라 시장마다 노드가 필요한데, 셋을 `marketData` 하나에 물리면 `Bundle.merge`가 `context["universe"]`를 덮어써 **두 시장이 소리 없이 사라진다**(4.1). items는 제대로 합쳐지므로 겉보기에는 정상이다. 노드 하나가 여러 venue를 처리하면 덮어쓰기가 애초에 생기지 않는다. 유동성 컷은 **venue별로 따로** 건다(3.7).

**LLM Screen — 가격 예측기가 아니다** ★

LLM이 캔들을 보고 가격을 예측한다는 근거는 없고, 학습 데이터 누출 때문에 검증도 불가능하다. **그러나 정성 정보 필터로는 다르다.**

숫자 스크리닝의 최대 위험은 **"지표는 좋은데 사실 위험한 회사"** 다. PBR 0.3에 ROE 15%인데 관리종목이거나, 감사의견 한정이거나, 유상증자를 앞두고 있거나, 횡령·배임 공시가 떴거나. 가격 데이터로 못 거르고, 사람이 매일 수백 종목의 공시를 읽을 수도 없다.

```
숫자 스크리너 → 후보 30종목 → LLM이 공시·뉴스를 읽고 지뢰 제거 → 5종목 → 사람 검토
```

- **수익 기여 경로가 알파 생성이 아니라 손실 회피다.** 소형주 스크리닝에서는 이쪽이 더 크다.
- **비용상 반드시 필터 뒤에 배치한다.**
- ★ **판단 보류를 1급 출력으로 둔다.** 출력 스키마에 `abstain` / `confidence`를 넣고 임계
  미달이면 신호를 죽이는 것을 **기본값**으로 한다. 점수만 받고 신뢰도를 안 받으면 모델이
  헛소리를 하는 중인지 알 방법이 없다 (FreqAI의 `do_predict`·DI와 같은 장치).

**LLM Provider의 세 종류 — 결정성 보증이 다르다** ★

| 구현 | 예 | `deterministic` | 백테스트 |
| :--- | :--- | :---: | :---: |
| `ApiProvider` | Anthropic · OpenAI (`temperature=0`) | ✅ | 허용\* |
| `CommandProvider` | `claude -p …` — **도구 사용 끔** | ✅ | 허용\* — **API 키 불필요** |
| `CommandProvider` | 위와 같으나 **도구 사용 켬** | ❌ | **거부** |
| `LocalModelProvider` | ollama 등 | ✅ | 허용\* |

\* 학습 데이터 누출 경고 배지는 그대로 유지된다.

agent 호출을 넣는 이유는 둘이다 — **비밀이 하나 더 사라지고**(시세는 이미 무인증이므로 남는 것은 텔레그램 토큰뿐), **agent는 컨텍스트를 스스로 가져온다**(이 노드의 용도가 공시·뉴스 읽기인데 미리 채운 프롬프트보다 직접 찾아 읽는 쪽이 명백히 유리하다).

**대신 두 가지를 강제한다.**

1. ⚠️ **백테스트에서 도구 사용 agent는 하드 차단.** 2023년 `bar_time`으로 리플레이하는데 agent가
   실시간 웹을 읽으면 **2026년 정보를 본다.** 학습 누출보다 나쁘다 — 그건 기억이지만 이건
   실제 미래 데이터다.
2. ⚠️ **`cacheable=False`면 `llm_cache`를 쓰지 않는다.** 캐시 키는 `(model, prompt_hash,
   input_digest)`인데 agent가 웹을 뒤지면 **실제 입력이 프롬프트 해시에 안 잡힌다.** 그대로
   캐시하면 낡은 답을 정답인 척 돌려주게 된다.

**구조화 출력**은 API처럼 스키마로 강제할 수 없으므로 텍스트를 Pydantic으로 검증하고, 실패하면 1회 재시도 후 **`abstain`으로 떨군다.** **보안** — 공시 텍스트를 프롬프트에 넣어 셸을 호출하므로 문자열 보간이 아니라 **argv 배열이나 stdin으로 전달**한다.

---

## 6. 파이프라인 정의 스키마

**형식은 YAML로 확정됐다** (11장 4번). 손으로 적어 보니 JSON의 문제는 구조가 아니라 **주석을 달 수 없다는 것**이었다 — 파이프라인 파일에 적고 싶은 것의 절반은 "왜 이 종목인가" · "왜 이 값인가"다. 스키마는 그대로고 로더가 확장자로 갈라 받으므로 `.json`도 계속 읽힌다. `pipeline_versions`의 스냅샷은 **직렬화이므로 JSON을 유지**한다.

```yaml
pipeline_id: pipe_upbit_momentum
version: 1
name: 업비트 횡단면 모멘텀 12-1

settings:
  user_timezone: Asia/Seoul
  default_mode: notify        # backtest | shadow | notify | paper | live
  daily_boundary: UTC00       # 코인 일봉 경계 (3.6)
  adjusted: true              # 전역 고정. 캐시 키에 들어간다 (3.8)
  max_concurrency: 4

nodes:
  - id: universe
    type: symbolUniverse
    params: { venue: upbit, quote_currency: KRW, top_by_turnover: 60 }
    on_error: { policy: retry, max_attempts: 3, fallback: fail }

  - id: data
    type: marketData
    params:                    # instruments를 비우면 상류 universe를 쓴다
      timeframe: 1d
      lookback: 320            # startup_candles(273)보다 커야 한다
      closed_only: true
      skip_stale: true
      source: auto
      cache: auto

  - id: strategy
    type: strategyRunner
    params:
      strategy_id: cross_momentum_12_1
      strategy_sha256: 9f2c…   # 다르면 경고 (4.7)
      params: { lookback: 252, skip: 21, top_pct: 0.2 }

  - id: persist
    type: persistSignal

edges:
  - { id: e1, source: universe, target: data }
  - { id: e2, source: data,     target: strategy }
  - { id: e3, source: strategy, target: persist }
```

- 엣지는 `source_handle` / `target_handle`(기본 `main`)로 분기를 표현한다. `on_error: route`면
  `error` 핸들이 암묵적으로 생긴다 (4.3).
- **그래프가 거의 직선이다.** 분기는 LLM 판정과 에러 라우팅뿐이다 — **이것이 캔버스를 버린
  이유이자 DAG 엔진을 남겨 둔 이유**이기도 하다 (분기와 팬아웃은 여전히 그래프이므로).
- v0.4의 `position`(캔버스 좌표)은 사라졌다. 예전 정의에 남아 있어도 무시된다.

---

## 7. 디렉터리 구조

**`backend/` 와 `frontend/` 구분이 없다.** 프론트가 없으면 "백엔드"라는 이름도 의미가 없다.

```
marketscan/
├── ARCHITECTURE.md · CLAUDE.md · README.md
├── pyproject.toml · uv.lock       # ★ Docker를 대신하는 재현성 장치
├── pipelines/demo.yaml            # 파이프라인 정의 (6장)
├── strategies/                    # ★ 사용자 전략 (정본). git 관리, 해시가 버전에 박힘
├── data/                          # SQLite. 백업 대상
├── reports/                       # 실행·백테스트 리포트 (재생성 가능)
├── app/
│   ├── cli/          main.py · output.py(종료 코드·--json) · pipeline_file.py
│   ├── engine/       types.py(Item·Bundle) · context.py · graph.py · runner.py
│   │                 signals.py(배출구) · state.py(Fresh Bar Gate) · expr.py · template.py
│   ├── market/       instrument.py · calendar.py · timeframe.py
│   ├── providers/    base.py · registry.py(라우팅·폴백) · ohlcv_source.py(캐시 계층)
│   │                 ccxt_base.py + ccxt_quirks/ · pykrx · yfinance · fdr · synthetic
│   ├── ingest/       worker.py — 수집 대상 도출·수집 (3.9)
│   ├── nodes/        registry.py + triggers/ inputs/ strategy/ logic/ actions/ indicators/(동결)
│   ├── strategies/   base.py(Protocol) · registry.py(로더·해시) · check.py(AST)
│   ├── storage/      models.py · db.py · repository.py · history.py
│   │                 ohlcv_cache.py · bar_state.py
│   ├── report/       run_report.py — 자기완결 HTML
│   └── core/         config.py
└── tests/
```

- **`app/strategies/`(프레임워크)와 최상위 `strategies/`(사용자 전략)를 구분한다.** 전자는
  로더·프로토콜이고 후자는 데이터에 가깝다.
- **`data/` · `strategies/` 두 디렉터리만 사용자 자산이다.** 백업 대상이자, 코드를 지우고 다시
  받아도 살아남아야 하는 것들이다. `reports/`는 재생성 가능하다.
- ⬜ 아직 없는 것: `app/backtest/`(P3) · `app/nodes/ai/`(P4) · `app/risk/`(P5).

---

## 8. 설치와 운용

**Docker를 쓰지 않는다.** `docker run --rm -v …`를 하루 3번 호출하는 구조는 얻는 것보다 마찰이 크다. **재현성은 `uv`가 대신한다** — `uv.lock`이 의존성을, `requires-python`이 인터프리터를 고정한다.

```bash
uv sync
uv tool install .          # marketscan 이 PATH에 올라간다
marketscan describe        # 설치 확인
```

**⚠️ 자동 실행을 무엇이 맡을지는 아직 정하지 않았다** (11장 4b). 지금 확정된 것은 **"무엇을 부르는가"뿐**이다.

```
marketscan ingest --commit                # 일봉 수집 → ohlcv_cache
marketscan run --market crypto --commit   # 코인 판정
marketscan run --market krx    --commit   # 한국장 마감 후
marketscan run --market us     --commit   # 미국장 마감 후
```

- Fresh Bar Gate(3.5)가 있으므로 `--market` 없이 전부 돌려도 되지만, 명시하는 쪽이 로그를
  읽기 편하다.
- **`--commit`이 없으면 아무것도 남지 않는다** (12.2). 자동 실행에만 붙인다.

| 후보 | 얻는 것 | 치르는 값 |
| :--- | :--- | :--- |
| **OS 스케줄러** | 상주 프로세스가 없다. 죽을 것이 없고 재부팅을 견딘다. 지금 당장 쓸 수 있다 | 알림·재시도·백오프가 OS 설정으로 흩어진다 |
| **`serve` 명령** | 스케줄·재시도·알림이 한곳에 모인다. 크로스 플랫폼이 하나로 끝난다 | **상주 프로세스가 돌아온다** — APScheduler를 기각한 이유가 되살아난다 |

**보안** — 네트워크에 아무것도 열지 않으므로 노출 위험이 대부분 사라진다. 남은 비밀은 **텔레그램 토큰과 (API를 쓴다면) LLM 키뿐**이며 시세에는 자격 증명이 필요 없다(3.3).

**백업** — `data/`(SQLite) · `strategies/` · 마스터 키. `ohlcv_cache`는 무료 소스가 막혀도 남는 유일한 자산이므로 반드시 포함한다(3.9).

**타임존** — 프로세스는 UTC로 고정하고 표시만 `user_timezone`으로 변환한다. 스케줄 시각은 로컬 기준으로 적되 **시장 마감과의 관계를 함께 남겨** 서머타임 전환 때 확인할 수 있게 한다(3.2).

---

## 9. 구현 로드맵

**진행 상태의 체크박스는 `README.md`가 갖는다.** 여기는 순서의 근거만 적는다.

| Phase | 범위 | 상태 |
| :--- | :--- | :--- |
| **0** | 계약 확정 — `Item`/`Bundle`/`RunContext`/`InstrumentRef`/`MarketCalendar`/`Provider` | ✅ |
| **0.5** | v0.5 전환 — 웹 계층 제거 → CLI, 전략 클래스화, 디렉터리 평탄화 | ✅ |
| **1** | 전략 러너 & 단일 시장 E2E — 업비트 실물 일봉으로 완주, `bar_state` 영속화 | ✅ (자동 실행 결정만 남음) |
| **2** | 멀티 마켓 ★ — 캘린더 3종, 무인증 소스 4종, 라우팅·폴백, **Ingestion Worker + `ohlcv_cache`** | 진행 중 |
| **3** | 백테스트 — 캘린더 리플레이, **피처 행렬 사전 계산**, 엔진 검증 4종, 신호 품질 지표 | ⬜ |
| **4** | LLM 스크리닝 — Provider 추상화, `LLM Screen`, `abstain`/`confidence` | ⬜ |
| **4.5** | 운용 — `shadow` 모드로 최소 몇 달 관찰, 오버라이드 추적, 소액 시작 | ⬜ |
| **5** | 실주문 (선택) — `BrokerProvider`, RiskGuard, `paper` 2주 후 `live` 소액 | ⬜ |

**순서의 근거**

- **백테스트가 LLM보다 앞이다.** 4.8에서 백테스트의 용도를 "전략 탐색"이 아니라 "내 구현 검증"으로
  재정의했으므로, 비결정적이고 비싼 LLM 계층을 얹기 **전에** 데이터·엔진의 정직성을 확인해야 한다.
- **Phase 1을 시장 하나로 좁힌 이유** — 끝까지 완주해야 Phase 2에서 어댑터를 늘릴 때 문제가
  소스 탓인지 전략 탓인지 구분된다.
- **Phase 2가 존재 이유다.** 두 번째·세 번째 어댑터를 붙여 봐야 3장의 추상화가 맞는지 검증된다.
- **Phase 3의 공수가 짧은 것은 청산·포지션·체결 모델이 없기 때문이다**(1.3). 그 선을 넘는 순간
  자체 구현을 접고 기성 엔진을 검토해야 한다.
- ⚠️ Celery/Redis는 **asyncio로 감당 안 될 때** 도입한다. 조기 도입은 복잡도만 늘린다.

---

## 10. 주요 리스크와 대응

| 리스크 | 영향 | 대응 |
| :--- | :--- | :--- |
| ★ **과적합 (전략 탐색)** | **가장 흔한 실패.** 백테스트만 좋고 실전에서 죽음 | 파라미터 튜닝을 비목표로 선언(1.3), 검증된 팩터의 표준값, **다중검정 카운터** (4.8) |
| ★ **LLM이 백테스트를 반복 호출** | 위 위험의 **자동화된 버전** | `backtest_runs` 카운터를 매 실행 출력, 임계 초과 시 거부 (4.8) |
| ★ **에이전트가 실수로 알림 발송·봉 소비** | 오발송, 그리고 **신호 영구 유실**(3.5) | `--commit` 없이는 부작용 없음이 기본. 읽기 전용 명령과 분리 (12.2) |
| ★ **도구 사용 agent가 백테스트에서 미래를 읽음** | **실제 미래 데이터 참조.** 학습 누출보다 나쁨 | `deterministic=False`는 backtest에서 하드 차단, 캐시도 안 함 (5장) |
| ★ **전략 코드 버전 드리프트** | 과거 실행의 근거를 잃음 | 소스 SHA-256 기록 + 스냅샷, 불일치 시 경고 (4.7) |
| ★ **`compute`의 비인과성** | **미래 참조가 조용히 들어옴** | `strategy check`의 AST 검사 + 난수 신호 테스트를 사후 방어선으로 (4.2 / 4.8) |
| ★ **유니버스 서바이버십** | **백테스트 성과 부풀림.** AST에 흔적이 안 남는다 | 동적 유니버스를 backtest에서 차단, 폐지 종목도 수집·보존 (3.9 / 4.8 / 5장) |
| **수정주가 불일치** | **지표가 조용히 틀어짐** | 전역 정책 고정 + 캐시 키 포함 + 소스 간 종가 검증 (3.8) |
| 무료 소스 차단·중단 | 데이터 유실 | Ingestion Worker 단일 창구 + 라우팅 폴백 + 캐시 영구 보관 (3.9) |
| 폴백으로 소스가 바뀜 | 지표 불연속·결정성 훼손 | `failed_sources`를 `ctx.log`·`Item.meta`·`node_runs`에 노출 (3.4) |
| 실패한 실행이 봉을 소비 | **신호 유실** | `stage()` 후 성공 시에만 `commit()` (3.5) |
| 미국장 서머타임 오처리 | 신호 시각 1시간 오차 | `ZoneInfo` 사용, 전환일 회귀 테스트 (3.2) |
| 장 마감 중 신호 재발생 | 알림 폭주 | Fresh Bar Gate + `skip_stale` (3.5) |
| 중복 알림 | 신뢰도 하락 | `dedup_key` UNIQUE + Cooldown 노드 (4.5) |
| 미완성 캔들 신호 | 잘못된 판단 | `closed_only` + 마감 후 지연 (4.4) |
| **LLM 학습 데이터 누출** | **백테스트가 낙관 편향** | 캐시로는 못 막는다. 경고 배지 + `shadow`를 1급 검증 경로로 (4.8) |
| LLM 비용 폭증 / 근거 없는 단정 | 운영비 / 잘못된 제외 | 캐시·호출 상한·**필터 뒤 배치**, `abstain`·`confidence`를 1급 출력으로 (5장) |
| 백테스트 리플레이 성능 | 반나절 걸리면 아무도 안 씀 | 피처 행렬 사전 계산. **계약을 바꿔서 속도를 얻지 않는다** (4.8) |
| 결제 통화 오판 | 통화 표기·합산 오류 | `quote_style`로 symbol에서 유도, 형식 불일치는 거부 (3.1) |
| API 키 유출 | 치명적 (**v0.5에서 크게 축소**) | **시세에 키가 필요 없어짐**(3.3). 남은 것은 LLM·텔레그램뿐 |
| 거래소 레이트 리밋 | 데이터 누락 | 일봉 하루 1회 수집 + Ingestion Worker 단일 지점 + 백오프 (3.9) |
| SQLite 잠금 경합 | 실행 실패 (**v0.5에서 강등**) | CLI 전환으로 쓰는 프로세스가 하나뿐. WAL + `busy_timeout`만 유지 (4.7) |
| **기존 도구와의 중복** | 만들 이유가 사라짐 | **멀티마켓(3장)이 유일한 차별점**임을 인지하고 유지 |

---

## 11. 미결정 사항

**열려 있는 것**

1. ★ **코인 일봉 경계** — `UTC00`(= KST 09:00, 업비트 기준) vs `KST00`(한국 자정).
   현재 기본값은 `UTC00`. 일봉이 유일한 판단 단위라 **코인 신호 전체가 이 경계에 좌우된다** (3.6).

4b. ★ **자동 실행과 알림을 무엇이 맡는가 — OS 스케줄러 vs `serve`** (8장에 두 후보의 득실).
   - **알림 전송은 이미 `serve` 쪽으로 확정됐다** (12.2). 남은 미결정은 **스케줄을 누가 거는가**뿐이다.
     OS 스케줄러로 가더라도 전송 주체는 필요하므로, 그쪽을 택하면 "알림만 담당하는 얇은 명령"이
     대신 생긴다. **어느 쪽이든 `run`은 조용하다.**
   - 쟁점은 **상주 프로세스를 되살릴 값이 있는가**다. APScheduler를 기각한 이유("프로세스가
     죽으면 스케줄도 같이 죽는다")는 `serve`에도 그대로 적용된다.
   - **미뤄도 비용이 없다** — `run --commit`이 하는 일은 양쪽에서 같다. 지금 굳히면 되돌릴 때
     문서와 코드가 함께 어긋난다. **코드는 준비됐고 며칠 돌려 보는 일만 남았다.**
   - 관찰할 것 하나 — 현재 `Symbol Universe`는 봉이 전부 stale인 실행에서도 거래소 목록을 다시
     조회한다. 하루 여러 번 부르는 구성을 고르면 이 호출이 그대로 늘어난다.

**나중에 정해도 되는 것**

5. **알림 채널** — 텔레그램 외 Slack/Discord/이메일 필요 여부.
6. **멀티 타임프레임 features 네임스페이스** — `w1.sma_20` 형태로 접두할지. 일봉·주봉만
   남으면서 우선순위가 낮아졌다.
7. ⚠️ **빗썸 `fetchOHLCV` 지원 여부** — CCXT 런타임 확인 후 미지원이면 별도 어댑터 (3.3).
8. **Alpaca 데이터 피드** — 무료 IEX vs 유료 SIP. yfinance 폴백용이므로 Phase 3 이후.
9. ⚠️ **Toss 증권 오픈 API 스펙** — **Phase 5로 이연.** 시세를 무인증 소스로 해결하면서
   주문 단계 전까지 영향이 없어졌다 (3.3).

**해소된 것**

| # | 항목 | 결론 |
| :--- | :--- | :--- |
| 2 | 상장폐지 종목의 과거 가격 확보 | **가능.** PyKRX·FDR 모두 폐지 종목 일봉을 준다 (2018년 이후 폐지 주권 표본 10/10). ⚠️ `SecuGroup == '주권'`만 조회되고 신주인수권증서 등은 빈 결과다. 수집은 `ingest --include-delisted` (3.9) |
| 3 | 첫 전략 | **횡단면 모멘텀 12-1** (252/21 표준값 고정) |
| 4 | 파이프라인 정의 형식 | **YAML 확정.** JSON의 문제는 구조가 아니라 주석을 달 수 없다는 것이었다 (6장) |
| — | 분봉 보존 정책 | 수집하지 않기로 하면서 소멸. `ohlcv_cache`는 SQLite 확정 (3.9) |
| — | 지표 라이브러리 | 전략 클래스가 각자 임포트하므로 전역 결정이 아니게 됐다 (2.1) |
| — | 프로젝트 이름 | **`marketscan`.** 3장(멀티 마켓)이 존재 이유이므로 이름이 그것을 말한다. `trade`는 하지 않는 일을 암시하고 `flow`는 폐기된 캔버스 은유였다 |
| — | 배포 형태 / 구현 언어 | **`uv` + `[project.scripts]` / 파이썬 유지** (부록 C) |
| — | LLM 노드 배치 | 필터 뒤로 확정. 비용과 역할(정성 필터) 양쪽에서 결론이 같다 (5장) |

> 무료 데이터 API의 티어 정책(호출 한도, 이력 범위)은 자주 바뀐다. 본 문서의 수치성 서술은
> **착수 시점에 재확인**한다.

---

## 12. CLI 인터페이스 ★

사용자가 셋이다 — **사람**, **자동 실행**, 그리고 **CLI를 호출하는 LLM**. 셋의 요구가 다르므로 명령 표면을 그에 맞춰 설계한다.

> **파이프라인 안의 `LLM Screen` 노드(5장)와 혼동하지 않는다.** 그건 결정성·캐시·비용 상한이
> 걸린 파이프라인 부품이고, 여기서 말하는 LLM은 **밖에서 CLI를 부르는 대화형 에이전트**다.

### 12.1 명령

| 명령 | 부작용 | 용도 |
| :--- | :---: | :--- |
| `run [--market M] [--commit]` | **`--commit` 시에만** | 파이프라인 실행. **기본은 dry-run.** 산출물은 stdout + HTML 리포트 |
| `ingest [--venue V] [--commit]` | **`--commit` 시에만** | 일봉 수집 → `ohlcv_cache`. **기본은 계획만** (3.9) |
| `explain <signal_id>` | 없음 | ★ **왜 이 신호가 났는가** (12.5) |
| `signals list` / `signals ack` | 없음 / 응답 기록 | 신호 이력 / **이 신호대로 움직였는가** (4.8) |
| `stats [--compare acted]` | 없음 | 신호 품질 지표. `--compare acted`가 오버라이드 분석 |
| `strategy new / check / list` | `new`만 파일 생성 | 템플릿 / ★ **AST 인과성 검사** (12.6) |
| `describe` | 없음 | 전략·유니버스·마지막 실행. **에이전트의 방향 잡기용** |
| `backtest` / `verify` | `backtest_runs` 기록 / 없음 | **Phase 3.** 리플레이 + 다중검정 카운터 / 엔진 검증 4종 |

**★ 읽기 전용 명령은 DB 파일조차 만들지 않는다.** 엔진을 열기만 해도 SQLite 파일이 생기므로 `_database_exists()`가 먼저 확인한다.

### 12.2 부작용은 명시적 옵트인 ★

**에이전트가 실수로 봉을 소비하면 안 된다.** 3.5의 `commit()`이 발동하면 그 신호는 재실행에서 stale로 걸러져 **영영 사라진다.** `--commit`이 없으면 `stage()` 이후 반드시 `discard()`한다. **기본값이 안전한 쪽이어야 사고가 안 난다.**

**★ 단일 실행은 외부로 아무것도 내보내지 않는다**

`--commit`이 열어 주는 것은 **`signals` 기록 · 봉 소비 · 캐시 쓰기**뿐이다. 텔레그램 같은 채널 전송은 `run`의 일이 아니라 상주 실행(`serve`)의 몫으로 미뤘다.

- **이유는 신뢰다.** 사람이 전략을 고치며 손으로 돌리는 실행과 자동으로 도는 실행은 오발송의
  무게가 다르다. 손으로 돌릴 때마다 채널로 메시지가 나가면 **알림 자체를 믿지 않게 되고**,
  그러면 1.2의 "주의력 기계"가 무너진다. **무시하게 된 알림은 없는 알림이다.**
- **단일 실행의 산출물은 stdout과 정적 HTML 리포트다.** dry-run은 `latest.html` 하나를
  덮어쓰고 `--commit` 실행만 `run_<id>.html`로 남는다 — 실제로 나간 판단만 이력이 되면 된다.
- **차단은 노드 안이 아니라 실행 엔진에 둔다.** 노드가 `sends_external_messages = True`를
  선언하면 `ctx.sends_alerts`가 False일 때 엔진이 **아예 실행하지 않는다.** 노드마다
  `if ctx.sends_alerts:`를 심는 방식은 배선 노드가 늘어나면 언젠가 하나를 빠뜨리고, 그날
  손으로 돌린 실행이 채널로 메시지를 쏜다.
- `ctx.sends_alerts`는 **세 조건의 곱**이다 — `allow_alerts`(`serve`만 켠다) × `--commit` ×
  모드가 backtest·shadow가 아닐 것.

> 리포트 파일 쓰기는 `--commit` 뒤에 두지 않았다. `reports/`는 재생성 가능하고 무엇도 되돌릴
> 수 없게 만들지 않기 때문이다. **되돌릴 수 없는 것만 `--commit`이 막는다.**

### 12.3 종료 코드

4.1이 "빈 `Bundle`도 정상 출력"이라고 정했으므로 **"신호 0건"과 "실패"를 반드시 구분한다.** 구분하지 않으면 자동 실행이 매일 실패로 잡힌다.

| 코드 | 의미 |
| :--- | :--- |
| `0` | 성공 (**신호 0건 포함**) |
| `2` | 데이터 소스·노드 실패 |
| `3` | 검증 실패 (파이프라인·전략·인자, 커버리지 부족, 다중검정 임계 초과) |

### 12.4 출력 규약

- **모든 명령에 `--json`.** 스키마를 안정적으로 유지한다. LLM이 한국어 표를 파싱하게 두면
  오독하고, JSON에 진행 로그가 섞이면 파싱이 깨진다 — `--json`이면 진행 로그는 stderr로.
- **`--limit` 기본값을 둔다.** LLM은 좁은 JSON은 잘 읽고 큰 덤프에는 무너진다.
  **OHLCV 원본은 기본 출력에 넣지 않는다.**
- **조용한 절삭을 하지 않는다.** top-N·샘플링을 하면 반드시 경고를 남긴다 — "전부 처리했다"는
  오해가 실사용에서 가장 위험하다.
- **미구현을 성공처럼 보이게 하지 않는다.** 빈 결과와 "아직 안 만들었다"를 구분해 내보낸다.

### 12.5 `explain` — 가장 값있는 명령

사람이 알림을 보고 던지는 질문은 언제나 하나다. **"이게 왜 떴어?"** 4.9는 `node_runs` 스냅샷으로 사후 재현이 된다고 하지만, 실제로 하려면 5개 테이블을 조인해야 한다. 이걸 한 명령으로 접는다.

```
krx:005930 · 2026-08-01T06:30:00+00:00 (1d)
전략  cross_momentum_12_1 @ 9f2c3a1b8e04
순위  7 / 500  (상위 1.4%)
데이터 cache(pykrx) · adjusted=True · fallback_from=[]
실행  run_d91342e5fe47 · success
판정  acted=None
```

**`fallback_from`(3.4)과 `strategy @ 해시`(4.7)가 함께 나오는 것이 핵심이다** — 어떤 코드로, 어느 소스에서 나온 판단인지가 사후에 복원된다. 없으면 LLM이 4~5번 질의하다 틀린다. 캐시에서 읽었으면 **그 구간을 채운 원래 소스까지** 밝힌다 — 아니면 캐시가 폴백 가시화를 도로 가려 버린다.

### 12.6 `strategy check` — 규칙을 기계화한다

4.2 규칙 1의 인과성은 런타임에 강제할 수 없지만 **AST로 상당 부분 잡힌다.** 검사 항목: `shift(음수)` · `center=True` · `bfill` · `datetime.now` · 네트워크 라이브러리 임포트 · `Params` 미선언.

```
leaky — 위반 1건
  L13 [causality] shift(-1) — 미래 참조입니다. 타깃 계산이라면 백테스트 평가기로 옮기세요.
```

**LLM이 전략을 쓰고 → `check`가 거르고 → `verify`로 엔진 검증까지 도는 루프**가 만들어진다. LLM이 무심코 `shift(-1)`을 쓰는 것은 흔한 일이라 이 검사는 실제로 값을 한다. ⚠️ **통과가 인과성을 보장하지는 않는다** — 사후 방어선은 난수 신호 테스트다(4.8).

### 12.7 만들지 않을 것

- **MCP 서버** — 잘 설계된 CLI + 셸 접근이면 충분하고, 유지할 프로세스를 하나 늘린다.
  필요해지면 CLI를 감싸는 얇은 껍데기라 언제든 붙일 수 있다.
- **LLM에게 매매 결정을 맡기는 것** — 1.2가 "최종 판단은 사람이 한다"고 정한 선이다.
  여기서 LLM의 역할은 **읽고 설명하는 것**이다.

이 층은 1.2의 "주의력 기계"와 어긋나지 않고 한 겹 더 쌓는다 — 예측을 추가하는 것이 아니라 주의력을 한 번 더 증폭한다. 다만 그 선을 넘는 두 가지(**백테스트 루프**, **매매 결정**)는 설계로 막는다.

---

## 부록 C. v0.4 → v0.5 변경 요약

v0.4는 "노드를 조합해 전략을 만드는 비주얼 도구"였다. v0.5는 **"멀티마켓 횡단면 스크리너 + 알림"** 이다. 아래 변경은 서로 독립적이지 않고, 대부분 앞의 결정에서 연쇄적으로 따라 나온다.

| # | 항목 | v0.4 | v0.5 | 이유 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **전략 표현** | 지표 노드 조합 | **파이썬 클래스 1개** | 지표 × 파라미터 × 조합은 끝이 없다. 사용자는 파이썬을 쓰는 본인 한 명이다 (4.2 / 5장) |
| 2 | **판단 단위** | 백테스트 일봉 / 알림 분봉 | **일봉·주봉 전용** | 분 단위 왕복 비용이 기대 수익을 넘고, 그 구간의 상대는 호가창을 본다 (1.3 / 3.6) |
| 3 | **전략의 축** | 시계열 필터 체인 | **횡단면 랭킹이 1급** | 표본 수 100배, 시장 방향 상쇄, 팩터의 정의 자체가 횡단면 (1.2 / 4.1) |
| 4 | **캔버스** | React Flow로 전략 조립 | **폐기** | 전략이 클래스로 옮겨가면서 캔버스가 편집 도구이길 그만두었다 |
| 5 | **백테스트의 용도** | 전략 검증 | **구현 검증** | 2,500행으로 수백만 조합을 뒤지면 우연히 맞는 것이 반드시 나온다 (1.3 / 4.8) |
| 6 | **엔진 검증** | look-ahead assert만 | **+ 난수 신호 · 전량 매수 · 신호 밀기** | 백테스트 엔진에는 정답을 알려 줄 오라클이 없다 (4.8) |
| 7 | **LLM 노드** | 매수 타당성 점수 | **정성 정보 필터** | 가격 예측 근거가 없고 학습 누출로 검증도 불가. 공시로 지뢰를 거르는 쪽은 근거가 있다 (5장) |
| 8 | **LLM 신뢰도** | 점수만 출력 | **`abstain`/`confidence`를 1급 출력으로** | 신뢰도를 안 받으면 모델이 헛소리 중인지 알 수 없다 (5장) |
| 9 | **전략 버저닝** | (없음) | **소스 SHA-256 + 스냅샷** | 파일을 고치면 과거 버전의 의미가 소급으로 바뀐다 (4.7) |
| 10 | **백테스트 성능** | 매 봉 지표 재계산 | **피처 행렬 사전 계산** | 500만 회 재계산이면 수 시간. look-ahead 위험이 한 곳에 갇힌다 (4.8) |
| 11 | **시세 자격 증명** | 소스별 API 키 필요 | **불필요** | 일봉만 쓰면 무인증 소스로 충분. 갖고 있지 않은 키는 새지 않는다 (3.3) |
| 12 | **`ohlcv_cache` 저장소** | SQLite vs Parquet 미결정 | **SQLite 확정** | 2억 행 문제는 분봉에서만 발생했다 (3.9) |
| 13 | **상장폐지 종목** | 유니버스 산출에서만 고려 | **수집·보존 대상** | 살아 있는 종목만 쌓으면 서바이버십 편향이 데이터 레이어에 고착된다 (3.9) |
| 14 | **shadow 모드** | 분봉 전략의 대안 검증 | **LLM 검증 + 실전 전 관찰** | 분봉이 사라지며 원래 이유는 없어졌지만 용도가 바뀌어 남았다 (4.8) |
| 15 | **로드맵 순서** | LLM → 백테스트 | **백테스트 → LLM** | 구현 검증 도구라면 비싼 계층을 얹기 전에 돌려야 한다 (9장) |
| 16 | **오버라이드 추적** | (없음) | **`signals.acted` + 사후 성과** | 규율 기계라면 측정할 것은 사용자가 규율을 지켰는지다 (4.8) |
| 17 | **인터페이스** | 웹 UI (React SPA + REST) | **CLI (Typer)** | 하루 3회 도는 배치에 상주 서버가 필요 없다. FastAPI·APScheduler·SSE가 함께 소멸 (12장) |
| 18 | **시각화** | 서버가 렌더한 대시보드 | **정적 HTML 파일 생성** | 서빙하지 않고 파일로 떨어뜨리면 나중에 비교할 수 있다 (2.1) |
| 19 | **스케줄러** | APScheduler | ⚠️ **미결정** | `serve`가 후보로 올라오며 다시 열렸다. APScheduler를 되살리자는 뜻은 아니다 (11장 4b) |
| 20 | **배포** | Docker Compose | **`uv` + `[project.scripts]`** | 스케줄러가 `docker run -v …`를 부르는 구조는 순손실 (8장) |
| 21 | **LLM 호출 방식** | API only | **API · 로컬 커맨드 · 로컬 모델** | agent 호출은 키가 필요 없고 자료를 스스로 찾는다 (5장) |
| 22 | **다중검정 카운터** | (없음) | **`backtest_runs` + 매 실행 경고** | LLM에게 CLI를 주면 파라미터 200조합을 순식간에 돌린다 (4.8) |
| 23 | **부작용 규약** | (없음) | **`--commit` 없이는 부작용 없음** | 에이전트가 실수로 알림을 쏘거나 봉을 소비하면 그 신호는 영영 사라진다 (12.2) |
| 24 | **시세 수집** | 노드가 직접 API 호출 | **Ingestion Worker → `ohlcv_cache` → 노드(읽기 전용)** | 레이트 리밋·중복 호출·장애 격리 (3.9) |
| 25 | **디렉터리** | `backend/` + `frontend/` | **최상위 평탄화** | 프론트가 없으면 "백엔드"라는 이름도 의미가 없다 (7장) |

**검토했으나 채택하지 않은 것**

| 제안 | 판단 |
| :--- | :--- |
| freqtrade 전략(`IStrategy`)을 그대로 실행 | **기각.** freqtrade는 CCXT·24/7·무캘린더·무수정주가 전제라 주식과 양립하지 않는다. 호환은 사실상 freqtrade 임포트를 뜻하고, 그러면 코인 런타임이 통째로 딸려온다. 게다가 공개 전략의 값어치는 대부분 **청산 로직**에 있는데 이 시스템에는 청산이 없다. freqtrade는 페어 단위 시계열 루프라 `rank`를 애초에 표현할 수 없다 (4.2) |
| freqtrade를 코인 전용으로 쓰고 사이드카만 붙이기 | **조건부 기각.** 코인만 할 것이라면 이쪽이 압도적으로 싸다. **멀티마켓이 요건이므로** 기각 |
| FreqAI 도입 | **기각.** lightgbm/torch가 딸려오고, 코인 모양이며, 재학습 때문에 백테스트가 매우 느리다. **단 `do_predict`/DI의 계약은 LLM Screen에 이식했다** (5장) |
| DAG 엔진 제거 | **보류.** 파이프라인이 얕아져 위상정렬의 존재감은 줄었지만 `runner.py`는 이미 동작하고 테스트도 있다. 분기·팬아웃·에러 라우팅은 여전히 그래프다. **더 투자하지 않되 지우지도 않는다** |
| 청산·포지션을 넣어 수익률·샤프 측정 | **기각 유지.** 일봉으로는 장중에 손절이 닿았는지 알 수 없어 가정을 넣어야 하고, 그 순간 체결·수수료·세금이 딸려와 Phase 5를 끌어온다 (1.3 / 4.8) |
| `maFilter` 등 기존 지표 노드 삭제 | **기각.** 단순 조건의 예시이자 `Bundle` 계약의 참조 구현으로 값이 있다. **동결**하되 신규 추가만 금지 (5장) |
| CLI니까 Go/Rust로 재작성 | **기각.** 그 직관은 하루 수백 번 호출되는 개발 도구에서 온 것인데 이건 하루 3회 배치다. 결정적으로 **3장이 PyKRX·FDR·exchange_calendars 위에 서 있고 대체재가 없다** — 다른 언어를 고르면 존재 이유인 3장을 처음부터 다시 짓는 것부터 시작한다. 성능도 반대 방향으로, 피처 행렬은 numpy가 C로 도는 구간이다 (2.1) |
| MCP 서버 제공 | **기각(보류).** 잘 설계된 CLI + 셸 접근이면 충분하고 유지할 프로세스만 늘어난다 (12.7) |
| Docker를 선택지로 남겨 두기 | **기각.** 리눅스 상시 가동 장비로 옮겨도 `uv`가 동일하게 동작한다. **쓰지 않을 것을 미리 유지하지 않는다** (8장) |
| `ohlcv_cache`를 Parquet/DuckDB로 | **기각.** 일봉은 SQLite로 충분하다. 실제 문제였던 분봉 보존이 3.6에서 닫혔다 (3.9) |
| 백테스트를 LLM이 자유롭게 호출 | **조건부 허용.** 막지는 않되 `backtest_runs` 카운터를 매번 출력하고 임계 초과 시 거부한다. 카운터가 출력되면 에이전트가 스스로 멈출 근거가 생긴다 (4.8) |
