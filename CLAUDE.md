# CLAUDE.md

이 저장소에서 작업할 때 지켜야 할 규칙입니다.

## 프로젝트

**tradeflow** — 노드 기반 비주얼 파이프라인으로 암호화폐·한국주식·미국주식의 매매 신호를
만드는 개인용 Self-hosted 시스템. n8n/ComfyUI 같은 캔버스에서 노드를 조합하면 백엔드
DAG 엔진이 실행한다.

**`ARCHITECTURE.md`가 설계의 단일 출처다.** 구조를 바꾸는 작업 전에 반드시 읽고,
설계를 바꿨다면 그 문서도 함께 갱신한다.

현재 **Phase 0 완료**. 시세는 `synthetic` 더미 소스만 있고 실제 어댑터는 Phase 2다.

## 명령

```bash
# 백엔드 (backend/)
uv sync
uv run pytest -q
uv run uvicorn app.main:app --reload

# 프론트엔드 (frontend/)
pnpm install
pnpm dev
pnpm typecheck          # tsc --noEmit (TypeScript 7 네이티브 컴파일러)

# 전체
docker compose up --build
```

## 환경 제약 (2026-08 기준)

- **Node는 22 LTS 이상이 필요하다.** pnpm 11이 `>=22.13`, Vite 8이 `>=22.12`를 요구한다.
  (개발 PC는 v24.18.1로 확인됨.)
- **`corepack enable pnpm`을 인자 없이 쓰면 실패한다.** `C:\Program Files\nodejs`에 심을
  만들려다 EPERM이 난다. 이 PC는 아래 방식으로 설치되어 있다 (관리자 권한 불필요):

  ```powershell
  corepack enable --install-directory "$env:APPDATA\npm" pnpm
  ```

  이 폴더는 이미 PATH에 있다. 재설치가 필요하면 위 명령을 그대로 쓴다.
- **`package.json`의 스크립트에서 `pnpm`을 셸아웃하지 않는다.** 심이 없는 환경에서 깨진다.
  `build`는 `tsc --noEmit && vite build`처럼 바이너리를 직접 호출한다.
- **ESLint는 의도적으로 도입하지 않았다.** `typescript-eslint@8`의 peer가
  `typescript >=4.8.4 <6.1.0`이라 TypeScript 7과 충돌한다. TS 7 지원이 나오면 추가한다.
  그때까지 정적 검사는 `pnpm typecheck`가 담당한다.
- `pnpm-lock.yaml`을 커밋한 뒤 `frontend/Dockerfile`의 `pnpm install`을
  `pnpm install --frozen-lockfile`로 바꿀 것.

## 절대 깨면 안 되는 규칙

이것들은 어기면 조용히 잘못된 신호가 나가거나 백테스트가 거짓말을 한다.

1. **노드 안에서 `datetime.now()`를 호출하지 않는다.** 모든 시각은 `ctx.now`에서 온다.
   이 규칙 하나가 백테스트와 실거래를 같은 코드 경로로 묶는다.
2. **Provider는 `end` 이후 캔들을 반환하지 않는다.** 반환 직전에
   `assert_no_future(df, end, self.id)`를 호출한다. 미래 참조는 폴백으로 감추지 말고 그대로 터뜨린다.
3. **필터 노드는 `ohlcv`를 보존한 채 `items`만 걸러낸다.** DataFrame을 버리면 뒤에
   필터를 이어붙일 수 없다. 판단 근거는 `features`/`tags`에 남긴다.
4. **모든 시각은 tz-aware UTC로 저장한다.** 표시할 때만 `settings.user_timezone`으로 변환한다.
   naive datetime은 `_as_utc()`가 거부한다.
   DB 컬럼은 `DateTime(timezone=True)`가 아니라 **`UtcDateTime`**을 쓴다 — SQLite는
   tzinfo를 저장하지 않아서, 그냥 두면 naive로 새어 나오고 프론트의 `new Date()`가
   그걸 로컬 시각으로 오해해 표시가 통째로 어긋난다.
5. **미국 시장에 고정 오프셋(UTC-5)을 쓰지 않는다.** 반드시 `ZoneInfo("America/New_York")`.
   서머타임 때문에 한국 기준 개장 시각이 1시간 움직인다.
6. **API 키를 DAG JSON에 넣지 않는다.** 노드는 Connection ID만 참조한다.
   파이프라인을 export해도 키가 새지 않아야 한다.
7. **`adjusted`(수정주가)는 캐시 키에 포함한다.** 조정가/비조정가가 섞이면 지표가 조용히
   틀어지고 원인 추적이 불가능해진다.
8. **실주문 코드를 추가하지 않는다.** Phase 5 전까지 `BrokerProvider` 구현체를 만들지 않는다.
   현재 범위는 신호 알림이다.
