# TASKS — AX-CAD 프로젝트 작업 대기열 (`ANTIGRAVITY-PM` 총괄)

> **거버넌스**: `COLLAB-02` (`docs/AGENT_CONTRACT.md` V2.0 · `docs/RACI.md`)
> - **Project Main / 구현 / GitHub 총괄**: `antigravity` (`ANTIGRAVITY-PM`, `New Worktree Mode`)
> - **독립 검수 / 보안 감사**: `claude-auditor` (`docs/audit-requests/` 발행 시에만 수행)
> - **상태**: `todo` | `in-progress` | `audit-pending` | `done` | `blocked`

---

## 1. 미결 작업 우선순위 대기열 (이관 완료 — `ANTIGRAVITY-PM` 소유)

| 우선순위 | ID | 제목 | 담당 | 브랜치 | 허용 범위 | 상태 | 위험도 / 비고 |
|---:|---|---|---|---|---|---|---|
| **P0** | **TASK-013** | PR #83 `viewer.ui.e2e.ts`·`workers: 1` 보정 및 PR #83/#87/#88 머지 준비 | antigravity | `docs/antigravity-pr-sync` | `frontend/e2e/**`, `frontend/playwright.config.ts` | `in-progress` | PR #84·#85·#86 `main` 머지 완료 |
| **P1** | **TASK-008** | 프론트엔드 `SheetDialog`·UI Phase 0·3·곡면 리뷰 결함(UI-01~13 + R82-F01~F09) 수정 | antigravity | `fix/antigravity-ui-review` | `frontend/src/**` | `audit-pending` | PR #87 생성, `AUD-20261120-003` 발행 (38/38 vitest PASS) |
| **P1** | **TASK-012** | PR #78~#82 백엔드 리뷰 결함(R82-01~08: P1 5·P2 3) 수정 | antigravity | `fix/antigravity-pr78-82` | `core/geometry/surface_edit.py`, `surface_analysis.py`, `core/dxf/dwg_builtin.py`, `routes_model.py`, `tests/test_pr78_82_edge.py` | `audit-pending` | PR #88 생성, `AUD-20261120-004` 발행 (69/69 pytest PASS) |
| **P2** | **TASK-002** | 조립 A1~A4 단계별 구현 (`plan.md` §23.10, 마이그레이션 `0036`~`0038`) | antigravity | `feat/antigravity-assembly-a1` | `core/geometry/**`, `backend/**`, `frontend/src/components/cad/AssemblyWorkbench.tsx`, `tests/**` | `todo` | 명세: `docs/specs/assembly-a1-a4-spec.md` (PR #80 `0035` 사용으로 `0036~0038` 적용, 마이그레이션 사람 승인 필요) |
| **P3** | **TASK-010** | CAM C1~C4 단계별 구현 (`plan.md` §24, 마이그레이션 `0039`) | antigravity | `feat/antigravity-cam-c1` | `core/cam/**`, `backend/**`, `frontend/src/components/cad/CamPanel.tsx`, `tests/**` | `todo` | 명세: `docs/specs/cam-cae-spec.md` (`TASK-002` 완료 후 착수, 마이그레이션 사람 승인 필요) |
| **P3** | **TASK-011** | CAE E1~E3 선형 정적 FEA 구현 (`plan.md` §24, `scipy`+마이그레이션 `0040`) | antigravity | `feat/antigravity-cae-e1` | `core/cae/**`, `backend/**`, `frontend/src/components/cad/CaePanel.tsx`, `tests/**` | `todo` | 명세: `docs/specs/cam-cae-spec.md` (`pyproject.toml` `scipy` 추가·`0040` 사람 승인 필요) |

---

## 2. 완료 이력 (Closed)

| ID | 제목 | 완료 기준 / 근거 | 상태 |
|---|---|---|---|
| **TASK-001** | 조립 A1~A4 설계 명세 (`plan.md` §23.10) | `docs/specs/assembly-a1-a4-spec.md` | `done` |
| **TASK-003** | 곡면 S6~S8 프론트엔드 UI | PR #78~#81 (`origin/main` 머지 완료) | `done` |
| **TASK-004** | 곡면 S6~S8 UI·API 통합 리뷰 및 E2E 검증 | PR #84 (`pr78-82-review.md`, `surface.e2e.ts` CI 5/5 GREEN) | `done` |
| **TASK-005** | 곡면 S6~S8 백엔드 리뷰 결함 수정 | PR #78~#81 (`origin/main` 머지 완료) | `done` |
| **TASK-006** | CAM·CAE(`plan.md` §24) 의사결정 및 상세 명세 | `docs/specs/cam-cae-decisions.md`, `cam-cae-spec.md` (`CAM-CAE-01`) | `done` |
| **TASK-009** | Playwright E2E 확장 (`model3d.sheet`, `viewer.ui`, `surface`) | PR #83, PR #84 | `done` |
| **TASK-014** | Project Main & GitHub 연동 이관 (`COLLAB-02`) | PR #85 (`main` 머지 완료) | `done` |
| **TASK-007** | 판금 S1~S5·DWG(`acadrust`) P0/P1 결함 수정 (`SH-01/02/03/05/07/08`) | PR #86 (`main` 머지 완료, 68/68 pytest PASS, `AUD-20261120-002`) | `done` |

---

## 3. 외부 입력 / 사용자 PC 대기 항목 (이관 보존 — `docs/HANDOFF.md` §4 연계)
- **R1.0/R2.2 Windows PC 패치 재검증** (`TC-158` 7·8단계, `BUG-53`·`BUG-54` 사용자 PC 최종 확인)
- **실도면 DXF 성능 측정** (`TC-97`, 2D 제작 도면 ≥10개)
- **G1 기준정보 실데이터(1~8 시트) + 실제 공급자 정보(`AXCAD_SUPPLIER_FILE`) + UAT**
