# marketscan

암호화폐·한국주식·미국주식을 **하나의 유니버스로 매일 훑어**, 볼 만한 소수의 후보를 알림으로
올려주는 개인용 도구입니다.

**예측 기계가 아니라 주의력 기계입니다.** 시장을 맞히는 것이 아니라, 혼자서는 볼 수 없는 범위를
대신 보고 정해둔 규칙을 대신 지키는 것이 목적입니다. 최종 판단은 사람이 합니다.

- 설계 문서: **[ARCHITECTURE.md](./ARCHITECTURE.md)** — 모든 설계 결정의 근거
- 작업 규칙: **[CLAUDE.md](./CLAUDE.md)**

---

## 이 물건이 아닌 것

| | freqtrade | **marketscan** |
| :--- | :--- | :--- |
| 정체 | 코인 자동매매 봇 | **멀티마켓 스크리너** |
| 최종 산출물 | 체결된 주문 | **사람이 읽는 후보 목록** |
| 시간 단위 | 분·시간 | **일봉 전용** |
| 전략의 축 | 시계열 타이밍 | **횡단면 랭킹** |
| 청산 | 본체 | **없음** |
| 성과 측정 | 수익률·샤프 | **신호 품질** (forward return·hit rate·IC) |

**자동매매를 하지 않는 것이 미완성이 아니라 정체성입니다.** 주문이 없으니 청산이 없고, 청산이
없으니 수익률 지표가 없고, 그래서 일봉으로 충분해지고, 시세 API 키가 필요 없어집니다.

---

## 현재 상태

> **Phase 1 완료 기준 충족 · 자동 실행 방식 결정 1건 남음**
>
> `marketscan run --commit`이 **업비트 실제 일봉**으로 끝까지 돕니다 — 거래소에서 유니버스를
> 뽑고, 12-1 모멘텀으로 줄 세우고, 신호를 남기고, `explain`으로 되짚을 수 있습니다.
> **주식(`krx` · `nasdaq`)은 아직 `synthetic` 더미입니다** — PyKRX · yfinance · FDR은 Phase 2입니다.

v0.5 전환이 끝났습니다. 아래가 v0.4에서 바뀐 것들입니다.

| | v0.4 | **v0.5 (현재)** |
| :--- | :--- | :--- |
| 인터페이스 | FastAPI + React 캔버스 | **CLI (Typer)** |
| 전략 표현 | 지표 노드 조합 | **파이썬 클래스 1개** |
| 판단 단위 | 백테스트 일봉 / 알림 분봉 | **일봉·주봉 전용** |
| 전략의 축 | 시계열 필터 체인 | **횡단면 랭킹** |
| 배포 | Docker Compose | **`uv` + `[project.scripts]`** |
| 정의 형식 | DAG JSON | **YAML** (§11-4에서 확정 — 스키마는 그대로) |
| 자동 실행 | APScheduler | ⚠️ **미결정** — OS 스케줄러 vs `serve` (며칠 돌려 본 뒤 판단) |

변경의 전체 목록과 근거는 [ARCHITECTURE.md 부록 C](./ARCHITECTURE.md)에 있습니다.

---

## 사용법

### 1. 설치

```bash
git clone <저장소> marketscan && cd marketscan
uv sync                    # 의존성 설치 (uv.lock 고정 — Docker가 필요 없습니다)
uv run marketscan describe  # 설치 확인
```

매번 `uv run`을 붙이기 싫다면 PATH에 올립니다.

```bash
uv tool install .          # 이후 그냥 `marketscan describe`
```

`cp .env.example .env`는 **선택**입니다. 시세에 자격 증명이 필요 없어서(§3.3),
텔레그램·LLM을 붙이기 전까지는 채울 것이 없습니다.

### 2. 지금 무엇이 있는지 본다

```bash
marketscan describe
```

전략 목록과 소스 해시, 파이프라인의 유니버스 크기, 검증 통과 여부, 마지막 실행을 한 번에 보여 줍니다.
**에이전트에게 CLI를 쥐여 줄 때 첫 명령으로 쓰라고 알려 주면 됩니다.**

### 3. 실행해 본다 — 기본은 dry-run

```bash
marketscan run
```

