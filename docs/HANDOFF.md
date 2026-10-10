# AX-CAD 세션 인계 (Hand-off)

> 2026-10-10 기준(V2.1). 새 세션은 이 문서 → `CLAUDE.md` → `.claude/memory/MEMORY.md` 순서로 읽고 시작한다.
> 진척 근거는 `docs/7` 테스트 보고서, 기능별 구현 결정은 `docs/2` 각 FN의 "구현 결정" 항목에 있다.

## 0. 요약

| 항목 | 상태 |
|---|---|
| 기준 커밋 | `main` = `c79d1a7` (Merge PR #10) |
| 작업 브랜치 | `main-23t1bb` — 머지 후 매번 `origin/main`에서 다시 시작 |
| 개발 진척 | S1~S12 + 견적 Revision + TC-82 E2E 완료. **G1 없이 가능한 개발은 끝남** |
| 테스트 | pytest 188 · vitest 77 · Playwright E2E 1(TC-82) · 테스트 보고서 V1.18 **Pass 75/82 (91%)** |
| CI | GitHub Actions `.github/workflows/ci.yml` — PR·main push마다 backend(Postgres 16 서비스 + ruff·mypy·pytest)와 frontend(typecheck·lint·test·build). E2E는 제외(실행 중인 스택·ACTIVE 기준정보 필요) |
| 결함 | 개발 결함 BUG-01~27 모두 Fixed, 미해결 0 |
| 마이그레이션 | 0001~0013 (아래 §3 표) |
| 막힌 것 | G1 기준정보, 실제 공급자 정보, ERP 사양, UAT — §4 참조 |

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

### 견적 흐름과 규칙

- **산출**: 메트릭(2D `core/quote_engine/metrics2d.py`, 3D `_bodies`) → `compute_quote()`(`cost.py`) → 라인·trace·로그 → `validate_quote()`(`validate.py`). 금액은 전부 `Decimal`/`numeric`, 공식은 AST 화이트리스트(`formula.py`).
- **추적성**: 라인당 trace 1개(원천 리비전 + 엔티티 핸들/3D Feature), 화면 "도면에서 보기"로 하이라이트.
- **조정(FN-19)**: `calculated_*` 불변, `override_*` + 사유·작성자·시각. DRAFT에서만 가능.
- **승인(FN-22)**: DRAFT → IN_REVIEW(라인 동결) → CONFIRMED | 반려 시 DRAFT. 검증 ERROR가 있으면 요청 불가. 승인자는 Revision 체인 전체의 작성자·조정자가 아닌 활성 REVIEWER 구성원.
- **견적서(FN-21)**: 초안은 언제나(워터마크, 내부용 산출근거 선택). 정식은 확정 후, 산출근거 첨부 불가, 첫 발행본 저장 후 같은 바이트 재제공. 공급자 정보가 샘플이면 409.
- **Revision(FN-19)**: 확정된 최신 견적만 개정. COPY(확정 금액·조정 복사) / RECALC(현재 도면·기준정보로 재산출). `원번호-R{n}`, 승인 시 이전 Rev SUPERSEDED(발행본은 계속 제공), 초안 Revision은 폐기 가능(번호 재사용 안 함).
- **검증 코드**: PRICE_MISSING, QTY_INVALID, MATERIAL_REQUIRED, TOTAL_MISMATCH, AMOUNT_OVERFLOW(ERROR) / OVERRIDE_LARGE(±30%), DUPLICATE_LINE, OVERHEAD_STALE, SOURCE_OUTDATED, MASTER_OUTDATED(WARN) + 엔진 ERROR 승계.

### BOM·ERP

- **BOM(FN-23)**: 도면 블록 INSERT/MINSERT 집계(중첩 곱, 메모이즈 + 연산 예산), 품번은 ATTRIB에서 읽고 없으면 미매핑 → 수동 지정(AUTO 품번·전송된 BOM은 수정 불가), CSV/JSON. 3D는 STEP 부품 인스턴스 수.
- **ERP(FN-24)**: 승인 도면 + 현재 리비전 BOM + 미매핑 0일 때만 전송. 멱등 키 `BOM:{문서}:{리비전}`, 백그라운드 재시도(최대 3회, 4xx 즉시 실패, 리다이렉트 미추종), 토큰은 환경변수에만, ERP 응답 본문은 ADMIN만.

### 화면

`/projects` · `/documents/[id]`(리비전·승인·견적 목록·BOM 링크) · `/viewer/[rev]`(2D, 견적 메트릭) · `/model/[doc]`(3D) · `/quotes/[id]`(라인·요약·검증·승인·출력·개정) · `/documents/[id]/bom` · `/approvals`(도면+견적) · `/admin/master-data`(G1 가져오기) · `/admin/integrations`(ERP 작업) · `/admin/users` · `/admin/audit`

## 4. 남은 작업 — 외부 입력별

| 입력이 오면 | 할 일 | TC / 산출물 |
|---|---|---|
| **G1 엑셀 작성본** (1~6 시트) | 기준정보 화면에서 새 버전 생성 → G1 가져오기 → 확인 → 활성화. 가짜 단가 버전(`G1-2027*`)은 새 버전 활성화 뒤 사용 중지 | FN-16 시드 |
| **G1 7 시트 + 샘플 도면** (DXF ≥15, STEP ≥5) | 5·6 시트 레이어/표제란 규칙 반영 → 샘플별 기대 메트릭 회귀 테스트 `tests/test_quote.py` | TC-60, TC-64 (S8-d) |
| **G1 8 시트** (수기 견적 3건) | 같은 도면으로 견적 산출 → 원 단위 비교, 차이는 규칙·단가·절사 원인 분석 | TC-67 (R3 수락) |
| **블록명→품번 매핑 결정** | G1 시트 추가 시 `mapping_rules`에 BOM 매핑 유형 추가, `core/bom/dxf.py`에서 사용 | FN-23 |
| **실제 공급자 정보** | 저장소 밖 JSON(키는 `backend/config/supplier.json`과 동일, `_sample` 없음, 사업자번호 `123-45-67890` 형식이며 `000-00-00000`은 거부) → `AXCAD_SUPPLIER_FILE` 지정 | 정식 견적서 |
| **ERP 사양** | 엔드포인트·인증·필드 매핑 확정 → `backend/api/routes_erp.py::_payload` 매핑, 스테이징 ERP로 TC-93~96 재확인 | FN-24 |
| **UAT** | 사용자가 테스트 보고서 §7 U-01~08 수행, 결과 기록 | R4 릴리스 판정 |
| (선택) | 테스트 보고서 미수행 TC-07·16·24 점검 | 테스트 보고서 |

## 5. 미결 결정 (사용자 / G1 회의)

1. 셋업 시간: 개당 vs 로트당
2. 절사 규칙·단위·적용 시점(라인별 / 합계 1회) 실값
3. 제조간접비 배부 기준(기계시간 × 기계경비 vs 노무비 비율)
4. 재료 중량 기준(순면적 vs BBox) — 현재 기본 순면적
5. 역할별 단가 열람 범위 — 현재 프로젝트 구성원(ESTIMATOR·REVIEWER·MANUFACTURING)이면 견적 단가 열람 가능
6. 공급자 정보·견적 유효기간·결제조건·견적서 양식(회사 양식이 있으면 반영)
7. ERP 제품·엔드포인트·인증 방식·payload 필드 매핑, 블록명→품번 기준정보 시트 추가 여부
8. 수동 품번 지정에 2인 확인(직무 분리)을 둘지

## 6. 보안 검토에서 보고만 하고 남긴 항목

| 항목 | 현재 상태 | 처리 방향 |
|---|---|---|
| `/api/audit-logs` | REVIEWER가 전 프로젝트 감사 로그 열람 | 멤버십 필터 |
| 감사 로그가 ERP 작업 payload를 UPDATE마다 복제 | 전역 `fn_audit_log` 설계 | 대용량 컬럼 제외·해시화 |
| 단일 DB 역할 | 소유자는 트리거 DROP·TRUNCATE 가능(0001 ponytail) | 앱 역할 분리 |
| COPY 개정본의 원천이 구 리비전 | `SOURCE_OUTDATED`는 WARN 유지 | 정책 결정 시 ERROR로 |
| OverrideIn 필드 오류 400, 조정 취소 사유 없음 | 공통 규칙 유지 | — |

의도적 단순화는 코드의 `ponytail:` 주석 44곳에 있다(`/ponytail-debt`로 목록화 가능).

## 7. 알려진 함정

- OCCT 8: `_s` 접미사 불일치, IGES 정적 파라미터는 `IGESControl_Controller.Init_s()` 이후에만 유효, IGES `ReadStream` 실패 → 저장 파일 `ReadFile` (MEMORY.md ADR-06).
- vitest는 `@/` 별칭·JSX 미설정 → 테스트 대상 로직은 `src/lib/*.ts`, 상대 경로 import. E2E 파일은 `*.e2e.ts`(vitest가 집어가지 않게).
- Playwright `browser.newContext()`는 config의 `baseURL`을 물려받지 않음 → 직접 넘김. `import.meta` 쓰면 ESM 취급되어 로더 오류 → `__dirname`.
- SQLAlchemy: 이미 로드된 행은 `with_for_update()`로 다시 읽지 않음 → 잠금 조회는 `populate_existing`(`_locked_quote`).
- 견적 헤더 상태는 트리거가 전이를 제한한다 — 테스트에서 SQL로 확정 상태를 만들 땐 DRAFT → IN_REVIEW → CONFIRMED 두 단계.
- 마이그레이션 downgrade는 `IF EXISTS`(구버전 DB 대비). conftest는 downgrade 전에 features·quote_headers를 비운다.
- API는 금액을 문자열로 반환 → 비교 전 `Decimal(str(x))`.
- `str.replace` 일괄 치환이 같은 문장을 가진 다른 모델/함수까지 바꾼 적 있음 → 치환 후 diff 확인.
