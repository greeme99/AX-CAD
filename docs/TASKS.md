# TASKS — AX-CAD 멀티 에이전트 작업 대기열

> **사용법**: 사람이 작업을 등록하고 `담당`, `브랜치`, `허용 범위`를 지정한다.
> 각 에이전트는 `docs/AGENT_CONTRACT.md` 규칙에 따라 자신에게 할당된 항목만 별도 worktree에서 수행한다.
>
> 상태: `todo` | `in-progress` | `review` | `done` | `blocked`

---

## 활성 작업 (Active Queue)

| ID | 제목 | 담당 | 브랜치 | 허용 범위 | 상태 | 비고 |
|---|---|---|---|---|---|---|
| TASK-001 | 조립 A1~A4 설계 명세·경계 조건·테스트 시나리오 (`plan.md` §23.10) | antigravity | `docs/antigravity-assembly-spec` | `docs/**` | `done` | 명세 완료: `docs/specs/assembly-a1-a4-spec.md` |
| TASK-002 | 조립 A1~A4 코어·API 구현 (`plan.md` §23.10) | claude | `feat/claude-assembly-a1-a4` | `core/geometry/**`, `backend/**`, `tests/**` | `todo` | 명세 완료: `docs/specs/assembly-a1-a4-spec.md` (A1→A4 단계별 PR) |
| TASK-003 | 곡면 S6~S8 프론트엔드 대화상자·분석 색 지도 UI | claude | `feat/claude-surface-ui` | `frontend/src/**` | `todo` | 명세 완료: `docs/specs/surface-ui-spec.md` |
| TASK-004 | 곡면 S6~S8 UI·API 통합 리뷰 및 E2E 시나리오 검증 | antigravity | `review/antigravity-surface-ui` | `docs/**`, `frontend/e2e/**` | `todo` | TASK-003 구현 완료 후 리뷰·검증 |
| TASK-005 | 곡면 S6~S8 백엔드·API 독립 리뷰 지적(P0 1·P1 5·P2 3) 수정 | claude | `fix/claude-surface-review` | `core/geometry/surface*.py`, `backend/api/routes_model.py`, `tests/test_surface*.py` | `todo` | 리뷰 완료: `docs/reviews/surface-s6-s8-review.md` |
| TASK-006 | CAM·CAE(`plan.md` §24) 범위 확정 의사결정·아키텍처 초안 | antigravity | `docs/antigravity-cam-cae-decisions` | `docs/**` | `done` | 완료: `docs/specs/cam-cae-decisions.md` (사용자 6개 결정 대기) |
| TASK-007 | 판금 S1~S5·DWG(`acadrust`) 백엔드 독립 리뷰 지적(SH-01~12) 수정 | claude | `fix/claude-sheet-dwg-review` | `core/geometry/sheetmetal.py`, `core/geometry/unfold.py`, `core/dxf/dwg.py`, `core/quote_engine/cost.py`, `core/copilot/dfm.py`, `backend/api/routes_model.py`, `backend/api/main.py`, `tests/test_sheetmetal_edge.py` | `todo` | 리뷰 완료: `docs/reviews/sheet-ui-dwg-review.md` (`tests/test_sheetmetal_edge.py` xfail 고정) |
| TASK-008 | 프론트엔드 `SheetDialog`·UI Phase 0·3 독립 리뷰 지적(UI-01~13) 수정 | claude | `fix/claude-ui-phase03-review` | `frontend/src/**` | `todo` | 리뷰 완료: `docs/reviews/sheet-ui-dwg-review.md` (`sheet.edge.test.ts` it.fails 고정) |
| TASK-009 | Playwright E2E 테스트 스위트 확장 (`model3d.sheet`, `viewer.ui`) | antigravity | `test/antigravity-e2e-suite` | `frontend/e2e/**`, `docs/TASKS.md` | `done` | 완료: `frontend/e2e/helpers.ts`, `model3d.sheet.e2e.ts`, `viewer.ui.e2e.ts` |
| TASK-010 | CAM C1~C4 구현 (`plan.md` §24, 마이그레이션 `0038`) | claude | `feat/claude-cam-c1` | `core/cam/**`, `backend/**`, `frontend/src/components/cad/CamPanel.tsx`, `tests/**` | `todo` | 명세 완료: `docs/specs/cam-cae-spec.md` (조립 A4 완료 후 C1→C4 PR 분할) |
| TASK-011 | CAE E1~E3 선형 정적 FEA 구현 (`plan.md` §24, `scipy`+`0039`) | claude | `feat/claude-cae-e1-e2` | `core/cae/**`, `backend/**`, `frontend/src/components/cad/CaePanel.tsx`, `tests/**` | `todo` | 명세 완료: `docs/specs/cam-cae-spec.md` (`pyproject.toml` `scipy` 추가 사람 승인 필요) |

---

## 작업 상세 및 인계 로그

### TASK-001 → TASK-002 (명세 인계 — Antigravity → Claude Code)
- 담당: claude (명세 작성: antigravity, `done`)
- 브랜치: `feat/claude-assembly-a1` (A1→A2→A3→A4 단계별 PR)
- 상태: `todo` (명세 준비 완료, Claude Code 구현 대기)
- 허용 범위: `core/geometry/**`, `backend/**`, `tests/**`, `frontend/src/components/cad/AssemblyWorkbench.tsx`
- 명세 문서: `docs/specs/assembly-a1-a4-spec.md` (마이그레이션 `0035`~`0037`, 신규 메이트 7종 잔차·참조 확대·DOF/과구속 진단, 하위 조립/패턴/교체, 간섭·접촉·여유·드래그·분해도, 질량·다단 BOM·STEP XDE·조립 견적 `A-ASM`, `ASM-T01~T10`)
- 완료 기준: 단계별 `uv run ruff check . && uv run ruff format --check . && uv run mypy core backend && uv run pytest -q tests/` 통과
- 다음 담당: `claude` (A1 구현) → `antigravity` (리뷰·검증)

### TASK-003 (명세 인계 — Antigravity → Claude Code)
- 담당: claude (명세 작성: antigravity)
- 브랜치: `feat/claude-surface-ui`
- 상태: `todo` (명세 준비 완료, Claude Code 구현 대기)
- 허용 범위: `frontend/src/**` (백엔드·의존성 무변경)
- 명세 문서: `docs/specs/surface-ui-spec.md` (S6 7종·S7 6종·S8 4종 API 계약, 컴포넌트 설계, 경계 조건 7항목, `SURF-U01~U05` 테스트 시나리오)
- 완료 기준: `pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test` 통과
- 다음 담당: `claude` (구현) → `antigravity` (`TASK-004` 리뷰·브라우저 검증)

<!-- 후속 작업 인계 시 위 형식으로 추가한다. -->
