# marketscan

**정해둔 전략으로 한국·미국 주식을 매일 훑어 매수 후보를 뽑고, 텔레그램으로
보내 주는 프로그램입니다.**

```
📈 trend_breakout_55 — 신호 1건 [15:40]

· 성호전자 (krx:043260)  15,870 (+6.22%)
   krx 1위/195 · trend_strength=3.409 · 손절 14,210 (-10.5%)
   marketscan explain 42

이 전략 최근 20건: 승률 55% · 20봉 중앙값 +1.8% (벤치마크 대비 -0.4%)

          [ ✅ 샀다 ]   [ ⬜ 안 샀다 ]
```

혼자서는 볼 수 없는 범위를 대신 보고, 정해둔 규칙을 대신 지킵니다.
**최종 판단은 사람이 합니다.**

**알림에 성적이 붙어 나가는 것이 이 프로그램의 특징입니다.** 스크리너는 내버려 두면
자신감 기계가 됩니다 — 사람은 맞은 종목만 기억하니까요. 그래서 낸 신호를 전부
기록하고 사후 수익률을 채워서, 알림을 받는 순간 **"이걸 얼마나 믿어야 하나"**가
같이 오게 했습니다. 한 달에 한 번은 성적표도 옵니다.

| 명령 | 하는 일 |
| :--- | :--- |
| **`serve`** | ★ **평소에는 이것만 띄워 둡니다** — 스케줄·알림·성적표 |
| `run` | 오늘의 후보를 뽑습니다. `--commit`이면 기록에 남습니다 |
| `evaluate` | 신호의 사후 수익률을 채웁니다 (캐시만 읽습니다) |
| `scorecard` | 성적표 — 승률·기저율·초과수익·오버라이드 |
| `backtest` | 한 종목을 하루씩 되감아 조건 충족일을 차트에 찍습니다 |

- 설계와 그 근거: **[ARCHITECTURE.md](./ARCHITECTURE.md)** — 로드맵·CLI 규약·환경변수 포함
- 작업 규칙: **[CLAUDE.md](./CLAUDE.md)**

---

## 사용법

```bash
uv sync
mkdir -p ~/.marketscan && cp sample/* ~/.marketscan/   # 설정과 전략은 한 벌입니다
uv run marketscan describe                             # 설치 확인
```

설정은 `~/.marketscan/config.yml` 하나이고, **사람이 정하는 것은 넷뿐입니다.**

```yaml
timezone: Asia/Seoul

universe:                # venue별로 몇 종목까지
  krx: 200
  nasdaq: 100

strategy: trend_breakout_55

schedule:
  at: ["15:40", "06:10"]
  heartbeat: "09:00"     # 신호 0건이어도 하루 1회
  scorecard_day: 1       # 매월 성적표

telegram:
  token: "<봇 토큰>"
  chat_id: "<채팅 ID>"
```

`lookback`·전략 파라미터·컷 방식은 **전략과 소스에서 유도**합니다. 적을 자리가 없는
것이 의도입니다 — 설정에서 슬쩍 바꿔 돌려 보는 경로를 없앤 것입니다.

```bash
marketscan run                  # 후보 뽑기. 기본은 dry-run(아무것도 남지 않습니다)
marketscan run --commit         # signals 기록 + 봉 소비
marketscan ingest --commit      # 일봉·지수 수집 → ohlcv_cache
marketscan evaluate             # 사후 수익률 채우기
marketscan scorecard --send     # ★ 성적표 (텔레그램으로도)
marketscan explain 1            # 이 신호가 왜 떴는가
marketscan backtest krx:005930 --start 20251201
marketscan serve                # 상주 — 스케줄·알림·하트비트·성적표
```

**평소에는 `serve` 하나만 띄워 두면 됩니다.** 나머지는 그것이 알아서 부릅니다.

- 모든 명령에 `--help`와 `--json`이 있습니다.
- 읽기 전용 명령(`explain`·`signals`·`stats`·`describe`)은 **DB 파일조차 만들지 않습니다.**
- 종료 코드: `0` 성공(신호 0건 포함) / `2` 소스 실패 / `3` 검증 실패.

> ⚠️ **알림이 나가는 것은 `serve`뿐입니다.** 손으로 `run`을 돌릴 때는 채널로 아무것도
> 나가지 않습니다 — 손으로 돌릴 때마다 메시지가 오면 알림 자체를 믿지 않게 됩니다.

---

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check app tests
```

**알고 있는 의존성 부채 하나** — `pykrx`를 1.0.x에 묶고 `setuptools<81`을 함께 답니다.
pykrx 1.0.x가 임포트 시점에 `pkg_resources`를 부르기 때문입니다. 1.2.3부터 그게
없어졌지만 **같은 릴리스가 `pandas<3.0`을 요구해서** 올리면 pandas가 메이저로
내려갑니다. **재검토 조건: pykrx가 pandas 3을 허용하면** 올리고 `setuptools` 핀과
`pykrx_source.py`의 경고 필터를 함께 지웁니다.

---

## 프로젝트 구조

```
~/.marketscan/                 ★ 사용자 자산 (백업 대상) — 저장소 바깥에 있다
├── config.yml                 설정 (사람이 정하는 것 넷)
├── <전략>.py                  파일 하나 = 전략 하나. 해시가 실행에 박힌다
├── data/marketscan.db         ohlcv_cache · signals · 실행 이력
└── reports/                   실행·백테스트 리포트 HTML

marketscan/
├── ARCHITECTURE.md            설계 문서 (단일 출처)
├── CLAUDE.md                  작업 규칙
├── sample/                    설정·전략 예제 한 벌
└── app/
    ├── config.py              ★ 설정 — 사람이 적는 것 전부. 나머지는 유도하거나 상수
    ├── pipeline.py            ★ 유니버스 → 봉 → 전략 → 기록 → 로그 (함수 하나)
    ├── evaluate.py            사후 수익률 — 캐시만 읽는다
    ├── scorecard.py           ★ 성적표 = 제품
    ├── benchmark.py           KOSPI · S&P500
    ├── service.py             명령의 본체 — CLI도 스케줄러도 여기를 지난다
    ├── serve.py               상주 루프 — 발화·알림·하트비트·ack·성적표
    ├── schedule.py            "다음 발화가 언제인가" (계산만)
    ├── alerts.py              텔레그램 — 나가는 것과 [샀다/안 샀다] 응답
    ├── cli/                   Typer 명령 · 출력 규약 · 종료 코드
    ├── engine/                Bundle·Item 계약 · RunContext · 신호 배출구 · 봉 상태
    ├── strategies/            Strategy 프로토콜 · 로더(SHA-256) · AST 인과성 검사
    ├── market/                InstrumentRef · MarketCalendar · 타임프레임 정책
    ├── providers/             시세 소스 + 라우팅·폴백 · 캐시 계층
    ├── ingest/                수집 대상 도출·수집
    ├── report/                자기완결 HTML + vendoring한 차트
    ├── backtest/              날짜별 리플레이
    ├── core/                  설정 경로 · 가격·시각 표기의 단일 출처
    └── storage/               SQLAlchemy 모델 · 실행 이력 · 신호 · ohlcv_cache
```
