# marketscan

정해둔 전략으로 암호화폐·한국주식·미국주식을 훑어 **볼 만한 후보를 리스트업**하는 개인용 CLI입니다.

| 명령 | 하는 일 |
| :--- | :--- |
| **`run`** | 오늘의 후보를 뽑습니다. `--commit`이면 기록에 남습니다 |
| **`backtest`** | 한 종목을 하루씩 되감아 **조건을 만족한 날을 차트에** 찍습니다 |
| **`serve`** | 웹 UI로 위를 다 쓰고, 스케줄로 돌리고 알림을 보냅니다 |

**예측 기계가 아니라 주의력 기계입니다.** 시장을 맞히는 것이 아니라, 혼자서는 볼 수 없는 범위를
대신 보고 정해둔 규칙을 대신 지키는 것이 목적입니다. 최종 판단은 사람이 합니다.

★ **스크리너는 내버려 두면 자신감 기계가 됩니다** — 사람은 맞은 종목만 기억하니까요.
그래서 `signals`에 내보낸 신호가 **전부** 남고, `backtest` 리포트는 그 마커가 실제 신호가
아니라는 사실을 화면에 붙박이로 적습니다. 보기 싫은 것을 같이 보여 주는 쪽이 기본값입니다.

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

> **Phase 1·2 완료 — 세 시장이 하나의 파이프라인으로 돕니다**
>
> `marketscan run --commit`이 **업비트 실제 일봉**으로 끝까지 돕니다 — 거래소에서 유니버스를
> 뽑고, 12-1 모멘텀으로 줄 세우고, 신호를 남기고, `explain`으로 되짚을 수 있습니다.
> **시세 소스 4종(PyKRX · yfinance · FDR · CCXT)과 `ohlcv_cache` 수집 계층이 붙었습니다.**
>
> **★ 세 시장을 한 번에 훑습니다.** 파이프라인 하나로 코인·한국·미국이 동시에 돕니다.
> 실측(2026-08-03 기준, `-p sample/demo.yaml` 12-1 모멘텀):
>
> ```
> market     pool  signals   상위
> crypto       39        7   upbit:KRW-USDT, KRW-NEAR, KRW-AKT
> krx         195       39   krx:043260, krx:010170, krx:009150
> us           95       19   nasdaq:SNDK, nasdaq:WDC, nasdaq:LITE
> ```
>
> **순위가 시장마다 1번부터 다시 시작합니다** — 랭킹·컷이 시장 안에서만 이뤄지기
> 때문입니다(규칙 17). 섞어 세면 분산이 넓은 코인이 위를 쓸어가 모멘텀이 아니라
> 변동성으로 줄 세운 것이 됩니다.
>
> **기본 전략은 추세추종(`trend_breakout_55`)입니다.** 같은 날 신호는 2건이었습니다 —
> 돌파는 상태가 아니라 **사건**이라 0건인 날이 대부분입니다. 그게 정상입니다.
>
> **Phase 3이 닫혔습니다** — `backtest`(§10)와 `serve`(§11: 웹 UI + 스케줄 + 알림 +
> 하루 1회 하트비트)가 모두 붙었습니다.
> ⚠️ 남은 것은 **사후 검증**입니다 — 신호별 사후 수익률·hit rate·IC (Phase 3.5).

v0.5 전환이 끝났습니다. 아래가 v0.4에서 바뀐 것들입니다.

| | v0.4 | **v0.5 (현재)** |
| :--- | :--- | :--- |
| 인터페이스 | FastAPI + React 캔버스 | **CLI (Typer) + 서버 렌더 UI**(빌드 단계 없음) |
| 전략 표현 | 지표 노드 조합 | **파이썬 클래스 1개** |
| 판단 단위 | 백테스트 일봉 / 알림 분봉 | **일봉·주봉 전용** |
| 전략의 축 | 시계열 필터 체인 | **횡단면 랭킹** |
| 배포 | Docker Compose | **`uv` + `[project.scripts]`** |
| 정의 형식 | DAG JSON | **YAML** (§11-4에서 확정 — 스키마는 그대로) |
| 자동 실행 | APScheduler | **`serve`로 확정** (§11-4b) — 구현은 Phase 3 |
| 결과 확인 | 실행 로그뿐 | **`backtest` — 날짜별 리플레이 + 차트** (§12.7) |

변경의 전체 목록과 근거는 [ARCHITECTURE.md 부록 C](./ARCHITECTURE.md)에 있습니다.

---

## 사용법

### 1. 설치

```bash
git clone <저장소> marketscan && cd marketscan
uv sync                    # 의존성 설치 (uv.lock 고정 — Docker가 필요 없습니다)

mkdir -p ~/.marketscan && cp sample/* ~/.marketscan/   # 설정과 전략을 한 벌로 복사
uv run marketscan describe  # 설치 확인
```

**설정·전략·DB·리포트가 전부 `~/.marketscan/`에 삽니다.** 저장소 바깥이라 코드를 지우고 다시
받아도 내 전략과 캐시가 남습니다 — 특히 `ohlcv_cache`는 무료 소스가 막혀도 남는 유일한
자산이라(§3.9) 저장소와 수명을 같이하면 안 됩니다.

기본 설정은 `~/.marketscan/config.yml`이고, **거기서 부르는 전략 파일은 같은 디렉터리에서
찾습니다.** 설정과 전략은 한 벌이라 통째로 복사·백업할 수 있어야 하기 때문입니다. 저장소의
`sample/`은 그 한 벌의 예제일 뿐입니다 — `-p sample/demo.yaml`로 부르면 `sample/`의 전략을
집습니다.

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
실행 run_875ba1e0bee8 · pipe_multimarket_momentum · mode=notify · success
기준 시각 2026-08-04 06:00 (KST)

