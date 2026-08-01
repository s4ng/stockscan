# tradeflow

노드를 드래그해 조합하는 **비주얼 파이프라인**으로 암호화폐·한국주식·미국주식의 매매 신호를
만들고 알림으로 받는 개인용 Self-hosted 시스템입니다.

n8n이나 ComfyUI 같은 캔버스에서 `시세 수집 → 지표 필터 → AI 판단 → 알림` 노드를 이어 붙이면
백엔드 DAG 엔진이 실행합니다. 코인(24시간)과 주식(장 운영시간·휴장일·서머타임)을 **한 파이프라인에
섞어도** 시장별 캘린더가 알아서 처리합니다.

- 설계 문서: **[ARCHITECTURE.md](./ARCHITECTURE.md)** — 모든 설계 결정의 근거
- 작업 규칙: **[CLAUDE.md](./CLAUDE.md)**

> **현재 상태: Phase 0 완료.** 타입 계약, DAG 실행 엔진, 노드 5종, 캔버스 UI가 동작합니다.
> 시세는 `synthetic` 더미 소스를 쓰며 실제 소스 어댑터(PyKRX·yfinance·KIS·Alpaca)는 Phase 2입니다.
> 실주문은 범위 밖입니다 — 현재는 **신호 알림 전용**입니다.

---

## Tech Stack

| 영역 | 스택 |
| :--- | :--- |
| Frontend | React 19, `@xyflow/react` 12, Zustand 5, TailwindCSS 4, Vite 8, **TypeScript 7** (Go 네이티브 컴파일러) |
| Backend | Python 3.12, FastAPI, Pydantic v2, NetworkX, pandas, **uv** |
| 배포 | Docker Compose (backend + nginx) |

---

## Development

### 사전 요구사항

| 도구 | 버전 | 확인 |
| :--- | :--- | :--- |
| **Node.js** | **22 LTS 이상** | `node -v` — pnpm 11이 `>=22.13`, Vite 8이 `>=22.12`를 요구 |
| **pnpm** | 11.x | `corepack pnpm --version` |
| **uv** | 최신 | `uv --version` |
| Docker Desktop | — | 전체 스택 실행 시에만 |

> **Windows에서 pnpm 준비하기**
>
> 그냥 `corepack enable pnpm`을 쓰면 심(shim)을 `C:\Program Files\nodejs`에 만들려다
> 권한 오류(EPERM)로 실패합니다. **PATH에 이미 있고 쓰기 가능한 npm 전역 폴더**에 설치하세요.
>
> ```powershell
> corepack enable --install-directory "$env:APPDATA\npm" pnpm
> ```
>
> 설치 후 **새 터미널을 열면** `pnpm`이 잡힙니다. corepack이 `package.json`의
> `packageManager` 핀(11.18.0)을 계속 관리합니다.
> 되돌리려면 `corepack disable --install-directory "$env:APPDATA\npm" pnpm`.

### 백엔드

```bash
cd backend

uv sync                                    # 의존성 설치 (uv.lock 고정)
uv run uvicorn app.main:app --reload       # 개발 서버 → http://localhost:8000
uv run pytest -q                           # 테스트 19개
```

- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/api/health

### 프론트엔드

```bash
cd frontend

pnpm install        # 의존성 설치
pnpm dev            # 개발 서버 → http://localhost:5173
pnpm typecheck      # tsc --noEmit (TypeScript 7)
pnpm build          # 타입검사 + 프로덕션 빌드 → dist/
pnpm preview        # 빌드 결과 미리보기
```

개발 서버는 `/api` 요청을 `localhost:8000`으로 프록시하므로 CORS 설정이 필요 없습니다.
백엔드 주소를 바꾸려면 `VITE_API_TARGET` 환경변수를 쓰세요.

### 전체 스택 (Docker)

```bash
cp .env.example .env       # TRADEFLOW_MASTER_KEY 채우기
docker compose up --build
```

| 서비스 | 주소 |
| :--- | :--- |
| 웹 UI | http://localhost:5173 |
| API | http://localhost:8000 |

두 포트 모두 `127.0.0.1`에만 바인딩됩니다.
⚠️ **거래소·증권사 API 키가 저장되는 시스템입니다. 인증 없이 인터넷에 노출하지 마세요.**

---

## 화면 구성

상단 대메뉴로 세 화면을 오갑니다.

| 메뉴 | 상태 | 내용 |
| :--- | :--- | :--- |
| **파이프라인** | 동작 | 노드 캔버스 편집·저장·실행 |
| **알림 채널** | Phase 1 예정 | 텔레그램·슬랙·디스코드 등 신호 수신처 |
| **연결** | Phase 2 예정 | 증권사·거래소·AI API 키와 소스 라우팅 |

## 동작 확인해보기

1. **파이프라인** 화면에서 좌하단 **[예제 파이프라인 불러오기]** 클릭
2. **[실행]** — 코인·한국주식·미국주식을 한 번에 수집하고 이동평균 조건으로 걸러 알림을 출력합니다
3. **[실행 기록]** 탭에서 노드별 입·출력과 로그 확인
4. 이름을 바꾸고 **[저장]**(`Ctrl+S`) → **[열기]**에서 다시 불러오기.
   저장할 때마다 새 버전이 쌓이며 **과거 버전은 수정되지 않습니다**
5. 모드를 `backtest`로 바꾸고 Market Data 노드의 timeframe을 `1h`로 두면
   **일봉 게이트**가 거부하는 것을 볼 수 있습니다

---

## 프로젝트 구조

```
tradeflow/
├── ARCHITECTURE.md            설계 문서 (단일 출처)
├── CLAUDE.md                  작업 규칙
├── docker-compose.yml
├── backend/
│   └── app/
│       ├── engine/            Bundle·Item 계약, RunContext, DAG 검증·실행
│       ├── market/            InstrumentRef, MarketCalendar, 타임프레임
│       ├── providers/         시세 소스 플러그인 + 라우팅·폴백
│       ├── nodes/             트리거·입력·지표·로직·액션
│       └── api/               FastAPI 라우터
└── frontend/
    └── src/
        ├── canvas/            React Flow 커스텀 노드
        ├── panels/            팔레트·파라미터 폼·실행 기록
        └── store/             Zustand
```

---

## 설계에서 눈여겨볼 것

| | |
| :--- | :--- |
| **Fresh Bar Gate** | 미국장이 닫힌 시간에도 코인은 계속 판정되고 주식은 조용히 제외됩니다. 사용자가 캘린더를 신경 쓸 필요가 없습니다 |
| **소스는 노드가 아니다** | 데이터 소스는 Connection + 라우팅 표로 관리합니다. 파이프라인이 특정 증권사에 묶이지 않고, 소스가 죽으면 폴백합니다 |
| **`ctx.now` 주입** | 노드는 `datetime.now()`를 쓰지 않습니다. 백테스트와 실거래가 같은 코드 경로를 씁니다 |
| **백테스트는 일봉 이상만** | 분봉 과거 이력 확보 비용을 회피합니다. 분봉 전략은 `shadow` 모드로 정방향 검증합니다 |
| **폼 자동 생성** | 파라미터 폼은 백엔드 JSON Schema에서 생성됩니다. 노드를 추가해도 프론트엔드를 고치지 않습니다 |