```
실행 run_c606937a98e9 · pipe_demo · mode=notify · success
기준 시각 2026-08-01T06:30:00+00:00

종목         봉  as_of                      순위  전략
-----------  --  -------------------------  ----  -------------
krx:000660   1d  2026-07-31T06:30:00+00:00  1     demo_momentum
nasdaq:AAPL  1d  2026-07-31T20:00:00+00:00  2     demo_momentum
nasdaq:MSFT  1d  2026-07-31T20:00:00+00:00  3     demo_momentum

리포트 /path/to/reports/latest.html
※ dry-run입니다. signals 미기록 · 봉 미소비 (--commit으로 실행).
```

**`--commit`이 없으면 아무것도 남지 않습니다.** DB 파일조차 만들지 않습니다.
몇 번을 돌려도 안전하므로, 전략을 고치면서 마음껏 돌려 보세요.

**단일 실행은 외부로 아무것도 내보내지 않습니다.** 산출물은 위 출력과 HTML 리포트 둘뿐입니다.
텔레그램 같은 채널 전송은 상주 실행(`serve`)의 몫으로 미뤄져 있습니다 — 손으로 돌릴 때마다
채널로 메시지가 나가면 **알림 자체를 믿지 않게 되기** 때문입니다(§12.2).

| 자주 쓰는 옵션 | |
| :--- | :--- |
| `--market crypto\|krx\|us` | 해당 시장만 실행 |
| `--now 2026-08-01T06:30:00Z` | 기준 시각 고정 — **재현 실행의 핵심** |
| `--json` · `--limit N` | 기계용 출력 · 출력 건수 제한 |
| `--no-report` | HTML 리포트를 쓰지 않습니다 (기본은 씁니다) |
| `-p pipelines/other.json` | 다른 파이프라인 정의 |

### 4. 리포트를 본다

`reports/`에 자기완결적인 HTML 한 장이 떨어집니다. CDN·폰트·스크립트를 참조하지 않으므로
**반년 뒤에 열어도 그대로 보입니다.** 신호 표(순위·백분위·점수·전략 해시)와 노드별 실행 로그가
들어 있고, `--limit`과 무관하게 **전체 신호**가 실립니다.

| | 파일 |
| :--- | :--- |
| dry-run | `reports/latest.html` — **덮어씁니다.** 스무 번 돌려도 파일이 쌓이지 않습니다 |
| `--commit` | `reports/run_<run_id>.html` — 실제로 나간 판단만 이력으로 남습니다 |

### 5. 실제로 남긴다

```bash
marketscan run --commit
```

이때부터 두 가지 부작용이 열립니다 — **`signals` 기록 · 봉 소비**.

> ⚠️ 봉 소비는 되돌릴 수 없습니다. Fresh Bar Gate(§3.5)가 "이미 본 봉"으로 표시하므로,
> 같은 봉으로 다시 돌리면 **신호 0건**이 나옵니다. 이건 버그가 아니라 중복 방지입니다.

### 6. "이게 왜 떴어?"

```bash
marketscan signals list          # id 확인
marketscan explain 1
```

```
krx:000660 · 2026-07-31T06:30:00+00:00 (1d)
전략  demo_momentum @ c828301f3f71
순위  1 / 6  (상위 16.6667%)
데이터 synthetic · adjusted=True · fallback_from=[]
실행  run_d91342e5fe47 · success
판정  acted=None
```

`전략 @ 해시`와 `fallback_from`이 함께 나오는 것이 핵심입니다 —
**어떤 코드로, 어느 소스에서 나온 판단인지**가 사후에 복원됩니다(§4.7 / §3.4).

### 7. 전략을 쓴다

전략은 `strategies/` 아래 **파일 하나 = 전략 하나**입니다. IDE와 git을 그대로 씁니다.

```bash
marketscan strategy new my_factor      # Params가 포함된 템플릿 생성
$EDITOR strategies/my_factor.py       # compute를 채운다
marketscan strategy check my_factor    # 인과성 정적 검사
```

`compute`(종목별 시계열) → `rank`(횡단면 순위) → `select`(최종 컷) 순으로 돕니다.
**`rank`가 중심**이고, 단일 종목 전략이면 `compute`만 채우면 됩니다.

