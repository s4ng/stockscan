<div align="center">

# stockscan

한국·미국 주식을 매일 훑어서 매수 후보를 뽑고 텔레그램으로 보내 주는 프로그램

<!-- 스크린샷이 옛 이름(marketscan)이라 잠시 내려 둠. 새로 찍어서 갈아 끼울 것
<p>
  <img src="docs/images/alerts.jpg" alt="하트비트와 일일 신호 알림" width="30%">
  <img src="docs/images/scorecard.jpg" alt="월간 성적표" width="30%">
</p>
-->

</div>

---

```
📈 trend_breakout_55 — 신호 1건 [15:40]

· 삼성전자 (krx:005930)  307,000 (+2.68%)
   krx 1위/195 · trend_strength=8.204 · 손절 276,813 (-9.8%)
   stockscan explain 42

이 전략 최근 20건: 승률 55% · 20봉 중앙값 +1.8% (벤치마크 대비 -0.4%)

          [ ✅ 샀다 ]   [ ⬜ 안 샀다 ]
```

| 명령 | 설명 |
| :--- | :--- |
| `serve` | 평소엔 이것만 띄워 둠. 스케줄·알림·성적표를 알아서 돌림 |
| `run` | 오늘의 후보를 뽑음 |
| `evaluate` | 신호의 사후 수익률을 채움 |
| `scorecard` | 성적표. 승률, 기저율, 벤치마크 대비, 산 것과 무시한 것 비교 |
| `backtest` | 한 종목을 하루씩 되감아 조건 충족일을 차트에 찍음 |

- 설계문서: [ARCHITECTURE.md](./ARCHITECTURE.md)
- 작업규칙: [CLAUDE.md](./CLAUDE.md)

## 사용법

```bash
uv sync
mkdir -p ~/.stockscan && cp sample/* ~/.stockscan/   # 설정과 전략은 한 벌
uv run stockscan describe

# 어디서든 `stockscan ...`으로 호출하려면
uv tool install .                                    # stockscan 명령을 PATH에 올림
stockscan describe
```

설정 파일 `~/.stockscan/config.yml`

```yaml
timezone: Asia/Seoul

universe:                # venue별로 몇 종목까지
  krx: 200
  nasdaq: 100

strategy: trend_breakout_55

schedule:
  at: ["15:40", "06:10"]
  heartbeat: "09:00"     # 신호 0건이어도 하루 한 번
  scorecard_day: 1       # 매월 성적표

telegram:
  token: "<봇 토큰>"
  chat_id: "<채팅 ID>"
```

```bash
stockscan run                  # 후보 뽑기. 기본은 dry-run이라 아무것도 안 남음
stockscan run --commit         # signals 기록 + 봉 소비
stockscan ingest --commit      # 일봉·지수 수집
stockscan evaluate             # 사후 수익률 채우기
stockscan scorecard --send     # 성적표를 텔레그램으로
stockscan explain 1            # 이 신호가 왜 떴는지
stockscan backtest krx:005930 --start 20251201
stockscan serve                # 상주
```

알아 둘 것 몇 가지.

- 알림은 `serve`에서만 나감. 손으로 `run`을 돌린다고 텔레그램이 오지는 않음.
- `explain`, `signals`, `stats`, `describe`는 읽기만 함. DB 파일도 안 만듦.
- 종료 코드는 0(성공, 신호 0건 포함) / 2(소스 실패) / 3(검증 실패).
- 모든 명령에 `--help`와 `--json`이 있음.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check app tests
```

> [!NOTE]
> `pykrx`가 1.0.x에, `setuptools`가 81 미만에 묶여 있음.
>
> pykrx 1.0.x는 임포트할 때 `pkg_resources`를 부르는데 setuptools 81에서 그게 빠졌음.
> 그래서 setuptools를 못 올림. `pykrx_source.py`의 경고 필터도 같은 이유로 있음.
>
> pkg_resources를 버린 1.2.3부터는 `pandas<3.0`을 요구함. 최신 1.2.8도 마찬가지.
> 지금 pandas 3을 쓰고 있어서 pykrx를 올리면 pandas가 메이저로 내려감. 소스 하나
> 때문에 그럴 일은 아니라고 보고 미뤄 둔 상태임.
>
> pykrx가 pandas 3을 받아 주면 그때 올리고, setuptools 핀과 경고 필터를 같이 지우면 됨.

## 프로젝트 구조

설정, 전략, DB, 리포트는 전부 `~/.stockscan/`에 저장.

```
~/.stockscan/
├── config.yml                 설정
├── <전략>.py                  파일 하나에 전략 하나. 해시가 실행 기록에 박힘
├── data/stockscan.db          ohlcv_cache · signals · 실행 이력
└── reports/                   실행·백테스트 리포트 HTML
```

```
app/
├── config.py       설정. 사람이 적는 건 전부 여기, 나머지는 유도하거나 상수
├── pipeline.py     유니버스 → 봉 → 전략 → 기록 → 로그
├── evaluate.py     사후 수익률. 캐시만 읽음
├── scorecard.py    성적표
├── benchmark.py    KOSPI, S&P500
├── service.py      명령의 본체. CLI도 스케줄러도 여기를 지남
├── serve.py        상주 루프. 발화·알림·하트비트·응답 수거·성적표
├── schedule.py     다음 발화가 언제인지 계산만 함
├── alerts.py       텔레그램. 나가는 것과 [샀다/안 샀다] 응답
├── cli/            Typer 명령, 출력 규약, 종료 코드
├── engine/         Bundle·Item 계약, RunContext, 신호 배출구, 봉 상태
├── strategies/     Strategy 프로토콜, 로더(SHA-256), AST 인과성 검사
├── market/         InstrumentRef, MarketCalendar, 타임프레임 정책
├── providers/      시세 소스, 라우팅·폴백, 캐시 계층
├── ingest/         수집 대상 도출과 수집
├── report/         자기완결 HTML과 vendoring한 차트
├── backtest/       날짜별 리플레이
├── core/           설정 경로, 가격·시각 표기
└── storage/        SQLAlchemy 모델, 실행 이력, 신호, ohlcv_cache
```
