# AX-CAD 세션 인계 (Hand-off)

> 2026-10-10 기준. 새 세션은 이 문서 → `CLAUDE.md` → `.claude/memory/MEMORY.md` 순서로 읽고 시작한다.

## 0. 요약

| 항목 | 상태 |
|---|---|
| 기준 커밋 | `main` = `e1bfbe2` (Merge PR #6), PR #7(S11-b 보안 반영 + S12) 진행 중 |
| 작업 브랜치 | `main-23t1bb` — 머지 후 매번 `origin/main`에서 다시 시작 |
| 완료 스프린트 | S1~S12 개발 완료(G1 의존분 제외), UAT는 사용자 수행(테스트 보고서 §7) |
| 테스트 | pytest 182, vitest 77, 테스트 보고서 V1.14 Pass 73/81 |
| 마이그레이션 | 0001~0012 (`0009`·`0010` 견적서 발행 대장, `0011` BOM, `0012` ERP 연동 작업) |
| 다음 작업 | §4 순서표 — 남은 것은 G1·공급자 정보·ERP 사양 의존(S8-d, TC-67, 정식 견적서, 실 ERP), 견적 Revision, TC-82 E2E |

## 1. 환경 기동 (클라우드 컨테이너)

컨테이너가 재시작되면 아래 순서로 복구한다. 접속 정보 값은 문서·로그에 쓰지 않는다.

```bash
service postgresql start                 # 재시작 후 꺼져 있음
uv sync && (cd frontend && pnpm install)
# 필요한 환경변수(값은 repo-root .env 또는 세션 env 파일, 커밋 금지)
#   DATABASE_URL, TEST_DATABASE_URL(개발 DB와 분리), JWT_SECRET, AXCAD_VAR_DIR(선택)
#   ERP_API_URL, ERP_API_TOKEN, ERP_TIMEOUT_S, ERP_BACKOFF_S (ERP 전송 시)
uv run alembic upgrade head              # 개발 DB
uv run uvicorn backend.api.main:app --port 8000
pnpm --dir frontend dev                  # :3000
```

- `backend/db/session.py`가 repo-root `.env`를 읽는다(기존 환경변수 우선, 값 출력 안 함).
- 테스트는 `TEST_DATABASE_URL`만 쓴다(없으면 DB 테스트 skip, 개발 DB로 절대 대체하지 않음).
- 개발 DB에는 테스트용 ACTIVE 기준정보 `G1-2027`, `G1-2027B`(가짜 단가)와 테스트 견적 2건이 있다. G1 실데이터가 오면 새 버전으로 가져와 활성화한다.

## 2. 작업 규칙 (이 세션에서 굳어진 것)

| 규칙 | 이유 |
|---|---|
| 게이트는 `set -o pipefail` 후 실행: ruff check → ruff format --check → mypy core backend → pytest / pnpm typecheck → lint → test → build | `| tail`이 실패를 가린 적 있음 |
| dev 서버가 떠 있으면 `pnpm build` 금지 (`.next` 충돌) | 빌드 깨짐 → `.next` 삭제 후 재빌드 |
| DB 테스트 스위트 2개 동시 실행 금지 | 같은 테스트 DB를 truncate/downgrade |
| 서버 종료는 `ps`로 PID 찾아 kill (`pkill -f` 금지) | 자기 셸까지 종료됨 |
| SQL에 DROP/TRUNCATE가 들어가는 파일은 Edit/Write 도구로 작성 | 위험 명령 훅이 bash 문자열을 차단 |
| PR 작업은 `gh api` REST (`pulls`, `pulls/{n}/merge`, `pulls/{n}/ccr/ready_for_review`) | GitHub MCP가 "invalid session" 반환 |
| 기능 단위 커밋 + `Co-Authored-By`/`Claude-Session` 트레일러, 스프린트마다 `docs/7` 결과·`docs/README` 변경 이력 갱신 | DoD |
| 업로드·인증·커널·금액 경로 변경 후 `security-reviewer` 검토 → 지적 반영 커밋 → PR 본문 갱신 | `.claude/rules/security.md` |
| G1 작성본(단가·임률)은 `docs/inputs/private/`(git 무시)에만 | 사내 기밀 |

## 3. 견적 엔진 핵심 (S8~S10에서 확정된 설계)

- **흐름**: 메트릭(2D `core/quote_engine/metrics2d.py`, 3D `_bodies`) → `compute_quote()`(`cost.py`) → `quote_lines` + `quote_traces` + `quote_validation_logs` → `validate_quote()`(`validate.py`).
- **금액**: 전부 `Decimal`, DB `numeric`. 라인 금액은 정확한 수량으로 계산 후 반올림(0.0001 정리 → 절사 규칙). 범위 상한 `MAX_QTY 1e11`, `MAX_AMOUNT 1e13`, `MAX_TOTAL 1e15`.
- **공식**: AST 화이트리스트 평가기(`formula.py`), 파라미터명은 메트릭명과 겹칠 수 없음.
- **추적성**: 라인당 trace 1개(`sources` ≤ 500 + `source_count` 전체 개수), 원천은 리비전 FK + 엔티티 핸들 또는 3D Feature ID.
- **감사 분리**: `calculated_*`는 DB 트리거로 불변, `override_*`는 사유·작성자·시각 CHECK, trace·log는 append-only, 확정(CONFIRMED) 견적은 override 변경 불가(0007).
- **기준정보**: 버전별, ACTIVE 후 동결(트리거). 견적은 생성 시점의 버전 ID를 고정 기록. G1 엑셀 양식·파서는 `core/quote_engine/g1.py` 한 곳에서 정의.
- **검증 코드**: PRICE_MISSING, QTY_INVALID, OVERRIDE_LARGE(±30%), MATERIAL_REQUIRED, DUPLICATE_LINE, TOTAL_MISMATCH, AMOUNT_OVERFLOW, OVERHEAD_STALE(WARN), SOURCE_OUTDATED + 엔진 ERROR 승계.
- **화면**: `/quotes/[quoteId]`(라인 표·요약·검증·TraceDrawer·조정 폼), 뷰어/모델 `?select=` 하이라이트, `/admin/master-data`(G1 가져오기).

## 4. 남은 작업 (권장 순서)

| 순서 | 작업 | FN / TC | G1 필요 | 메모 |
|---|---|---|---|---|
| ✅ 1 | 견적 승인 워크플로 (S11-a, 마이그레이션 0008, `quote_approvals`) | FN-22 / TC-76~78 | 아니오 | 완료 — FN-22 "견적 구현 결정" 참조 |
| ✅ 2 | 견적서 PDF/XLSX (S11-b, 마이그레이션 0009·0010 `quote_reports`) | FN-21 / TC-79~81 | 샘플 값 | 완료 — 정식 출력은 `backend/config/supplier.json`을 실제 값으로 바꾸고 `_sample` 키를 지워야 열림(그 전엔 초안만) |
| 3 | 견적 Revision(재견적) | S11 | 아니오 | 확정 견적 수정 = 새 견적 생성 |
| ✅ 4 | BOM 생성·CSV/JSON (S12-a, 0011) | FN-23 / TC-90~92 | 일부 | 완료 — 블록명→품번 기준정보 매핑은 G1 후 |
| ✅ 5 | ERP 전송 + integration_jobs 재시도 (S12-b, 0012) | FN-24 / TC-93~96 | 아니오 | 완료 — 실제 ERP 주소·토큰·payload 매핑은 ERP 담당과 확정 필요 |
| 6 | S8-d: 5·6 시트 규칙 → 7 시트 기대값 회귀 | TC-60, 64 | **예** | 샘플 DXF ≥15, STEP ≥5 |
| 7 | 수기 견적 3건 원 단위 일치 | TC-67 | **예** | R3 수락 기준 |
| 8 | E2E 견적 생성 → 승인 → PDF | TC-82 | 2 이후 | Playwright |

## 5. 미결 결정 (사용자 / G1 회의)

1. 셋업 시간: 개당 vs 로트당 적용
2. 절사 규칙·단위·적용 시점(라인별 / 합계 1회) 실값
3. 제조간접비 배부 기준(기계시간 × 기계경비 vs 노무비 비율)
4. 재료 중량 기준(순면적 vs BBox) — 현재 기본 순면적
5. 역할별 단가 열람 범위(보안 검토 L9: 현재 프로젝트 구성원이면 견적 단가 열람 가능)
6. (S11) 공급자 정보·견적 유효기간·결제조건·견적서 양식 샘플
7. (S12) ERP 제품·엔드포인트·인증 방식·payload 필드 매핑, 블록명→품번 기준정보(G1 시트 추가 여부)

## 6. 알려진 함정

- OCCT 8: `_s` 접미사 불일치, IGES 정적 파라미터는 `IGESControl_Controller.Init_s()` 이후에만 유효, IGES `ReadStream` 실패 → 저장 파일 `ReadFile` (MEMORY.md ADR-06).
- vitest는 `@/` 별칭·JSX 미설정 → 테스트 대상 로직은 `src/lib/*.ts`에 두고 상대 경로 import.
- 마이그레이션 downgrade는 `IF EXISTS`로 작성(구버전 DB 대비). conftest는 downgrade 전에 features를 비운다.
- API는 금액을 문자열로 반환 → 검증 로직에서 비교 전 `Decimal(str(x))` 변환.