```python
class MyFactorStrategy(Strategy):
    id = "my_factor"
    timeframe = "1d"          # 1d / 1w만 허용됩니다
    startup_candles = 60      # 워밍업이 부족한 종목은 자동 제외
    score_feature = "score"   # 기본 rank가 이 값으로 유니버스를 줄 세웁니다

    class Params(BaseModel):
        lookback: int = Field(default=20, ge=2, le=500)

    def compute(self, item, p, ctx):
        close = item.ohlcv["close"]
        return item.with_features(score=float(close.iloc[-1] / close.iloc[-p.lookback] - 1))
```

`strategy check`가 `shift(-n)` · `center=True` · `bfill` · `datetime.now()` ·
네트워크 임포트를 AST로 잡습니다.

```
$ marketscan strategy check leaky
leaky — 위반 1건
  L13 [causality] shift(-1) — 미래 참조입니다. 타깃(정답 라벨) 계산이라면 백테스트 평가기로 옮기세요.
```

> ⚠️ **통과가 인과성을 보장하지는 않습니다.** 사후 방어선은 난수 신호 테스트입니다(§4.8).

전략 파일을 고치면 SHA-256이 바뀌고, 파이프라인에 기록된 해시와 다르면 실행 시 경고가 나옵니다.
과거 신호의 근거가 소급으로 바뀌는 것을 막기 위해서입니다(§4.7).

### 8. 파이프라인을 고친다

`pipelines/demo.yaml`이 기본값입니다. `MARKETSCAN_PIPELINE_PATH`로 바꾸거나 `-p`로 지정합니다.
노드는 **배선**입니다 — 지표 조건은 노드가 아니라 전략 클래스 안에 넣습니다(§5).

```
manualTrigger → symbolUniverse → marketData → strategyRunner → persistSignal → logAlert
```

**형식은 YAML로 확정됐습니다**(§11-4). 손으로 적어 보니 JSON의 문제는 구조가 아니라
**주석을 달 수 없다는 것**이었습니다 — 파이프라인 파일에 적고 싶은 것의 절반은 "왜 이 종목인가"
· "왜 이 값인가"입니다. §6의 스키마는 그대로이고 로더가 확장자로 갈라 받으므로 `.json`도
계속 읽힙니다. 저장 스냅샷(`pipeline_versions`)은 직렬화이므로 JSON을 유지합니다.

**유니버스는 손으로 적지 않습니다.** `symbolUniverse`가 거래소에 물어 거래대금 상위 N개를
뽑습니다 — 종목을 고정해 두면 "혼자서는 볼 수 없는 범위를 대신 본다"는 목적에 닿지 않습니다.

```yaml
- id: universe
  type: symbolUniverse
  params:
    venue: upbit
    quote_currency: KRW      # 원화 마켓만
    top_by_turnover: 30      # 24시간 거래대금 상위 30종목
```

> ⚠️ **동적 유니버스는 backtest 모드에서 거부됩니다.** 거래소 목록은 언제나 "지금"이라,
> 과거를 리플레이하면 **전략 코드가 완전히 인과적인 채로 유니버스가 미래를 봅니다**(§4.8
> 서바이버십). `strategy check`의 AST 검사에 걸리지 않는 경로라 노드가 직접 막습니다.

### 9. 자동으로 돌린다

⚠️ **아직 정하지 않았습니다**(§11-4b). 지금 확정된 것은 *무엇을 부르는가*뿐입니다.

```
marketscan run --market crypto --commit   # 코인
marketscan run --market krx    --commit   # 한국장 마감 후
marketscan run --market us     --commit   # 미국장 마감 후
```

OS 스케줄러에 맡길지, 스케줄·알림을 내장한 `serve` 명령을 둘지는 Phase 1에서 며칠 돌려 본 뒤
판단합니다. **어느 쪽이든 위 명령줄은 바뀌지 않습니다.**

Fresh Bar Gate(§3.5)가 시장별 마감을 알아서 걸러 주므로, `--market` 없이 전부 돌려도 됩니다 —
명시하는 쪽이 로그를 읽기 편할 뿐입니다.

### 종료 코드

크론이든 `serve`든 CI든, 상태는 종료 코드로 구분합니다.

| 코드 | 의미 |
| :--- | :--- |
| `0` | 성공 — **신호 0건도 여기입니다** |
| `2` | 데이터 소스·노드 실패 |
| `3` | 검증 실패 (파이프라인·전략·인자) |

