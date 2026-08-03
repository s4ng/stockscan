# marketscan

정해둔 전략으로 암호화폐·한국주식·미국주식을 훑어 **볼 만한 후보를 리스트업**하는 개인용 CLI입니다.

| 명령 | 하는 일 |
| :--- | :--- |
| **`run`** | 오늘의 후보를 뽑습니다. `--commit`이면 기록에 남습니다 |
| **`review`** | 남긴 신호를 모아 **"그래서 어떻게 됐나"를 차트로** 봅니다 |
| **`serve`** | 위를 스케줄로 돌리고 알림을 보냅니다 |

**예측 기계가 아니라 주의력 기계입니다.** 시장을 맞히는 것이 아니라, 혼자서는 볼 수 없는 범위를
대신 보고 정해둔 규칙을 대신 지키는 것이 목적입니다. 최종 판단은 사람이 합니다.

★ **`review`가 이 도구를 정직하게 유지합니다.** 리뷰 없는 스크리너는 자신감 기계가 됩니다 —
사람은 맞은 종목만 기억하니까요. 오른 것과 내린 것을 **같이 보게 강제하는 화면**이 "후보"라는
말의 무게를 지킵니다.

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
> **★ 세 시장을 한 번에 훑습니다.** `pipelines/demo.yaml` 하나로 코인·한국·미국이
> 동시에 돕니다. 실측(2026-08-03 기준):
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
> ⚠️ **`review`와 `serve`는 아직 없습니다** — Phase 3입니다.

v0.5 전환이 끝났습니다. 아래가 v0.4에서 바뀐 것들입니다.

| | v0.4 | **v0.5 (현재)** |
| :--- | :--- | :--- |
| 인터페이스 | FastAPI + React 캔버스 | **CLI (Typer) + 정적 HTML** |
| 전략 표현 | 지표 노드 조합 | **파이썬 클래스 1개** |
| 판단 단위 | 백테스트 일봉 / 알림 분봉 | **일봉·주봉 전용** |
| 전략의 축 | 시계열 필터 체인 | **횡단면 랭킹** |
| 배포 | Docker Compose | **`uv` + `[project.scripts]`** |
| 정의 형식 | DAG JSON | **YAML** (§11-4에서 확정 — 스키마는 그대로) |
| 자동 실행 | APScheduler | **`serve`로 확정** (§11-4b) — 구현은 Phase 3 |
| 결과 확인 | 실행 로그뿐 | **`review` — 신호 이력 + 차트** (§12.7) |

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

**`--commit`이 없으면 판단이 남지 않습니다** — `signals` 미기록, 봉 미소비. 몇 번을 돌려도
안전하므로 전략을 고치면서 마음껏 돌려 보세요.

> **단, 받아 온 봉은 `data/marketscan.db`의 `ohlcv_cache`에 쌓입니다.** 이건 판단이 아니라
> 자료라서 dry-run에도 열려 있습니다 — 버리면 무료 API를 두 번 두드리게 됩니다(§3.9).
> 덕분에 두 번째 실행부터는 대부분 외부 호출 없이 돕니다.

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
> **DB(`data/marketscan.db`)가 생기고 봉이 쌓입니다.** 대신 `signals`는 비어 있고
> `bar_state` 테이블은 생기지도 않습니다.

같은 봉을 두 소스가 다르게 주면 수집이 **그 자리에서 경고**합니다(§3.8).
덮어쓰되 조용히 덮지 않습니다 — 수정주가 정책 차이로 생긴 계열 불연속은
사후에 찾을 방법이 이것뿐입니다.

### 10. 그래서 어떻게 됐나 — `review` ★

> ⚠️ **Phase 3입니다. 아직 없습니다.**

```bash
marketscan review --since 2026-01-01 --strategy cross_momentum_12_1
# → reports/review_2026-01-01_2026-08-03.html
```

쌓인 신호를 전부 늘어놓고, 각 종목이 **신호 이후 어떻게 움직였는지**를 차트로 보여 줍니다.
`explain`이 신호 하나의 *근거*를 답한다면 `review`는 신호 전체의 *결과*를 답합니다.

| | |
| :--- | :--- |
| **왜 필요한가** | 리뷰가 없으면 맞은 종목만 기억하게 됩니다. 오른 것과 내린 것을 같이 봐야 "후보"라는 말이 정직해집니다 |
| **왜 백테스트보다 나은가** | 그때 **실제로 내보낸** 신호를 나중에 보는 것이라 **진짜 out-of-sample**입니다. 백테스트는 아무리 잘 만들어도 과거를 되감는 일이라 의심할 것이 남습니다 |
| **무엇이 실리나** | `--commit` 실행의 신호만. dry-run은 아무것도 남기지 않으므로 실험이 리뷰를 오염시키지 않습니다 |

**정적 HTML 한 장입니다. 서버가 아닙니다.** 읽기 전용이고 필터는 CLI 인자로 받으므로 서버가
낄 자리가 없습니다. 차트 라이브러리(`lightweight-charts`)는 **저장소에 vendoring해서 인라인**합니다 —
CDN을 걸면 반년 뒤 그 화면이 깨지는데, 반년 전 신호를 보는 것이 이 화면의 목적입니다.