종목             이름            종가 (등락)      봉  봉 마감 (KST)     시장    순위  전략
---------------  --------------  ---------------  --  ----------------  ------  ----  -------------------
upbit:KRW-USDT   테더            1,431 (-0.76%)   1d  2026-08-03 09:00  crypto  1     cross_momentum_12_1
upbit:KRW-NEAR   니어프로토콜    2,455 (+1.66%)   1d  2026-08-03 09:00  crypto  2     cross_momentum_12_1
krx:043260       성호전자        15,870 (+6.22%)  1d  2026-08-03 15:30  krx     1     cross_momentum_12_1
nasdaq:SNDK      Sandisk Corp    1,249 (+2.78%)   1d  2026-08-04 05:00  us      1     cross_momentum_12_1

리포트 ~/.marketscan/reports/latest.html
※ dry-run입니다. signals 미기록 · 봉 미소비 (--commit으로 실행).
```

**순위가 시장마다 1번부터 다시 시작합니다.** 랭킹·컷이 시장 안에서만 이뤄지기 때문입니다(규칙 17).

**`--commit`이 없으면 판단이 남지 않습니다** — `signals` 미기록, 봉 미소비. 몇 번을 돌려도
안전하므로 전략을 고치면서 마음껏 돌려 보세요.

> **단, 받아 온 봉과 종목 목록은 `~/.marketscan/data/marketscan.db`에 쌓입니다.** 이건 판단이 아니라
> 자료라서 dry-run에도 열려 있습니다 — 버리면 무료 API를 두 번 두드리게 됩니다(§3.9).
>
> **실측: 1회차 64초 → 2회차 7초.** 봉은 `ohlcv_cache`, 종목 목록은 `instruments`에서
> 나옵니다. 다만 **거래대금은 캐시하지 않습니다** — 어제의 상위 60종목을 오늘 훑게 되면
> 그건 성능이 아니라 판단이 달라지는 문제입니다(§4.7).

**단일 실행은 외부로 아무것도 내보내지 않습니다.** 산출물은 위 출력과 HTML 리포트 둘뿐입니다.
텔레그램 같은 채널 전송은 상주 실행(`serve`)의 몫으로 미뤄져 있습니다 — 손으로 돌릴 때마다
채널로 메시지가 나가면 **알림 자체를 믿지 않게 되기** 때문입니다(§12.2).

| 자주 쓰는 옵션 | |
| :--- | :--- |
| `--market crypto\|krx\|us` | 해당 시장만 실행 |
| `--now 2026-08-01T06:30:00Z` | 기준 시각 고정 — **재현 실행의 핵심** |
| `--json` · `--limit N` | 기계용 출력 · 출력 건수 제한 |
| `--no-report` | HTML 리포트를 쓰지 않습니다 (기본은 씁니다) |
| `-p sample/demo.yaml` | 다른 파이프라인 정의 |

### 4. 리포트를 본다

`~/.marketscan/reports/`에 자기완결적인 HTML 한 장이 떨어집니다. CDN·폰트·스크립트를 참조하지 않으므로
**반년 뒤에 열어도 그대로 보입니다.** `--limit`과 무관하게 **전체 신호**가 실립니다.

```
종목         이름       종가 (등락)        시장  봉 마감 (KST)     순위  유니버스  백분위
krx:005930   삼성전자   239,500 (+2.34%)   krx   2026-08-03 15:30  3     195      1.5385
nasdaq:SNDK  Sandisk    1,249 (+2.78%)     us    2026-08-04 05:00  1     95       1.0526
```

| | |
| :--- | :--- |
| **정렬** | **점수가 아니라 백분위 순.** 점수는 시장마다 스케일이 달라 한 줄로 세우면 그 기간에 잘 간 시장이 위를 통째로 차지합니다 — 종목이 아니라 시장을 고른 표가 됩니다 |
| **색** | 등락률에만. **상승 빨강 · 하락 파랑** (국내 HTS 관례) |
| **시각** | 모두 KST. 저장은 UTC이고 표시할 때만 변환합니다(§4.4) |
| **자릿수** | 크기에서 유도합니다 — `1,181,000` · `521.57` · `0.00000123` |

> ⚠️ **미국 종목은 날짜가 하루 뒤로 보입니다.** 8/3 세션이 KST로는 다음 날 새벽
> 05:00에 닫히기 때문입니다. 값은 정확하고, 리포트에도 같은 주석이 달려 있습니다.

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
upbit:KRW-USDT (테더) · 봉 마감 2026-08-03 09:00 (KST) · 1d
종가  1,431 (-0.76%)
전략  cross_momentum_12_1 @ c2ced90f5da5
순위  1 / 39 (crypto)  (상위 2.5641%)  — 360종목을 훑어 321종목은 봉 부족으로 제외
데이터 cache(ccxt.upbit) · adjusted=False · fallback_from=[]
실행  run_875ba1e0bee8 · success
판정  acted=None
```

세 가지가 함께 나오는 것이 핵심입니다.

- **`전략 @ 해시`** — 어떤 코드로 나온 판단인가 (§4.7)
- **`데이터 cache(원래소스)` · `fallback_from`** — 어느 소스에서 나왔는가 (§3.4).
  캐시에서 읽었어도 **그 구간을 채운 원래 소스**를 밝힙니다
- **`순위 1 / 39 (crypto)`** — 무엇과 비교한 순위인가. 시장을 밝히지 않으면
  39가 무엇의 39인지 알 수 없습니다 (규칙 17)

### 7. 전략을 쓴다

전략은 `~/.marketscan/` 아래 **파일 하나 = 전략 하나**입니다. IDE와 git을 그대로 씁니다.