"신호 0건"과 "실패"를 구분하지 않으면 자동 실행이 매일 실패로 잡힙니다(§4.1 / §12.3).

---

## 할 일

### Phase 0.5 — v0.5 전환 ✅ 완료

- [x] 프로젝트 개명 `tradeflow` → `marketscan` (환경변수 접두사 `MARKETSCAN_` 포함)
- [x] `frontend/` 삭제 — React·Vite·pnpm·TypeScript 툴체인 제거
- [x] `docker-compose.yml` · `backend/Dockerfile` 삭제 — 재현성은 `uv.lock`이 대신
- [x] **Typer CLI 골격** — `run` · `ingest`\* · `explain` · `signals` · `stats` · `describe` (§12)
- [x] `--commit` 규약 — 없으면 `signals` 미기록 · 봉 `discard()`
- [x] **알림은 단일 실행에서 분리** — `run`의 산출물은 stdout + HTML 리포트뿐 (§12.2)
- [x] 종료 코드 규약 — `0` 성공(신호 0건 포함) / `2` 소스 실패 / `3` 검증 실패
- [x] FastAPI · APScheduler 계층 제거 (`app/main.py` · `app/api/`)
- [x] 디렉터리 평탄화 `backend/app/` → `app/`
- [x] **`Strategy` 프로토콜** — `compute` / `rank` / `select` (§4.2)
- [x] `strategies/` 로더 + 소스 SHA-256 기록 (§4.7)
- [x] `marketscan strategy check` — AST 인과성 검사 (`shift(-n)` · `center=True` · `datetime.now`)
- [x] 타임프레임을 정책 계층에서 `1d`/`1w`로 제한 — **타입은 건드리지 않는다** (§3.6)
- [x] `Indicator` 범주 동결 — `maFilter`는 남기고 신규 추가 금지
- [x] `pyproject.toml`에 `[project.scripts]` 등록

\* `ingest`는 명령 표면만 있습니다. 실제 수집(`ohlcv_cache` · Ingestion Worker)은 Phase 2입니다 —
`--json` 출력의 `implemented: false`가 이 사실을 명시합니다.

**완료 기준 ✅**: `marketscan run --dry-run`이 더미 전략(`strategies/demo_momentum.py`)으로 끝까지 돕니다.

### Phase 1 — 전략 러너 & 단일 시장 E2E — **완료 기준 충족 · 결정 1건 남음**

**목적은 "돈이 되는 전략"이 아니라 "경로가 실제로 도는가"입니다.** 시장 하나로 좁혀
끝까지 완주해야, Phase 2에서 어댑터를 늘릴 때 문제가 소스 탓인지 전략 탓인지 구분됩니다.

- [x] **실제 시세 소스 1개** — `CcxtProvider`(업비트). 거래소당 파일을 만들지 않고
      차이는 `providers/ccxt_quirks/`로 뺐습니다
- [x] `Symbol Universe` → `Market Data` → `Strategy Runner` → **HTML 리포트** E2E
      (텔레그램은 `serve`로 빠졌습니다 — Phase 1의 "동작하는 물건"은 리포트입니다)
- [x] 첫 전략: 횡단면 모멘텀 12-1 — 표준값(252/21) 고정. `strategies/cross_momentum_12_1.py`
- [x] ★ **`bar_state` 영속화** — `SqlBarState`가 봉 상태를 SQLite에 남깁니다.
      게이트가 프로세스를 넘어 살아남습니다 (§3.5)
- [x] 캔들 마감 처리 · `signals`의 `acted` 응답 경로 (`marketscan signals ack`)
- [x] 파이프라인 정의 형식 — **YAML로 확정** (§11-4)
- [ ] **자동 실행 방식 결정** — OS 스케줄러 vs `serve`(스케줄+알림 내장) (§11-4b).
      **코드는 준비됐고 며칠 돌려 보는 일만 남았습니다** — `run --commit`이 실물로 돌고
      봉 게이트가 영속화되어, 무엇이 몇 번 부르든 같은 봉을 두 번 판정하지 않습니다

**완료 기준 ✅**: 업비트 실제 일봉으로 `marketscan run --commit`이 돌고, 나온 신호를
`explain`으로 되짚을 수 있으며, 같은 봉으로 다시 돌리면 stale로 걸러집니다.