> ⚠️ **`review`는 수익률 보고서가 아닙니다.** 여기 나오는 것은 **신호별 사후 수익률**이고,
> "이걸 따랐으면 내 계좌가 어땠나"는 청산 규칙이 있어야 답할 수 있습니다(§1.3). 섞어 읽으면
> 없는 성과를 본 것이 됩니다.

### 11. 자동으로 돌린다 — `serve`

> ⚠️ **Phase 3입니다. 방식은 정해졌고 구현이 남았습니다.**

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

\* `ingest`는 Phase 0.5에서 명령 표면만 있었고, 실제 수집(`ohlcv_cache` · Ingestion Worker)은
Phase 2에서 채워졌습니다.

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

### Phase 3 — ★ 리뷰 & 상주 실행

**이 프로젝트의 정체성이 완성되는 단계입니다.** `run`만 있으면 신호를 내보고 잊습니다.
`review`가 있어야 "그래서 어떻게 됐나"가 남고, 그게 있어야 스크리너가 자신감 기계가 되지
않습니다(§1.1).

**3a. 데이터 — 리뷰가 읽을 것을 먼저 채웁니다**

- [ ] **신호별 사후 수익률** (`Forward Return Evaluator`) — `as_of`로부터 1·5·20봉 뒤 종가를
      캐시에서 찾아 `signals`에 채웁니다. **캐시가 붙어서 외부 호출이 필요 없습니다**
- [ ] **수집 대상에 "과거 신호 종목" 추가** (규칙 18) — 유니버스에서 밀린 종목의 봉이 끊기면
      리뷰 차트가 거기서 멈추고, **하필 밀린 종목이 대개 내린 종목이라** 화면이 낙관 편향됩니다
- [ ] **벤치마크 지수 수집** — KOSPI · S&P500 · BTC. §4.8의 "벤치마크 대비 초과수익"에
      필요한데 **지금은 하나도 안 모읍니다.** pykrx·FDR이 지수를 줍니다

**3b. `review` — 정적 HTML + 차트**

- [ ] ★ **`bar_time` → 세션 날짜 변환기** — `bar_time`은 세션 **마감** 시각이라 그대로 날짜로
      자르면 **코인이 하루 어긋납니다**(업비트 8/2 봉이 8/3로 찍힘). 규칙은
      `(bar_time − 1초)를 calendar.tz로 옮긴 날짜`. **봉과 마커가 같은 함수를 써야** 마커가
      제 봉 위에 앉습니다
- [ ] `lightweight-charts` vendoring (`app/report/vendor/` + LICENSE) — **CDN 금지** (§2.1)
- [ ] **`marketscan review`** — 기간·전략·시장으로 걸러 신호 이력 + 이후 주가 경로를
      정적 HTML로. 신호 시점에 마커 (§12.7)
- [ ] 차트를 신호의 `meta["adjusted"]`와 **같은 키로** 조회 — 다르면 마커가 엉뚱한 가격에 뜹니다
- [ ] 종목별 `priceFormat` — KRW 주식은 정수 원, 코인은 소수점 8자리. 기본값이면 코인이
      전부 `0.00`으로 찌그러집니다
- [ ] `stats`의 forward return · hit rate · IC (§4.8 신호 품질 지표)

**3c. `serve` — 상주 실행**

- [ ] **`marketscan serve`** — 스케줄 + 알림 + ⚠️ **하루 1회 하트비트**(신호 0건이어도).
      없으면 프로세스가 죽은 것과 신호가 없는 것이 구분되지 않습니다 (§8)
- [ ] `Schedule Trigger` 노드 · `Telegram Alert` 노드 (`sends_external_messages = True`)

### Phase 3.5 — 백테스트

**리뷰 다음입니다.** `review`가 진짜 out-of-sample이라 더 정직하고, 백테스트는 아무리 잘
만들어도 과거를 되감는 일이라 의심할 것이 남습니다. 여기서의 용도는 **"내 구현이 안 틀렸나"**
하나뿐입니다.

- [ ] 캘린더 기반 리플레이 + look-ahead assert
- [ ] **피처 행렬 사전 계산** — 유일한 실제 엔지니어링 (§4.8)
- [ ] **엔진 검증 4종** — 난수 신호 · 전량 매수 · 신호 1일 밀기 · 상장폐지 포함
- [ ] `backtest_runs` 다중검정 카운터

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
| `review [--since D]` | 없음 | ★ **그래서 어떻게 됐나** — 신호 이력 + 차트 (Phase 3) |
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
    ├── providers/             시세 소스 플러그인 + 라우팅·폴백 · 캐시 계층(ohlcv_source)
    ├── ingest/                Ingestion Worker — 수집 대상 도출·수집 (§3.9)
    ├── nodes/                 배선용 노드 (트리거·입력·전략·로직·액션)
    ├── report/                자기완결 HTML — run_report · review_report(P3) · vendor/
    └── storage/               SQLAlchemy 모델 · 실행 이력 · 신호 · ohlcv_cache
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