```bash
marketscan strategy new my_factor      # Params가 포함된 템플릿 생성
$EDITOR ~/.marketscan/my_factor.py     # compute를 채운다
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

`~/.marketscan/config.yml`(추세추종)이 기본값입니다. 12-1 횡단면 모멘텀은 `demo.yaml`에
그대로 있고 `-p ~/.marketscan/demo.yaml`로 부릅니다. `MARKETSCAN_PIPELINE_PATH`로도 바꿉니다.
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
    venues:                                             # 시장마다 컷 조건이 다릅니다
      - { venue: upbit, quote_currency: KRW, top_by_turnover: 60 }
      - { venue: krx,   top_by_turnover: 200 }
      - { venue: nasdaq, limit: 100 }                   # 미국 목록엔 거래대금이 없습니다
```

**노드는 하나로 둡니다.** 시장마다 노드를 만들면 `Bundle.merge`가
`context["universe"]`를 덮어써 두 시장이 소리 없이 사라집니다 — 자세한 것은 아래
[Phase 2 절](#세-시장을-어떻게-한-번에-훑는가)에 있습니다.

> ⚠️ **동적 유니버스는 backtest 모드에서 거부됩니다.** 거래소 목록은 언제나 "지금"이라,
> 과거를 리플레이하면 **전략 코드가 완전히 인과적인 채로 유니버스가 미래를 봅니다**(§4.8
> 서바이버십). `strategy check`의 AST 검사에 걸리지 않는 경로라 노드가 직접 막습니다.

### 9. 봉을 미리 모아 둔다

```bash
marketscan ingest                    # 무엇을 모을지와 지금 캐시에 뭐가 있는지 (기본)
marketscan ingest --commit           # 실제로 모아 ohlcv_cache에 쌓는다
marketscan ingest --venue krx --include-delisted --commit
```

```
수집 대상 60종목 · 캐시 없음 3종목

종목           봉  요청  캐시  마지막 봉
-------------  --  ----  ----  -------------------------
upbit:KRW-BTC  1d  320   320   2026-08-03T00:00:00+00:00
upbit:KRW-ETH  1d  320   0     -
```

**수집 대상은 파이프라인에서 자동으로 나옵니다.** 유니버스 노드를 실제로 돌려
그날 훑을 종목을 그대로 씁니다 — 목록을 따로 관리하면 파이프라인을 고칠 때마다
어긋나고, 어긋난 종목은 캐시가 비어 조용히 빠집니다.

| | |
| :--- | :--- |
| **왜 실행에서 떼어 냈나** | 노드가 매 실행마다 API를 부르면 200종목을 훑는 순간 무료 소스가 막힙니다. 레이트 리밋을 한 곳에서만 밟습니다 |
| **왜 지우지 않나** | `ohlcv_cache`는 성능 최적화가 아니라 **영구 보관하는 데이터 자산**입니다. yfinance가 막혀도 쌓인 이력으로 파이프라인과 백테스트가 계속 돕니다. **백업 대상입니다** |
| **하루에 몇 번 불러도 되나** | 됩니다. 이미 그 봉까지 성공한 대상은 소스를 두드리지 않습니다 (`--force`로 무시) |
| **폐지 종목은?** | `--include-delisted`. **폐지 시점을 기준으로** 모읍니다 — 오늘 기준으로 조회하면 그 구간에 봉이 없어 빈 결과가 성공처럼 보입니다(§4.8 서바이버십) |

`marketData` 노드의 `cache` 파라미터가 읽는 방식을 정합니다.

| 값 | 동작 |
| :--- | :--- |
| `auto` (기본) | 캐시를 먼저 보고, 요청 구간을 못 채우면 소스로 갑니다 |
| `off` | 항상 소스를 부릅니다 (정합성 비교·디버깅) |
| `only` | **외부 호출을 하지 않습니다.** 커버리지가 모자라면 사유와 함께 그 종목을 제외합니다 |

> ★ **dry-run도 캐시에 씁니다.** `--commit`이 막는 것은 **되돌릴 수 없는 것** 셋입니다 —
> 알림 발송 · `signals` 기록 · 봉 소비. 봉을 캐시에 넣는 건 판단이 아니라 자료 축적이고,
> 받아 놓고 버리면 무료 API를 두 번 두드리게 됩니다. 그래서 `marketscan run` 한 번이면
> **DB(`~/.marketscan/data/marketscan.db`)가 생기고 봉이 쌓입니다.** 대신 `signals`는 비어 있고
> `bar_state` 테이블은 생기지도 않습니다.

같은 봉을 두 소스가 다르게 주면 수집이 **그 자리에서 경고**합니다(§3.8).
덮어쓰되 조용히 덮지 않습니다 — 수정주가 정책 차이로 생긴 계열 불연속은
사후에 찾을 방법이 이것뿐입니다.

### 10. 그날 이 종목은 어땠나 — `backtest` ★

```bash
marketscan backtest krx:005930     --start 20251201              # 한국주식 (삼성전자)
marketscan backtest nasdaq:AAPL    --start 20251201              # 미국주식 (Apple)
marketscan backtest upbit:KRW-BTC  --start 20251201              # 암호화폐 (비트코인)

marketscan backtest 삼성전자 --start 2025-12-01 --end 2026-03-01  # 이름으로도 됩니다
# → ~/.marketscan/reports/backtest_krx_005930_20251201_20260301.html
```

**종목은 `venue:symbol`로 적습니다.** 시장을 심볼 모양으로 추측하지 않습니다 — `BTC`는
나스닥에도 있고, venue를 모르면 어느 캘린더로 봉을 자를지·어느 통화로 표시할지·누구와
비교할지가 정해지지 않습니다(§3.1).

| 넣고 싶은 것 | 이렇게 적습니다 | 시장 | 심볼 형식 |
| :--- | :--- | :--- | :--- |
| 한국주식 | `krx:005930` · `krx:000660` | `krx` | 6자리 종목코드 |
| 미국주식 | `nasdaq:AAPL` · `nasdaq:NVDA` · `nyse:KO` | `us` | 티커. **거래소를 구분해 적습니다** |
| 암호화폐 (업비트) | `upbit:KRW-BTC` · `upbit:KRW-ETH` | `crypto` | `결제통화-기초자산` |
| 암호화폐 (바이낸스) | `binance:BTC/USDT` | `crypto` | `기초자산/결제통화` |

- **시장(`krx`/`us`/`crypto`)은 venue에서 유도됩니다.** `nasdaq`과 `nyse`는 **같은 `us`**라
  한 풀에서 순위를 매깁니다. 이 값이 `--market` 필터와 랭킹 풀이 함께 쓰는 어휘입니다(규칙 17).
- **결제 통화도 venue가 정합니다.** 주식은 고정(KRW·USD)이고, 코인은 심볼에서 읽습니다 —
  업비트에는 KRW 말고 `BTC-ETH` 같은 BTC 마켓도 있어서 상수로 박으면 조용히 틀립니다.
- **이름·심볼만 적으면**(`삼성전자`, `005930`) 설정 파일이 훑는 시장의 목록에서 찾고,
  여러 시장에 걸리면 후보를 보여 주고 멈춥니다. `venue:symbol`로 적으면 이 조회가 없습니다.

같은 표기를 파이프라인의 `instruments`에도 그대로 씁니다.

```yaml
instruments: [krx:005930, nasdaq:AAPL, upbit:KRW-BTC]
```

`--start`부터 **하루씩 되감으며** 전략을 다시 돌립니다. 그날까지 마감된 봉만 잘라서 넣으므로
전략은 미래를 볼 수 없고, 조건을 만족한 날이 캔들 차트에 마커로 찍힙니다.

```
백테스트 krx:005930 (삼성전자) · trend_breakout_55 @ 037774d6eb16
기간 2025-12-01 ~ 2026-08-04 · 판정 167일 · 워밍업 부족 0일 (필요 253봉)

세션        봉 마감 (KST)     종가     근거
----------  ----------------  -------  ---------------------------------------------
2025-12-26  2025-12-26 15:30  117,000  trend_strength=3.409 · tsmom_12m=1.167
2026-01-02  2026-01-02 15:30  128,500  trend_strength=3.77 · tsmom_12m=1.311
```

| | |
| :--- | :--- |
| **용도** | **"내 구현이 안 틀렸나"** 하나입니다. "이 전략이 돈이 되나"가 아닙니다(§4.8) |
| **부작용** | 없습니다. `signals`를 남기지 않고 봉도 소비하지 않습니다 |
| **종목 지정** | `krx:005930`이 정확하고 빠릅니다. 이름·심볼만 적으면 설정이 훑는 시장의 목록에서 찾습니다 |
| **전략** | 기본은 설정 파일의 전략과 **그 파라미터**입니다. `--strategy`로 바꿉니다 |

> ⚠️ **마커는 "조건 충족일"이지 "실제 신호일"이 아닙니다.**
> 전략은 `compute`(종목별) → `rank`(횡단면) → `select`(컷) 순서인데, **종목 하나만** 보면
> "상위 N개" 컷의 후보가 1개라 항상 통과합니다. 실제 실행에서는 같은 날 조건을 통과한 다른
> 종목에 밀렸을 수 있습니다 — 그날 그 시장의 통과 종목이 컷보다 적었다면 두 값은 같습니다.
> 리포트 상단에도 같은 경고가 붙박이로 들어갑니다. 순위·백분위는 표에서 뺐습니다(유니버스가
> 1이라 언제나 "1 / 1 · 상위 100%"가 되어 정보가 아니라 오해입니다).

**정적 HTML 한 장입니다. 서버가 아닙니다.** 차트 라이브러리(`lightweight-charts`)는
저장소에 **vendoring해서 인라인**합니다 — CDN을 걸면 반년 뒤 그 화면이 깨지는데,
반년 전 판단을 되짚는 것이 이 화면의 목적입니다.

> ⚠️ **`review`(신호 이력 + 이후 경로)는 없앴고, `backtest`가 그 자리를 대신하지 않습니다.**
> 하나는 내가 고른 종목의 과거 재생이고, 다른 하나는 실제로 낸 신호의 사후 성적입니다.
> 후자를 보는 창구는 `signals`·`stats`이고 **사후 수익률·hit rate·IC는 아직 비어 있습니다**(§4.8).

### 11. 브라우저에서 쓴다 — `serve` ★

```bash
marketscan serve                 # http://127.0.0.1:8765
marketscan serve --port 9000
```

대시보드에서 **지금 지원하는 것을 그대로** 씁니다 — `run`(dry-run·기록) · `backtest` ·
`ingest` 버튼과, 만들어진 **리포트 HTML을 바로 여는 목록**입니다. 전략 목록·소스 해시·
유니버스·마지막 실행도 한 화면에 있습니다.

| | |
| :--- | :--- |
| **왜 v0.4로 돌아간 게 아닌가** | 걷어낸 것은 *파이프라인을 캔버스로 편집하는 React 앱*이고, 이건 **있는 명령을 부르고 결과를 띄우는 서버 렌더 화면**입니다. Node·pnpm·빌드 단계가 없습니다 |
| **버튼과 CLI가 같은가** | 같습니다. 둘 다 `app/service.py`를 지납니다 — 갈라지면 규칙 11·13이 화면 쪽으로 우회됩니다 |
| **알림** | ⚠️ **화면에서 누른 실행은 알림을 보내지 않습니다.** 손으로 돌릴 때마다 채널로 나가면 알림을 믿지 않게 됩니다(§12.2). 알림은 스케줄 실행만 |
| **동시 실행** | 한 번에 하나입니다. 겹친 `--commit` 둘은 같은 봉을 두 번 소비합니다 |

> ⚠️ **기본 바인딩은 `127.0.0.1`입니다.** 이 화면은 인증이 없고 `--commit` 실행을 부를 수
> 있으며, 프로세스는 `~/.marketscan` 전체(DB 포함)에 접근합니다. 외부에 열려면 그 사실을
> 알고 여세요.

### 11b. 자동으로 돌린다 — 스케줄·알림 ★

설정 파일의 `scheduleTrigger`가 실행 시각을 갖습니다. **`serve`의 설정이 아니라
파이프라인의 성질**이라, 설정 파일을 복사하면 스케줄도 함께 갑니다.

```yaml
- id: trigger
  type: scheduleTrigger
  params:
    timezone: Asia/Seoul
    at:
      - { time: "09:10", market: crypto, note: "업비트 일봉 마감(09:00 KST) 뒤" }
      - { time: "15:40", market: krx, note: "KRX 마감(15:30) 뒤" }
      - { time: "06:10", market: us, note: "미국장 마감 뒤 — 서머타임에 움직입니다" }
    heartbeat: "09:00"    # ★ 신호 0건이어도 하루 1회
```

**시각은 로컬 기준으로 적고 마감과의 관계를 함께 적습니다.** 마감에서 자동으로 유도하지
않는 이유는 서머타임입니다 — 미국장 마감은 한국 시각으로 1년에 두 번 한 시간씩 움직이는데,
유도한 값은 그 사실을 화면 어디에도 남기지 않습니다.

| | |
| :--- | :--- |
| **알림** | 신호가 있을 때만 보냅니다. 0건마다 오면 알림을 보지 않게 되고, 그 자리는 하트비트가 맡습니다 |
| **하트비트** | ★ 신호 0건이어도 하루 1회. 없으면 **"신호가 없는 것"과 "프로세스가 죽은 것"이 구분되지 않습니다** |
| **켤 때** | 시작 시각보다 앞서 지난 오늘 슬롯은 **건너뜁니다**(화면에 표시). 몰아서 돌리면 봉을 한꺼번에 소비합니다 |
| **채널** | `MARKETSCAN_TELEGRAM_TOKEN`·`..._CHAT_ID`가 있으면 텔레그램, 없으면 **기록만** 합니다(화면에서 볼 수 있습니다) |

**알림 설정 (텔레그램)**

```bash
# 1) 봇을 만들고 토큰을 받습니다 — 텔레그램에서 @BotFather → /newbot
# 2) 그 봇에게 아무 말이나 한 번 보낸 뒤 chat_id를 확인합니다
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"   # result[].message.chat.id

# 3) ~/.marketscan/.env 에 적습니다 (설정·전략·DB와 같은 곳)
MARKETSCAN_TELEGRAM_TOKEN=123456:AA...
MARKETSCAN_TELEGRAM_CHAT_ID=987654321

# 4) 지금 확인합니다 — 스케줄 시각까지 기다리지 않습니다
marketscan alert-test        # 화면에도 '테스트 알림 보내기' 버튼이 있습니다
```

> ⚠️ **토큰은 설정 파일(`config.yml`)이 아니라 환경변수로 받습니다**(규칙 7).
> 설정 파일은 복사·공유되기 때문입니다. `.env`는 **실행 디렉터리와 `~/.marketscan/`**
> 두 곳에서 읽습니다 — 후자에 두면 어디서 `serve`를 띄우든 잡힙니다.

`serve`가 상주하면서 시장별 마감 뒤에 아래를 부릅니다. **어느 것도 새 동작이 아니고, 손으로
치던 명령을 그대로 부를 뿐입니다.**

```
marketscan ingest --commit                # 일봉 수집
marketscan run --market crypto --commit   # 코인
marketscan run --market krx    --commit   # 한국장 마감 후
marketscan run --market us     --commit   # 미국장 마감 후
```

**알림이 나가는 명령은 `serve` 하나뿐입니다.** 손으로 `run`을 돌릴 때는 채널로 아무것도
나가지 않습니다 — 손으로 돌릴 때마다 메시지가 오면 알림 자체를 믿지 않게 되기 때문입니다(§12.2).

OS 스케줄러 대신 `serve`를 고른 것은 **알림 때문**입니다. 어느 쪽이든 전송 주체는 필요한데,
OS 쪽으로 가면 재시도·백오프가 OS 설정으로 흩어지고 윈도우 작업 스케줄러의 마찰이 더해집니다.

> ⚠️ **대가는 상주 프로세스입니다.** 조용히 죽으면 알림이 안 오는데, **알림이 없는 것과
> 신호가 0건인 것이 구분되지 않습니다.** 그래서 `serve`는 신호가 0건이어도 **하루 1회
> 하트비트를 보냅니다.** 이게 없으면 언제부터 죽어 있었는지 알 수 없습니다.

Fresh Bar Gate(§3.5)가 시장별 마감을 알아서 걸러 주므로 `--market` 없이 전부 돌려도 됩니다 —
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

> **다음 작업은 Phase 3.5(사후 검증)입니다.** Phase 1·2·3이 끝났습니다.
> `review`를 없애며 비워 둔 자리 — 신호별 사후 수익률 → `stats`의 hit rate·IC →
> 엔진 검증 4종 — 이 남아 있고, 그때까지 out-of-sample 검증이 얇습니다.

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
- [x] 전략 로더 + 소스 SHA-256 기록 (§4.7)
- [x] `marketscan strategy check` — AST 인과성 검사 (`shift(-n)` · `center=True` · `datetime.now`)
- [x] 타임프레임을 정책 계층에서 `1d`/`1w`로 제한 — **타입은 건드리지 않는다** (§3.6)
- [x] `Indicator` 범주 동결 — `maFilter`는 남기고 신규 추가 금지
- [x] `pyproject.toml`에 `[project.scripts]` 등록

\* `ingest`는 Phase 0.5에서 명령 표면만 있었고, 실제 수집(`ohlcv_cache` · Ingestion Worker)은
Phase 2에서 채워졌습니다.

**완료 기준 ✅**: `marketscan run --dry-run`이 더미 전략(`sample/demo_momentum.py`)으로 끝까지 돕니다.

### Phase 1 — 전략 러너 & 단일 시장 E2E — **완료 기준 충족 · 결정 1건 남음**

**목적은 "돈이 되는 전략"이 아니라 "경로가 실제로 도는가"입니다.** 시장 하나로 좁혀
끝까지 완주해야, Phase 2에서 어댑터를 늘릴 때 문제가 소스 탓인지 전략 탓인지 구분됩니다.

- [x] **실제 시세 소스 1개** — `CcxtProvider`(업비트). 거래소당 파일을 만들지 않고
      차이는 `providers/ccxt_quirks/`로 뺐습니다
- [x] `Symbol Universe` → `Market Data` → `Strategy Runner` → **HTML 리포트** E2E
      (텔레그램은 `serve`로 빠졌습니다 — Phase 1의 "동작하는 물건"은 리포트입니다)
- [x] 첫 전략: 횡단면 모멘텀 12-1 — 표준값(252/21) 고정. `sample/cross_momentum_12_1.py`
- [x] ★ **`bar_state` 영속화** — `SqlBarState`가 봉 상태를 SQLite에 남깁니다.
      게이트가 프로세스를 넘어 살아남습니다 (§3.5)
- [x] 캔들 마감 처리 · `signals`의 `acted` 응답 경로 (`marketscan signals ack`)
- [x] 파이프라인 정의 형식 — **YAML로 확정** (§11-4)
- [ ] **자동 실행 방식 결정** — OS 스케줄러 vs `serve`(스케줄+알림 내장) (§11-4b).
      **코드는 준비됐고 며칠 돌려 보는 일만 남았습니다** — `run --commit`이 실물로 돌고
      봉 게이트가 영속화되어, 무엇이 몇 번 부르든 같은 봉을 두 번 판정하지 않습니다

**완료 기준 ✅**: 업비트 실제 일봉으로 `marketscan run --commit`이 돌고, 나온 신호를
`explain`으로 되짚을 수 있으며, 같은 봉으로 다시 돌리면 stale로 걸러집니다.

⚠️ **Phase 1 시점의 시세는 코인만 실물이었습니다.** `krx` · `nasdaq` 실제 소스는
아래 Phase 2에서 붙었습니다.

### Phase 2 — 멀티 마켓 ★ 존재 이유 — **완료**

- [x] `MarketCalendar` 3종 (24x7 / KRX / US) + 서머타임 전환일 회귀 테스트
      — `exchange_calendars`(XKRX · XNYS). 휴장일·조기폐장이 실제 값으로 들어옵니다
- [x] 무인증 일봉 소스 4종 — `PykrxProvider` · `YFinanceProvider` · `FdrProvider` · `CcxtProvider`
- [x] Routing Table — `krx: pykrx→fdr` · `nasdaq/nyse: yfinance→fdr` · `upbit: ccxt`
- [x] **폴백 가시화 회귀 테스트** — 실제 조합(`pykrx→fdr`)으로 앞 소스를 죽여
      `failed_sources`가 `Item.meta["fallback_from"]`과 경고 로그까지 닿는지 확인합니다
      (`tests/test_fallback.py`)
- [x] **Ingestion Worker + `ohlcv_cache` 영구 보관** — `marketscan ingest --commit`이
      실제로 모읍니다. 노드는 `ctx.ohlcv`만 보고 **뒤가 캐시인지 모릅니다**(§3.9).
      `adjusted`가 키에 들어가고(규칙 8) `source_id`가 남습니다
- [x] **상장폐지 종목 수집** — `ingest --include-delisted`. **폐지 시점을 `end`로
      잡습니다** — 오늘 기준으로 조회하면 그 구간에 봉이 없어 빈 결과가 성공처럼 보입니다
- [x] 두 소스 종가 정합성 검증 (§3.8) — 같은 봉을 다른 소스가 다르게 주면
      캐시 쓰기가 그 자리에서 잡아 `ingest`가 경고로 올립니다. 덮어쓰되 조용히 덮지 않습니다
- [x] ★ **`symbolUniverse`가 venue 목록을 받는다** — 노드 하나가 여러 venue를 훑고,
      유동성 컷은 venue별로 겁니다. 단수 `venue:` 표기도 계속 받습니다 (아래 참조)
- [x] **랭킹·백분위·컷을 시장별로 분리** (규칙 17) — 섞으면 분산 넓은 코인이 위를
      쓸어가 **모멘텀이 아니라 변동성으로 줄 세운 것**이 됩니다
- [x] **유니버스 조회 소스를 능력으로 고른다** — 라우팅 표의 앞 소스(pykrx·yfinance)가
      목록 조회를 구현하지 않아 **krx·nasdaq 유니버스가 아예 실패하고 있었습니다**
- [x] Fresh Bar Gate 혼합 파이프라인 검증 (코인+한국+미국 동시) — 실측 통과

#### 세 시장을 어떻게 한 번에 훑는가

**유니버스 노드는 하나입니다.** 시장마다 노드를 만들어 `marketData` 하나에 물리면
`Bundle.merge`가 context를 `dict.update`로 합치면서 **세 노드가 모두 쓰는
`context["universe"]` 키가 덮어써집니다** — items는 제대로 합쳐지지만 유니버스는
마지막 것만 남아 두 시장이 소리 없이 사라집니다. 노드가 하나면 이 문제가 애초에 없습니다.

```yaml
- id: universe
  type: symbolUniverse
  params:
    venues:                                              # 단수 `venue:`도 계속 받습니다
      - { venue: upbit,  quote_currency: KRW, top_by_turnover: 60 }
      - { venue: krx,    top_by_turnover: 200 }
      - { venue: nasdaq, limit: 100 }
```

**목록의 원소가 문자열이 아니라 조건 묶음인 이유**는 venue마다 필요한 컷이 다르기
때문입니다 — 코인은 KRW 마켓 제한이 필요하고 주식은 아니며, 미국 목록에는 거래대금이
아예 없습니다.

| | |
| :--- | :--- |
| **유동성 컷** | **venue별로 따로** 겁니다. 섞어 자르면 거래대금 단위가 달라(원 vs 달러) 비교가 성립하지 않습니다 |
| **랭킹·백분위·컷** | **시장별로** 나눕니다(규칙 17). 순위가 시장마다 1번부터 다시 시작하는 이유입니다 |
| **`top_by_turnover`** | 소스가 거래대금을 줘야 합니다. krx는 FDR이 `Amount`를 줍니다 |
| **`limit`** | 미국처럼 거래대금이 없는 venue의 대안. ⚠️ **소스가 준 순서**에 기댑니다 |

> ⚠️ **거래대금을 주지 않는 소스에 `top_by_turnover`를 걸면 노드가 거부합니다.**
> 경고만 남기고 빈 목록을 돌려주면 **그 시장이 통째로 사라진 채 실행이 성공**하기 때문입니다.

`--market crypto|krx|us`는 이 `venues` 목록도 함께 거릅니다 — 고정 목록만 걸러서는
필터가 있다는 사실이 오히려 오해를 만듭니다. 백테스트 차단(규칙 14)은 그대로입니다.

### Phase 3 — ★ 백테스트 & 상주 실행

**3a. `backtest` — 날짜별 리플레이 + 차트 ✅ 완료**

- [x] ★ **`bar_time` → 세션 날짜 변환기**(`session_date`) — `bar_time`은 세션 **마감** 시각이라
      그대로 날짜로 자르면 **코인이 하루 어긋납니다**(업비트 8/2 봉이 8/3로 찍힘). 규칙은
      `(bar_time − 1초)를 calendar.tz로 옮긴 날짜`. **봉과 마커가 같은 함수를 씁니다**
- [x] `lightweight-charts` vendoring (`app/report/vendor/` + LICENSE) — **CDN 금지** (§2.1)
- [x] **`marketscan backtest <종목> --start D`** — 그날까지의 봉만 잘라 전략을 다시 돌리고
      조건 충족일에 마커 (§12.7). 부작용 없음
- [x] **`compute` → `rank` → `select` 순서를 `run`과 공유**(`strategies/stages.py`) — 각자
      적으면 언젠가 한쪽만 바뀌고, 그날부터 백테스트가 실거래와 다른 코드를 돕니다
- [x] ★ **횡단면 컷 미적용 경고** — 종목 하나면 "상위 N개"의 후보가 1개라 항상 통과합니다.
      리포트 배너와 CLI 출력 양쪽에 붙박이. 순위·백분위는 표에서 제외
- [x] 종목별 `priceFormat` — 크기에서 자릿수를 유도 (코인이 전부 0으로 보이지 않게)

**3b. `serve` — 웹 UI + 스케줄·알림 ✅ 완료**

- [x] ★ **`app/service.py`** — 명령의 본체를 CLI에서 떼어냈습니다. **버튼과 터미널이 같은
      코드를 지납니다** (갈라지면 규칙 11·13이 화면 쪽으로 우회됩니다)
- [x] **`marketscan serve`** — FastAPI + Jinja 서버 렌더. 대시보드 · 실행 버튼 ·
      **리포트 뷰어**. Node·pnpm·빌드 단계 없음
- [x] 리포트 경로 가두기 · 실행 잠금(연타 방지) · 기본 바인딩 `127.0.0.1`
- [x] **스케줄 + 알림 + ⚠️ 하루 1회 하트비트**(신호 0건이어도). 없으면 프로세스가 죽은 것과
      신호가 없는 것이 구분되지 않습니다 (§8)
- [x] `scheduleTrigger` 노드 — 파이프라인이 실행 시각을 갖습니다. 서머타임은 날마다
      다시 계산해 넘깁니다
- [x] ★ **켤 때 지난 슬롯을 몰아서 부르지 않습니다** — "직전 판단과 지금 사이를 지났는가"로
      판정합니다. 아니면 저녁에 켠 순간 그날 슬롯이 전부 `--commit`으로 돕니다
- [x] 텔레그램 채널(환경변수) · 토큰 없으면 기록만

### Phase 3.5 — 사후 검증 (`review`를 없애고 남은 자리)

⚠️ **`review`(신호 이력 + 이후 경로)는 만들지 않기로 했습니다.** `backtest`가 그 자리를
대신하지 **않습니다** — 하나는 내가 고른 종목의 과거 재생이고, 다른 하나는 실제로 낸 신호의
사후 성적입니다. 후자가 **진짜 out-of-sample**인데 지금 그걸 보는 화면이 없습니다.
아래가 그 공백을 메우는 항목들입니다.

- [ ] **신호별 사후 수익률** (`Forward Return Evaluator`) — `as_of`로부터 1·5·20봉 뒤 종가를
      캐시에서 찾아 `signals`에 채웁니다. **캐시가 붙어서 외부 호출이 필요 없습니다**
- [ ] `stats`의 forward return · hit rate · IC (§4.8 신호 품질 지표)
- [ ] **수집 대상에 "과거 신호 종목" 추가** (규칙 18) — 유니버스에서 밀린 종목의 봉이 끊기면
      차트가 거기서 멈추고, **하필 밀린 종목이 대개 내린 종목이라** 화면이 낙관 편향됩니다
- [ ] **벤치마크 지수 수집** — KOSPI · S&P500 · BTC. §4.8의 "벤치마크 대비 초과수익"에
      필요한데 **지금은 하나도 안 모읍니다.** pykrx·FDR이 지수를 줍니다
- [ ] **엔진 검증 4종** — 난수 신호 · 전량 매수 · 신호 1일 밀기 · 상장폐지 포함.
      **난수 신호의 hit rate가 기저율을 넘으면 미래 참조가 있는 것입니다**

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
| `backtest <종목> --start D` | 없음 | ★ **그날 이 종목은 어땠나** — 날짜별 리플레이 + 차트 |
| `serve` | **알림 발송** | 스케줄 + 알림 + 하트비트. **알림이 나가는 유일한 명령** (Phase 3) |
| `ingest [--venue V] [--commit]` | **`--commit` 시에만** | 일봉 수집 → `ohlcv_cache`. **기본은 계획만** |
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
| `MARKETSCAN_CONFIG_DIR` | `~/.marketscan` |
| `MARKETSCAN_PIPELINE_PATH` | `config.yml` (설정 디렉터리 기준) |
| `MARKETSCAN_STRATEGIES_DIR` | (없음 — 파이프라인 파일과 같은 디렉터리) |
| `MARKETSCAN_DATABASE_URL` | `sqlite+aiosqlite:///./data/marketscan.db` (설정 디렉터리 기준) |
| `MARKETSCAN_REPORTS_DIR` | `reports` (설정 디렉터리 기준) |

**시세에는 자격 증명이 필요 없습니다** — 일봉 고정으로 무인증 소스만 씁니다
(PyKRX · yfinance · FDR · CCXT 공개 OHLCV). 남는 비밀은 텔레그램 토큰과 LLM 키뿐입니다.

---

## Development

```bash
uv sync              # 의존성 설치 (uv.lock 고정)
uv run pytest -q     # 테스트
uv run ruff check app tests
```

**알고 있는 의존성 부채 하나** — `pykrx`를 1.0.x에 묶어 두고 `setuptools<81`을 함께
답니다. pykrx 1.0.x가 임포트 시점에 `pkg_resources`를 부르기 때문입니다. 1.2.3부터
그게 없어졌지만 **같은 릴리스가 `pandas<3.0`을 요구해서**, 올리면 pandas가 메이저로
내려갑니다. 소스 하나 때문에 코어 라이브러리를 되돌릴 일은 아니라고 보고 미뤄 둔
상태입니다. **재검토 조건: pykrx가 pandas 3을 허용하면** 올리고 `setuptools` 핀과
`pykrx_source.py`의 경고 필터를 함께 지웁니다.

---

## 프로젝트 구조

```
~/.marketscan/                 ★ 사용자 자산 (백업 대상) — 저장소 바깥에 있다
├── config.yml                 기본 설정 (MARKETSCAN_PIPELINE_PATH로 바꾼다)
├── <전략>.py                  파일 하나 = 전략 하나. 해시가 실행에 박힌다
├── data/marketscan.db         ohlcv_cache · signals · 실행 이력 (--commit 실행에서만 생성)
└── reports/                   실행 리포트 HTML — latest.html + run_<id>.html

marketscan/
├── ARCHITECTURE.md            설계 문서 (단일 출처)
├── CLAUDE.md                  작업 규칙
├── sample/                    설정·전략 예제 한 벌. ~/.marketscan/으로 복사해 쓴다
│   ├── config.yml             ★ 기본 설정 — 추세추종 55일 돌파 (§6 스키마 그대로)
│   ├── demo.yaml              12-1 횡단면 모멘텀
│   └── *.py                   그 설정들이 부르는 전략 — **설정 파일 옆**에서 찾는다
└── app/
    ├── cli/                   Typer 명령 · 출력 규약 · 종료 코드
    ├── engine/                Bundle·Item 계약, RunContext, DAG 검증·실행, 신호 배출구
    ├── strategies/            Strategy 프로토콜 · 로더(SHA-256) · AST 인과성 검사
    ├── market/                InstrumentRef, MarketCalendar, 타임프레임 정책
    ├── providers/             시세 소스 + 라우팅·폴백 · 캐시 계층(ohlcv_source · universe_source)
    ├── ingest/                Ingestion Worker — 수집 대상 도출·수집 (§3.9)
    ├── nodes/                 배선용 노드 (트리거·입력·전략·로직·액션)
    ├── report/                자기완결 HTML — run_report · backtest_report · vendor/(차트)
    ├── web/                   ★ serve의 화면 — FastAPI 라우트 + Jinja 템플릿
    ├── backtest/              날짜별 리플레이
    ├── service.py             ★ 명령의 본체 — CLI도 웹도 여기를 지납니다
    ├── core/                  config · formatting(가격·시각 표기의 단일 출처)
    └── storage/               SQLAlchemy 모델 · 실행 이력 · 신호 · ohlcv_cache · instruments
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