⚠️ **시세는 코인만 실물입니다.** `krx` · `nasdaq`은 아직 `synthetic` 더미 소스이고,
실제 소스 4종은 Phase 2입니다.

### Phase 2 — 멀티 마켓 ★ 존재 이유 — **진행 중**

- [x] `MarketCalendar` 3종 (24x7 / KRX / US) + 서머타임 전환일 회귀 테스트
      — `exchange_calendars`(XKRX · XNYS). 휴장일·조기폐장이 실제 값으로 들어옵니다
- [x] 무인증 일봉 소스 4종 — `PykrxProvider` · `YFinanceProvider` · `FdrProvider` · `CcxtProvider`
- [x] Routing Table — `krx: pykrx→fdr` · `nasdaq/nyse: yfinance→fdr` · `upbit: ccxt`
- [ ] **폴백 가시화 회귀 테스트** — `failed_sources` 경로는 구현돼 있으나 실제 소스
      조합으로는 아직 검증하지 않았습니다
- [ ] **Ingestion Worker + `ohlcv_cache` 영구 보관** — 지금은 `Market Data`가 실행마다
      Provider를 직접 호출합니다. §3.9가 "캐시는 성능이 아니라 데이터 자산"이라고
      정한 지점이고, `marketscan ingest`는 여전히 `implemented: false`입니다
- [ ] **상장폐지 종목 수집** — `FdrProvider.list_delisted()`까지 됐고, **수집·보관은
      Ingestion Worker에 달려 있습니다.** 폐지 종목의 과거 일봉을 실제로 받을 수
      있다는 것은 확인했습니다 (2018년 이후 폐지 주권 표본 10/10)
- [ ] Fresh Bar Gate 혼합 파이프라인 검증 (코인+한국+미국 동시)
- [ ] 두 소스 종가 정합성 검증 (§3.8) — 같은 날 종가가 다르면 경고

**다음 세션은 `ohlcv_cache`부터 시작하는 것이 맞습니다.** 남은 항목 대부분이 캐시에
매달려 있습니다 — 상장폐지 수집도, 혼합 파이프라인 검증도 수집 계층이 있어야 합니다.

### Phase 3 — 백테스트

- [ ] 캘린더 기반 리플레이 + look-ahead assert
- [ ] **피처 행렬 사전 계산** — 유일한 실제 엔지니어링 (§4.8)
- [ ] **엔진 검증 4종** — 난수 신호 · 전량 매수 · 신호 1일 밀기 · 상장폐지 포함
- [ ] `backtest_runs` 다중검정 카운터
- [ ] 정적 HTML 리포트 생성 (`reports/`)

### Phase 4 — LLM 스크리닝

- [ ] LLM Provider 추상화 + `deterministic` / `cacheable` 플래그 (§5)
- [ ] `CommandProvider` — 로컬 agent 커맨드 호출 (API 키 불필요)
- [ ] **도구 사용 agent는 backtest 모드에서 하드 차단**
- [ ] `LLM Screen` — 공시·뉴스 기반 제외 판정
- [ ] `abstain` / `confidence` 1급 출력 + 임계 미달 시 신호 제거

### Phase 4.5 — 운용

- [ ] `shadow` 모드로 최소 몇 달 관찰
- [ ] **오버라이드 추적** — `signals.acted` + 무시한 신호의 사후 성과 (§4.8)
- [ ] 소액 시작

### 확인 필요

- [ ] PyPI · GitHub에서 `marketscan` 이름 충돌 확인
- [x] 상장폐지 종목의 과거 가격을 어디까지 받을 수 있는가 — **받을 수 있습니다.**
      PyKRX·FDR 모두 폐지 종목 일봉을 줍니다 (2018년 이후 폐지 주권 표본 10/10).
      `SecuGroup == '주권'`만 조회되고, 신주인수권증서 등은 빈 결과입니다
- [ ] 코인 일봉 경계 `UTC00` vs `KST00` — 일봉이 유일한 판단 단위라 중요도가 올라감

---

## 명령 요약