9. **파이프라인 저장은 덮어쓰지 않는다.** 항상 `pipeline_versions`에 새 버전을 추가한다.
   과거 스냅샷을 수정하면 "그때 그 신호가 어떤 그래프에서 나왔는지"를 잃는다.

## API 규칙

- **고정 경로를 경로 파라미터보다 먼저 선언한다.** FastAPI는 선언 순서대로 매칭하므로,
  `/pipelines/{pipeline_id}`가 `/pipelines/validate`보다 위에 있으면 `validate`가
  id로 잡힌다. `app/api/routes.py`의 섹션 주석을 지킬 것.
- **저장은 검증을 통과하지 못해도 허용한다.** 작업 중인 그래프를 저장하지 못하면 쓸 수 없다.
  막는 곳은 `/pipelines/run`이다.

### 현재 엔드포인트

```
GET    /api/health
GET    /api/nodes                      노드 카탈로그 (프론트 폼의 단일 출처)
POST   /api/pipelines/validate
POST   /api/pipelines/run
GET    /api/pipelines                  저장 목록
POST   /api/pipelines                  저장 (id 없으면 신규, 있으면 새 버전)
GET    /api/pipelines/{id}[?version=N] 불러오기 (기본은 활성 버전)
GET    /api/pipelines/{id}/versions
DELETE /api/pipelines/{id}
```

## 프론트엔드 구조

```
App.tsx                 셸 — TopAppBar + 화면 전환
components/TopAppBar    대메뉴: 파이프라인 · 알림 채널 · 연결
components/PipelineToolbar  이름 · 저장/열기/새로 · 모드 · 실행
pages/PipelinePage      캔버스 + 팔레트 + 인스펙터
pages/PlaceholderPage   미구현 화면 공통 껍데기
```

- **파이프라인 화면은 언마운트하지 않는다** (`hidden` 클래스로 감춘다). 메뉴를 옮겼다고
  편집 중인 캔버스가 날아가면 안 된다.
- 라우터 라이브러리를 쓰지 않는다. 화면이 3개뿐이라 상태 전환으로 충분하다.
  상세 페이지(`/pipelines/:id`)가 생기면 그때 react-router를 넣는다.
- dirty 판정은 `snapshot(toPayload(...))` 문자열 비교다. 선택 상태 같은 UI 전용 값은
  `toPayload`에 없으므로 노드를 클릭하는 것만으로는 dirty가 되지 않는다.

## 새 노드를 추가할 때

1. `app/nodes/<카테고리>/<이름>.py`에 `BaseNode` 하위 클래스를 만들고 `@register`를 붙인다.
2. **`app/nodes/__init__.py`에 임포트를 추가한다.** 아직 자동 탐색이 없어서 빠뜨리면
   레지스트리에 등록되지 않는다.
3. 파라미터는 Pydantic 모델로 선언한다. `Field(description=...)`을 채우면 그대로 UI에
   설명으로 나온다 — **프론트엔드 폼은 이 JSON Schema에서 자동 생성되므로 프론트를
   고칠 필요가 없다.**
4. 상류 데이터 없이도 돌아야 하는 소스 노드는 `requires_input = False`로 둔다.
   `inputs`는 `(MAIN,)`으로 두어야 트리거 뒤에 배치할 수 있다.
5. `backend/tests/`에 테스트를 추가한다. 네트워크 없이 `synthetic` 소스로 돌 것.

## 코드 관례

- **주석은 "왜"를 적는다.** 무엇을 하는지는 코드가 말한다. 특히 위 8개 규칙과 관련된
  방어 코드에는 어떤 사고를 막는지 남긴다.
- 오류 메시지는 한국어로, **다음에 뭘 해야 하는지까지** 적는다.
  예: `"instrument는 'venue:symbol' 형식이어야 합니다. 예: 'upbit:KRW-BTC'"`
- 심볼은 항상 `InstrumentRef`로 다룬다. 맨 문자열 `"005930"`을 그대로 넘기지 않는다.
- 조용한 절삭(top-N, 샘플링)을 하면 반드시 `ctx.log.warning`으로 남긴다.
  "전부 처리했다"는 오해가 실사용에서 가장 위험하다.
- 새 의존성은 꼭 필요할 때만. 특히 프론트엔드는 TS 7 호환을 먼저 확인한다.

## 자주 하는 실수

| 증상 | 원인 |
| :--- | :--- |
| 노드가 `skipped`로 끝남 | 상류가 모두 skip/error거나, `requires_input=True`인데 연결된 엣지가 없음 |
| `알 수 없는 노드 type` | `app/nodes/__init__.py`에 임포트를 안 넣음 |
| 두 번째 실행에서 결과가 0건 | 정상이다. Fresh Bar Gate가 같은 봉을 stale로 제외한 것 |
| 백테스트가 422로 거부됨 | 일봉 미만 타임프레임. 의도된 게이트다 (`shadow` 모드를 쓸 것) |
