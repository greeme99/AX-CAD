# AX-CAD 세션 인계 (Hand-off)

> 2026-10-21 기준(V4.1 — 운영 방식 전환 + 보안 검토 반영). 새 세션은 이 문서 → `CLAUDE.md` → `.claude/memory/MEMORY.md` 순서로 읽고 시작한다.
> 진척 근거는 `docs/7` 테스트 보고서, 기능별 구현 결정은 `docs/2` 각 FN의 "구현 결정" 항목에 있다.

## 0. 요약

| 항목 | 상태 |
|---|---|
| 기준 커밋 | `main` = `b88dfcb` (Merge PR #22: Track 5·6) + PR #23(Track 7 운영 전환) |
| 작업 브랜치 | `main-23t1bb` — 머지 후 매번 `origin/main`에서 다시 시작 |
| 개발 진척 | S1~S12 + 견적 Revision + TC-82 E2E + CI + Track 2(품질) + **Track 3(사내 배포·운영·보안)** + **Track 4(외부 기준문서 대비 보완, plan.md §10)** + **Track 5(FreeCAD 기능 이식: 2D 스냅·편집 도구, 3D 가공 Feature, 색상·STL/BREP, plan.md §11)** + **Track 6(공사·설비 견적: 수량산출·철거/신설, 공종 단가·간접비, 지역·시점 보정·유효기한, 시나리오·민감도, 내역서·공사원가계산서, plan.md §12)** 완료 + **Track 7(운영 전환: PC별 Windows 설치·서명 패치, 로그인·결재·ERP 실연동 제거, plan.md §13)**. 후속은 plan.md §10.5 백로그 B-1~B-10(CR로 착수) |
| 테스트 | pytest 251(+성능 5는 `AXCAD_PERF=1`일 때만) · vitest 114 · Playwright E2E 1(TC-82, CI에서 compose 스택 대상) · CI `windows` 잡(설치·토큰·패치, TC-122) · 테스트 보고서 V1.35 **Pass 95/100 (95%)**(폐지 7건 제외) |
| CI | GitHub Actions `.github/workflows/ci.yml` — PR·main push마다 backend(Postgres 16 서비스 + ruff·mypy·pytest), frontend(typecheck·lint·test·build), deploy(compose 이미지 빌드·기동·보안 점검·**E2E TC-82**·백업/복구 훈련·자체 서명 HTTPS), **windows**(설치본 빌드·설치·기동·패치, Windows PowerShell 5.1) |
| 배포 | **PC별 Windows 설치본**(`installer/windows`, 사용법 `docs/PC_설치_가이드.md`), 릴리스는 Actions **Windows release**(서명 키 필요). `deploy/` docker compose는 개발·CI용으로 유지 |
| 결함 | 개발 결함 BUG-01~47 모두 Fixed, 미해결 0. 성능 TC-97은 실도면 측정 후 목표 재설정(§4) |
| 마이그레이션 | 0001~0016 (아래 §3 표) |
| 사내 서버 | **보류**(Track 7): 서버 없이 PC별 설치로 전환. 이전 확정 사항(Linux·Docker·자체 서명 HTTPS)은 `deploy/README.md`에 보존 |
| 리허설 | **완료**(2026-10-11, 사용자 Windows PC: Docker Desktop+WSL2, RAM 8GB): 설치·HTTPS(Chrome·Edge 신뢰)·업로드·뷰어·백업 통과, 실도면 12개 측정(2D 4개뿐 → 목표 미확정) |
| 막힌 것 | **패치 서명 키 생성**(사용자 PC, §4), 2D 제작 도면 ≥10개 성능 측정, G1 기준정보·샘플 도면, 실제 공급자 정보, UAT — §4 참조 |

### 머지 이력

| PR | 내용 |
|---|---|
| #1 | S6 프론트 + S7 STEP/IGES (R2 마감) |
| #2 | S8 2D 메트릭·기준정보·G1 엑셀 가져오기 |
| #3 | S9 원가 엔진·견적 API·추적성 |
| #4 | S10 견적 UI·수동 조정·검증 |
| #5 | S11-a 견적 승인 + 첫 인계 문서 |
| #6 | S11-b 견적서 PDF·Excel |
| #7 | S11-b 보안 반영 + S12 BOM·ERP |
| #8 | 견적 Revision + 보안 반영 |
| #9 | TC-82 Playwright E2E |
| #10 | 인계 문서 V2.0 |
| #11 | GitHub Actions CI |
| #12 | Track 2: TC-07·16·24, 성능 측정, 감사 로그 범위·0014, UAT 가이드, 접근성 |
| #13 | MEMORY ADR-07/08, 압축 |
| #14 | NFR-02 실도면 측정 테스트·목표 재설정 절차 |
| #15 | Track 3: 배포 패키지·운영 기본기·CI E2E·DB 역할 분리·CSP |
| #16 | TC-100 모바일 안내 + 인계 문서 V3.0 |
| #17 | 사내 서버 확정: 자체 서명 HTTPS·PC 신뢰 등록(TC-106, BUG-40·41), 메모리 반영 |
| #18 | 사내 검증 가이드(서버 → 성능 → G1 → ERP → UAT) |
| #19 | Windows 리허설 반영, 3D를 DXF로 저장한 파일 보기 전용 표시(결정 10, TC-107, BUG-42) |
| #20 | 메모리: 개발 단계 서버 = 집의 개인 PC(Windows 10 + Docker Desktop + WSL2), 회사 전환 시 사내 Linux 서버로 이전 |
| #21 | Track 4: 외부 기준문서(SYNOPEX) 대비 plan §10, 견적 검증 도면 품질 경고, 내보내기 자체 검증, Undo 정리, 오픈소스 고지(BUG-43) |
| #22 | Track 5 FreeCAD 기능 이식(BUG-44) + Track 6 공사·설비 견적·시나리오·내역서(BUG-45) |

## 1. 환경 기동 (클라우드 컨테이너)

컨테이너가 재시작되면 **PostgreSQL과 dev 서버가 모두 꺼진다.** 아래 순서로 복구한다. 접속 정보 값은 문서·로그에 쓰지 않는다.

```bash
service postgresql start
uv sync && (cd frontend && pnpm install)
# 환경변수(값은 repo-root .env 또는 세션 env 파일, 커밋 금지)
#   필수  DATABASE_URL, TEST_DATABASE_URL(개발 DB와 분리), JWT_SECRET
#   선택  AXCAD_VAR_DIR
#   견적서 AXCAD_SUPPLIER_FILE  실제 공급자 정보 파일(저장소 밖 권장). 없으면 샘플 → 정식 견적서 409
#   ERP    ERP_API_URL(https, 개발만 localhost http), ERP_API_TOKEN, ERP_TIMEOUT_S(≤30), ERP_BACKOFF_S
uv run alembic upgrade head              # 개발 DB
uv run uvicorn backend.api.main:app --port 8000      # --reload 없음: 백엔드 코드 바꾸면 재시작
pnpm --dir frontend dev                  # :3000 (/api → :8000 프록시)
```

- `backend/db/session.py`가 repo-root `.env`를 읽는다(기존 환경변수 우선, 값 출력 안 함).
- 테스트는 `TEST_DATABASE_URL`만 쓴다(없으면 DB 테스트 skip, 개발 DB로 절대 대체 안 함). conftest가 세션 시작 때 base까지 downgrade 후 upgrade한다.
- 사내 서버 설치·운영(배포 패키지): `deploy/README.md`. 로컬 개발은 위 명령 그대로(앱도 DB 소유자 계정으로 접속).
- 로컬에서 배포 스택 시험: `dockerd`를 백그라운드로 띄우고, 베이스 이미지는 `mirror.gcr.io/library/*`, 빌드는 `--network=host` + 프록시 build-arg + `--secret id=ca,src=/root/.ccr/ca-bundle.crt`. 샌드박스는 Debian 저장소가 막혀 api 이미지는 Ubuntu 대체 베이스(`sandbox-python:local`)로만 빌드된다 — 기본(Debian) 빌드 검증은 CI `deploy` 잡. env는 `COMPOSE_ENV_FILES=<scratchpad env>`로 넘긴다(`deploy/.env`는 쓰기 금지 규칙).
- `tests/test_db_roles.py`는 DB 사용자에게 역할 생성 권한이 있어야 돈다(CI는 superuser). 이 샌드박스는 `ALTER ROLE axcad CREATEROLE` 해 둠.
- E2E: `frontend/e2e/README.md` (API를 `AXCAD_SUPPLIER_FILE=frontend/e2e/fixtures/supplier.json`으로 띄우고 `E2E_ADMIN_LOGIN`/`E2E_ADMIN_PASSWORD_FILE` 지정 후 `pnpm --dir frontend e2e`).

### 개발 DB 상태 (전부 테스트 데이터)

| 대상 | 내용 |
|---|---|
| 사용자 | `e2e`(ADMIN), `e2e-reviewer`(REVIEWER), E2E 실행마다 생긴 `est-*`/`rev-*` |
| 기준정보 | ACTIVE `G1-2027`, `G1-2027B` — **가짜 단가**. G1 실데이터가 오면 새 버전으로 가져와 활성화 |
| 견적 | Q-…-000001·000002(초안), 000003(SUPERSEDED) → 000003-R1(확정), 000006·000007(E2E 확정) |
| BOM·ERP | 도면 ASM-01(문서 8, 승인됨)의 BOM 1건, ERP 작업 1건 SUCCESS(모의 ERP 대상) |
| 비밀번호 | 세션 scratchpad 파일에만 있음(새 세션에서는 관리자 API로 재발급) |

## 2. 작업 규칙 (이 프로젝트에서 굳어진 것)

| 규칙 | 이유 |
|---|---|
| PR은 CI(GitHub Actions) 녹색이어야 머지. 로컬에서도 같은 게이트를 먼저 돌린다 — `set -o pipefail` 후: ruff check → ruff format --check → mypy core backend → pytest / pnpm typecheck → lint → test → build (+ 필요 시 `pnpm e2e`) | `\| tail`이 실패를 가린 적 있음 |
| dev 서버가 떠 있으면 `pnpm build` 금지, 빌드 전 서버 종료 + `.next` 삭제 | `.next` 충돌 |
| 프론트 의존성을 바꾸면 dev 서버 재시작 + `.next` 삭제 | `@playwright/test` 추가 후 Next 모듈 경로가 바뀌어 빈 화면 |
| DB 테스트 스위트 2개 동시 실행 금지 | 같은 테스트 DB를 truncate/downgrade |
| 서버 종료는 `ps`로 PID 찾아 kill (`pkill -f` 금지) | 자기 셸까지 종료됨 |
| DROP/TRUNCATE 문자열이 들어가는 파일·명령은 Edit/Write 도구로 | 위험 명령 훅이 bash 문자열을 차단 |
| 머지 전 마이그레이션은 제자리 수정 가능(개발 DB는 해당 리비전 아래로 내렸다가 다시 올림), **머지된 마이그레이션은 새 리비전으로만** 변경 | 0009→0010 사례 |
| 증거 테이블(승인 이력, 발행 견적서, Revision)이 있으면 downgrade는 거부하도록 작성 | 감사 증적 보존 |
| PR 작업은 `gh api` REST: `pulls`(생성·본문), `pulls/{n}/ccr/ready_for_review`, `pulls/{n}/merge`(merge_method=merge, sha 지정) | GitHub MCP가 "invalid session" 반환 |
| 머지 후 작업 브랜치는 `git fetch origin main && git merge --ff-only origin/main` 후 일반 push | force push는 위험 명령 훅이 차단 |
| 배포(`deploy/`)·CI를 바꾸면 로컬 compose로 기동·E2E·백업/복구를 돌려 보고, 기본 빌드는 CI `deploy` 잡 녹색으로 확인 | 샌드박스와 실제 베이스 이미지가 다름 |
| CI 셸 검사는 한 줄 한 검사(`A && B` 금지), deploy 잡은 `shell: bash`(pipefail) | `bash -e`가 `&&` 앞쪽 실패를 놓침(BUG-39) |
| 기능 단위 커밋 + `Co-Authored-By`/`Claude-Session` 트레일러, 매 작업 `docs/7` 결과·`docs/2` 구현 결정·`docs/README` 변경 이력 갱신 | DoD |
| 업로드·인증·승인·금액·외부 연동 변경은 `security-reviewer`(백그라운드) → 지적 반영 커밋 → PR 본문에 반영 표 | `.claude/rules/security.md`, 지금까지 매번 실제 결함을 찾음 |
| G1 작성본(단가·임률)·실제 공급자 정보는 저장소에 커밋하지 않음 | 사내 기밀 |

## 3. 시스템 구조 요약

### 마이그레이션

| 리비전 | 내용 |
|---|---|
| 0001~0004 | 사용자·프로젝트·문서·리비전·도면 승인·감사 로그, 3D Feature(Extrude/Revolve/Boolean/IMPORT) |
| 0005 | 기준정보(버전·재질·단가·공정 규칙·원가 비율·매핑 규칙), ACTIVE 후 동결 |
| 0006 | 견적 헤더·라인·trace·검증 로그, 계산값 불변 트리거, override CHECK |
| 0007·0008 | 확정/검토 중 견적 override 변경 거부, `quote_approvals`(PENDING 1건 유일) |
| 0009·0010 | 견적서 발행 대장 `quote_reports`(정식본 바이트·sha256 저장, 형식별 1회 발행) |
| 0011 | BOM 헤더·품목(수량 불변, 품번 규칙 CHECK, 헤더 불변) |
| 0012 | ERP `integration_jobs`(멱등 키, run_id 소유권, 전송 완료 동결) |
| 0013 | 견적 Revision(체인·번호·사유), 헤더 가드(원천·금액·계보 불변, 상태 전이 제한) |
| 0014 | 감사 로그: 변경 불가 대용량 컬럼은 값이 같으면 UPDATE 이미지에서 생략(ERP `request_payload`) |
| 0015 | `features.feature_type`에 가공 Feature(FILLET·CHAMFER·HOLE·PATTERN·MIRROR·SHELL) 추가 |
| 0016 | 공종 단가 `work_items`·보정계수 `adjust_factors`(동결·감사), 재질·단가 `valid_until`, 간접노무비율·간접공사비율, 매핑 `QTO_LAYER`, 견적 원천 `CONSTRUCTION`(리비전 필수) |

### 견적 흐름과 규칙

- **산출**: 메트릭(2D `core/quote_engine/metrics2d.py`, 3D `_bodies`) → `compute_quote()`(`cost.py`) → 라인·trace·로그 → `validate_quote()`(`validate.py`). 금액은 전부 `Decimal`/`numeric`, 공식은 AST 화이트리스트(`formula.py`).
- **추적성**: 라인당 trace 1개(원천 리비전 + 엔티티 핸들/3D Feature), 화면 "도면에서 보기"로 하이라이트.
- **조정(FN-19)**: `calculated_*` 불변, `override_*` + 사유·작성자·시각. DRAFT에서만 가능.
- **승인(FN-22)**: **Track 7에서 폐지**(도면·견적 모두). 견적은 늘 DRAFT.
- **견적서(FN-21)**: 언제나 현재 값으로 출력(내부용 산출근거 선택), 공급자 정보가 샘플이면 "샘플 공급자" 워터마크(Track 7).
- **Revision(FN-19)**: Track 7에서 폐지. 다시 계산은 "재산출"(새 견적).
- **공사 견적(Track 6)**: 리비전 → `compute_qto()`(`qto.py`, 레이어 → 실·벽·문… 역할, 기존 리비전과 형상 키 비교로 new/demo/keep) → `compute_construction()`(`construction.py`, 공종 수량식 → 재료·노무·경비 → 간접노무비·간접공사비) → `summarize(construction=True)`(이윤 = 노무비+경비+일반관리비 기준). 보정계수·시나리오는 `Quote` 생성자에서 두 견적 유형 공통 적용, 시나리오 비교는 저장 없이 엔진 재실행(`_recompute`).
- **검증 코드**: PRICE_MISSING, QTY_INVALID, MATERIAL_REQUIRED, TOTAL_MISMATCH, AMOUNT_OVERFLOW(ERROR) / OVERRIDE_LARGE(±30%), DUPLICATE_LINE, OVERHEAD_STALE, SOURCE_OUTDATED, MASTER_OUTDATED(WARN) + 엔진 ERROR 승계.

### BOM·ERP

- **BOM(FN-23)**: 도면 블록 INSERT/MINSERT 집계(중첩 곱, 메모이즈 + 연산 예산), 품번은 ATTRIB에서 읽고 없으면 미매핑 → 수동 지정(AUTO 품번·전송된 BOM은 수정 불가), CSV/JSON. 3D는 STEP 부품 인스턴스 수.
- **ERP(FN-24)**: Track 7에서 실연동 폐지 → BOM "ERP 업로드 파일(Excel)"(`export?format=erp`, 현재 도면 BOM·미매핑 0).

### 화면

`/projects` · `/documents/[id]`(리비전·견적 목록·BOM 링크) · `/viewer/[rev]`(2D, 견적 메트릭·공사 견적) · `/model/[doc]`(3D) · `/quotes/[id]`(라인·요약·시나리오·검증·출력) · `/documents/[id]/bom`(ERP 업로드 파일) · `/admin/master-data`(G1 가져오기) · `/admin/users`(서버 모드만) · `/admin/audit`

## 4. 남은 작업 — 외부 입력별

Track 7 이후 설치·업데이트는 [`docs/PC_설치_가이드.md`](PC_설치_가이드.md)가 기준이다. [`docs/사내_검증_가이드.md`](사내_검증_가이드.md)의 서버 설치·ERP 절차는 보류, 성능·G1·UAT 절차는 PC 설치본에서 그대로 쓴다.

| 입력이 오면 | 할 일 | TC / 산출물 |
|---|---|---|
| **G1 엑셀 작성본** (1~6 시트) | 기준정보 화면에서 새 버전 생성 → G1 가져오기 → 확인 → 활성화. 가짜 단가 버전(`G1-2027*`)은 새 버전 활성화 뒤 사용 중지 | FN-16 시드 |
| **G1 7 시트 + 샘플 도면** (DXF ≥15, STEP ≥5) | 5·6 시트 레이어/표제란 규칙 반영 → 샘플별 기대 메트릭 회귀 테스트 `tests/test_quote.py` | TC-60, TC-64 (S8-d) |
| **G1 8 시트** (수기 견적 3건) | 같은 도면으로 견적 산출 → 원 단위 비교, 차이는 규칙·단가·절사 원인 분석 | TC-67 (R3 수락) |
| **블록명→품번 매핑 결정** | G1 시트 추가 시 `mapping_rules`에 BOM 매핑 유형 추가, `core/bom/dxf.py`에서 사용 | FN-23 |
| **실제 공급자 정보** | 저장소 밖 JSON(키는 `backend/config/supplier.json`과 동일, `_sample` 없음, 사업자번호 `123-45-67890` 형식이며 `000-00-00000`은 거부) → `AXCAD_SUPPLIER_FILE` 지정 | 정식 견적서 |
| **패치 서명 키**(최초 1회) | 사용자 PC에서 `uv run python installer\patcher.py keygen %USERPROFILE%\axcad-patch.key` → 출력된 공개키를 `installer/patch_pub.hex`로 커밋(공개키는 채팅으로 받아도 됨), 개인키 파일 내용은 GitHub Secret `AXCAD_PATCH_KEY`(사용자가 직접 등록) → Actions **Windows release** 실행 | TC-122, 첫 설치본 |
| **ERP 업로드 양식** | 회사 ERP의 BOM 일괄 등록 양식(열 이름·순서)을 받으면 `routes_bom.py::ERP_COLS`만 맞춘다 | FN-24 |
| **UAT** | PC 설치본으로 설치 → 사용자가 `docs/UAT_가이드.md` §7 시나리오 수행(결재·ERP 전송 항목 제외) | R4 릴리스 판정 |
| **실도면 DXF**(2D 제작 도면 ≥10개, 5MB 이상 포함) | 사용자가 **설치한 PC**에서 측정(`docs/사내_검증_가이드.md` §2, 3D를 DXF로 저장한 파일은 제외) 후 `NFR-02 real` 줄만 전달 → p95×1.5로 NFR-02 업로드 목표 제안·확정 → SRS·TC-97 갱신 (`docs/7` §2.5). 리허설 값(PC, 참고용): p95 3.70초 | TC-97 |

## 5. 미결 결정 (사용자 / G1 회의)

1. 셋업 시간: 개당 vs 로트당
2. 절사 규칙·단위·적용 시점(라인별 / 합계 1회) 실값
3. 제조간접비 배부 기준(기계시간 × 기계경비 vs 노무비 비율)
4. 재료 중량 기준(순면적 vs BBox) — 현재 기본 순면적
5. 역할별 단가 열람 범위 — 현재 프로젝트 구성원(ESTIMATOR·REVIEWER·MANUFACTURING)이면 견적 단가 열람 가능
6. 공급자 정보·견적 유효기간·결제조건·견적서 양식(회사 양식이 있으면 반영)
7. ~~ERP 연동 방식~~ → **결정(2026-10-21): 실연동 없음, 업로드 파일.** 블록명→품번 기준정보 시트 추가 여부는 미결
8. 수동 품번 지정에 2인 확인(직무 분리)을 둘지
9. ~~NFR-02 업로드 목표~~ → **결정(2026-10-10): 실도면 측정 후 재설정.** 확정 목표를 실도면이 넘을 때만 비동기 업로드 재검토
10. ~~3D를 DXF로 저장한 파일~~ → **결정(2026-10-11): 안내 + 보기 전용 표시(2안).** 3DFACE·3D POLYLINE·폴리페이스/메시·HELIX는 평면 투영 선으로 표시(편집·견적 제외), 3DSOLID 등 솔리드는 "STEP으로 올려 달라" 안내

## 6. 보안 검토에서 보고만 하고 남긴 항목

| 항목 | 현재 상태 | 처리 방향 |
|---|---|---|
| ~~단일 DB 역할~~ | **배포 패키지에서 해소**(Track 3-4): API는 `axcad_app`(DML만), 소유자는 migrate·백업만. 로컬 개발·테스트는 여전히 소유자 | — |
| 토큰 sessionStorage | 서버 모드 전용. PC 모드는 설치 토큰을 localStorage에 둔다(XSS면 읽힘). PC 모드 웹은 프레임 금지·nosniff만 있고 nginx의 전체 CSP는 없다 | PC 모드: `next.config` headers로 CSP 이식 검토 |
| PC 모드 같은 사용자 접근 | 설치 토큰(BUG-46)으로 다른 계정·사이트는 막힘. **같은 Windows 계정**의 다른 프로그램은 설정 파일을 읽을 수 있어 막을 수 없음(같은 계정 악성코드는 범위 밖) | 공용 계정·공용 PC 설치 금지 안내 |
| PC 설치본 PostgreSQL 미고정(Track 7) | `build.ps1`이 러너 이미지의 PostgreSQL을 복사 — 버전·OpenSSL DLL이 이미지에 따라 바뀜(Node는 해시 고정). 이 세션은 EDB 다운로드가 막혀 해시를 못 구함 | EDB 바이너리 zip을 SHA-256 고정으로 받기 |
| 첫 설치본 무서명(Track 7) | `SHA256SUMS.txt`가 설치본과 같은 경로로 전달돼 손상만 검출. 설치본의 공개키는 전달 경로를 믿는 셈 | 공개키 지문·설치본 해시를 별도 경로(사내 공지)로 공유, 또는 Authenticode 서명 |
| PC 모드 DB 단일 역할(Track 7) | PC 설치본 API가 DB 소유자 `axcad`로 접속(서버 배포의 `axcad_app` 최소 권한 미사용) | 설치 시 `backend.cli app-role`로 앱 역할 생성 후 API는 그 역할로 |
| 견적서 발행 기록 없음(Track 7) | 결재 제거로 견적서를 매번 새로 만들고 저장하지 않음 → 고객에게 보낸 판본 기록이 없음, 출력 후에도 조정 가능 | 필요 시 출력본을 `quote_reports`에 저장(발행 대장) |
| 서버 모드 초안 내보내기(Track 7) | 결재 제거로 서버 모드에서도 VIEWER 포함 구성원이 DXF·STEP 내보내기 가능(PC 모드는 관리자 1명) | 서버 재도입 시 `need(user, …)` 검토 |
| 설치 스크립트 소소한 항목(Track 7) | 바로가기 `-ExecutionPolicy Bypass`, `powershell.exe` 등 일부 이름 호출, 사용자 지정 `-Dest`의 폴더 권한, 포트 선점 프로그램이 상태 확인에 응답 가능, API `/docs` 노출(127.0.0.1), `cli local-user`가 기존 `local` 계정 상태 미확인, USB 백업 암호화 없음 | 같은 계정 범위·사용자 안내(BitLocker To Go)로 수용 |
| 감사 귀속 위조 | 앱 역할이 `app.user_id` 설정·`audit_logs` INSERT 가능 → 앱이 침해되면 작성자 위조 가능(앱 코드 신뢰 모델로 수용) | `fn_audit_log` SECURITY DEFINER + 앱의 `audit_logs` INSERT 회수 |
| COPY 개정본의 원천이 구 리비전 | `SOURCE_OUTDATED`는 WARN 유지 | 정책 결정 시 ERROR로 |
| 조정 취소 사유 없음 | 공통 규칙 유지 | — |
| Undo 무변경 비교 비용(Track 4) | 커밋마다 엔티티 목록을 비교(같은 객체는 즉시 통과, 바뀐 것만 형상 JSON 비교) | 초대형 도면에서 느려지면 변경 핸들만 비교 |
| 수량산출 캐시 증가(Track 6) | (리비전, 기존 리비전) 쌍마다 캐시 파일·워커 실행. 1회 비용은 실 이름 예산·슬롯으로 묶였고 리비전은 구성원 DESIGNER만 만듦 | 커지면 리비전별 측정 캐시 + 비교는 부모 프로세스에서 |
| 공사 견적 정식본의 원가 내역(Track 6) | 정식본에도 공사원가계산서·내역서(일반관리비·이윤·간접비·단가)가 붙음 — 국내 공사 내역서 관행 | **사용자 확인 필요**: "정식본에 내부 원가 없음" 원칙과 다름. 숨기려면 정식본에서 별첨 A·B 제외 |

의도적 단순화는 코드의 `ponytail:` 주석 43곳에 있다(`/ponytail-debt`로 목록화 가능).

## 7. 알려진 함정

- OCCT 8: `_s` 접미사 불일치, IGES 정적 파라미터는 `IGESControl_Controller.Init_s()` 이후에만 유효, IGES `ReadStream` 실패 → 저장 파일 `ReadFile` (MEMORY.md ADR-06).
- `ezdxf.new()` 기본 단위는 **m**(`$INSUNITS=6`) → 테스트 도면에서 mm를 의도하면 `units=ezdxf.units.MM`. 안 그러면 1000배 큰 도형으로 파싱됨(성능 측정에서 겪음).
- vitest는 `@/` 별칭·JSX 미설정 → 테스트 대상 로직은 `src/lib/*.ts`, 상대 경로 import. E2E 파일은 `*.e2e.ts`(vitest가 집어가지 않게).
- Playwright `browser.newContext()`는 config의 `baseURL`을 물려받지 않음 → 직접 넘김. `import.meta` 쓰면 ESM 취급되어 로더 오류 → `__dirname`.
- SQLAlchemy: 이미 로드된 행은 `with_for_update()`로 다시 읽지 않음 → 잠금 조회는 `populate_existing`(`_locked_quote`).
- 견적 헤더 상태는 트리거가 전이를 제한한다 — 테스트에서 SQL로 확정 상태를 만들 땐 DRAFT → IN_REVIEW → CONFIRMED 두 단계.
- 마이그레이션 downgrade는 `IF EXISTS`(구버전 DB 대비). conftest는 downgrade 전에 features·quote_headers를 비운다.
- API는 금액을 문자열로 반환 → 비교 전 `Decimal(str(x))`.
- `str.replace` 일괄 치환이 같은 문장을 가진 다른 모델/함수까지 바꾼 적 있음 → 치환 후 diff 확인.
- API는 모든 POST에 `Content-Length`를 요구 → 본문 없는 POST는 `curl -d ""`(아니면 411).
- 기준정보 "현재 버전"은 **오늘 적용 중인** ACTIVE 버전 → 테스트용 버전의 `effective_from`은 오늘 날짜.
- PostgreSQL 16: CREATEROLE 사용자는 `NOSUPERUSER` 같은 속성을 명시조차 못 함(기본값이 이미 안전) → 생성 후 속성을 조회해 검증.
- nginx: location에 `add_header`가 하나라도 있으면 server 레벨 헤더가 사라짐 → 공통 헤더는 `headers.inc`를 location마다 include. upstream은 `resolver` + 변수로(재생성된 컨테이너 IP 추적).
- 샌드박스 네트워크: Docker Hub 429 → `mirror.gcr.io`, ghcr 블롭·Debian 저장소 차단, 프록시는 127.0.0.1(빌드 시 `--network=host`).