| 명령 | 부작용 | 용도 |
| :--- | :---: | :--- |
| `run [--market M] [--commit]` | **`--commit` 시에만** | 파이프라인 실행. **기본은 dry-run** |
| `ingest [--venue V]` | 없음 (Phase 2) | 일봉 수집 — 아직 미구현 |
| `explain <signal_id>` | 없음 | **왜 이 신호가 났는가** — 전략·해시·순위·소스·폴백 |
| `signals list` | 없음 | 신호 이력 |
| `signals ack <id> --acted\|--ignored` | 응답 기록 | **이 신호대로 움직였는가** (§4.8 오버라이드 추적) |
| `stats [--group-by G] [--compare acted]` | 없음 | 신호 건수·분산·오버라이드 |
| `describe` | 없음 | 전략·유니버스·마지막 실행 |
| `strategy new / check / list` | `new`만 파일 생성 | 전략 템플릿 · AST 인과성 검사 |

**모든 명령에 `--json`이 있습니다.** 붙이면 stdout에는 JSON만 나가고 진행 로그는 stderr로 갑니다 —
LLM에게 한국어 표를 파싱하게 두면 오독하기 때문입니다(§12.4).

**환경 변수** — 접두사는 `MARKETSCAN_`입니다. `.env.example` 참조.

| 변수 | 기본값 |
| :--- | :--- |
| `MARKETSCAN_PIPELINE_PATH` | `pipelines/demo.yaml` |
| `MARKETSCAN_STRATEGIES_DIR` | `strategies` |
| `MARKETSCAN_DATABASE_URL` | `sqlite+aiosqlite:///./data/marketscan.db` |

**시세에는 자격 증명이 필요 없습니다** — 일봉 고정으로 무인증 소스만 씁니다
(PyKRX · yfinance · FDR · CCXT 공개 OHLCV). 남는 비밀은 텔레그램 토큰과 LLM 키뿐입니다.

---

## Development

```bash
uv sync              # 의존성 설치 (uv.lock 고정)
uv run pytest -q     # 테스트
uv run ruff check app tests
```

---

## 프로젝트 구조

```
marketscan/
├── ARCHITECTURE.md            설계 문서 (단일 출처)
├── CLAUDE.md                  작업 규칙
├── pipelines/demo.yaml        파이프라인 정의 (YAML 확정 — §6 스키마 그대로)
├── strategies/                ★ 사용자 전략 — 파일 하나 = 전략 하나. 해시가 실행에 박힌다
├── data/                      SQLite (백업 대상 · --commit 실행에서만 생성)
└── app/
    ├── cli/                   Typer 명령 · 출력 규약 · 종료 코드
    ├── engine/                Bundle·Item 계약, RunContext, DAG 검증·실행, 신호 배출구
    ├── strategies/            Strategy 프로토콜 · 로더(SHA-256) · AST 인과성 검사
    ├── market/                InstrumentRef, MarketCalendar, 타임프레임 정책
    ├── providers/             시세 소스 플러그인 + 라우팅·폴백
    ├── nodes/                 배선용 노드 (트리거·입력·전략·로직·액션)
    └── storage/               SQLAlchemy 모델 · 실행 이력 · 신호
```

---

## 설계에서 눈여겨볼 것

| | |
| :--- | :--- |
| **멀티 마켓 추상화** | `InstrumentRef` · `MarketCalendar` · Provider 라우팅. **이 프로젝트의 존재 이유** — freqtrade에도 유사 국내 도구에도 없는 부분입니다 |
| **Fresh Bar Gate** | 미국장이 닫힌 시간에도 코인은 계속 판정되고 주식은 조용히 제외됩니다. 캘린더를 신경 쓸 필요가 없습니다 |
| **소스는 노드가 아니다** | 데이터 소스는 Connection + 라우팅 표로 관리합니다. 파이프라인이 특정 소스에 묶이지 않고, 죽으면 폴백합니다 |
| **`ctx.now` 주입** | 노드는 `datetime.now()`를 쓰지 않습니다. 백테스트와 실행이 같은 코드 경로를 씁니다 |
| **횡단면 우선** | 1급 연산은 "한 시점에 유니버스를 줄 세우는 것"입니다. 표본 수·시장 방향 상쇄·팩터의 정의가 모두 여기서 나옵니다 |
| **백테스트의 용도** | "이 전략이 돈이 되나"가 아니라 **"내 구현이 안 틀렸나"** 입니다. 난수 신호의 hit rate가 기저율을 넘으면 미래 참조가 있는 것입니다 |
