# TASKS — AX-CAD 프로젝트 작업 대기열 (`ANTIGRAVITY-PM` 총괄)

> **거버넌스**: `COLLAB-02` (`docs/AGENT_CONTRACT.md` V2.0 · `docs/RACI.md`)
> - **Project Main / 구현 / GitHub 총괄**: `antigravity` (`ANTIGRAVITY-PM`, `New Worktree Mode`)
> - **독립 검수 / 보안 감사**: `claude-auditor` (`docs/audit-requests/` 발행 시에만 수행)
> - **상태**: `todo` | `in-progress` | `audit-pending` | `done` | `blocked`

---

## 1. 미결 작업 우선순위 대기열 (`ANTIGRAVITY-PM` 소유)

| 우선순위 | ID | 제목 | 담당 | 브랜치 | 허용 범위 | 상태 | 위험도 / 비고 |
|---:|---|---|---|---|---|---|---|
| **P2** | **TASK-016** | `CLAUDE-AUDITOR` 독립 감사 실행(`AUD-20261120-001~016`) 및 R3.1 릴리스 노트(`installer/release-notes/3.0.0.txt`) 준비 | antigravity / claude-auditor | `docs/antigravity-r3-release` | `docs/audit-reports/**`, `installer/release-notes/**` | `in-progress` | PR #99(`3.0.0.txt`, `AUD-20261120-INDEX.md`) 머지 완료 · `CLAUDE-AUDITOR` 실행 대기 |

---

## 2. 완료 이력 (Closed)

| ID | 제목 | 완료 기준 / 근거 | 상태 |
|---|---|---|---|
| **TASK-001** | 조립 A1~A4 설계 명세 (`plan.md` §23.10) | `docs/specs/assembly-a1-a4-spec.md` | `done` |
| **TASK-003** | 곡면 S6~S8 프론트엔드 UI | PR #78~#81 (`origin/main` 머지 완료) | `done` |
| **TASK-004** | 곡면 S6~S8 UI·API 통합 리뷰 및 E2E 검증 | PR #84 (`pr78-82-review.md`, `surface.e2e.ts` CI 5/5 GREEN) | `done` |
| **TASK-005** | 곡면 S6~S8 백엔드 리뷰 결함 수정 | PR #78~#81 (`origin/main` 머지 완료) | `done` |
| **TASK-006** | CAM·CAE(`plan.md` §24) 의사결정 및 상세 명세 | `docs/specs/cam-cae-decisions.md`, `cam-cae-spec.md` (`CAM-CAE-01`) | `done` |
| **TASK-009** | Playwright E2E 확장 (`model3d.sheet`, `viewer.ui`, `surface`) | PR #83, PR #84 (`main` 머지 완료, 4종 E2E 전건 PASS) | `done` |
| **TASK-014** | Project Main & GitHub 연동 이관 (`COLLAB-02`) | PR #85 (`main` 머지 완료, `AUD-20261120-001`) | `done` |
| **TASK-007** | 판금 S1~S5·DWG(`acadrust`) P0/P1 결함 수정 (`SH-01/02/03/05/07/08`) | PR #86 (`main` 머지 완료, 68/68 pytest PASS, `AUD-20261120-002`) | `done` |
| **TASK-008** | 프론트엔드 WebGL 누수·스택 오버플로·트리/재생성 결함 수정 | PR #87 (`main` 머지 완료, 38/38 vitest PASS, `AUD-20261120-003`) | `done` |
| **TASK-012** | PR #78~#82 백엔드·내장 DWG 리뷰 결함 수정 (`R82-01/02/03/05/06/08`) | PR #88 (`main` 머지 완료, `ezdwg` 포함 5/5 GREEN, `AUD-20261120-004`) | `done` |
| **TASK-013** | PR #83 E2E 보정 및 오픈 PR 전건 `main` 머지 완료 | PR #83·#89 (`main` 머지 완료, 오픈 PR 0건) | `done` |
| **TASK-002** | 조립 A1~A4 백엔드 코어·마이그레이션(`0036~0038`)·API 구현 (`plan.md` §23.10) | PR #90(A1)·PR #93(A2~A4) (`main` 머지 완료, `ASM-T01~T09`, `AUD-20261120-005~008`) | `done` |
| **TASK-010** | CAM C1~C4 구현 (`plan.md` §24, 마이그레이션 `0039`, `CamPanel.tsx`) | PR #95 (`main` 머지 완료, 5/5 GREEN, `CAM-T01~T05`, `AUD-20261120-009`) | `done` |
| **TASK-015** | 조립 A1~A4 프론트엔드 UI(`AssemblyWorkbench.tsx`) 연동 및 STEP XDE 다단 라운드트립 | PR #96 (`main` 머지 완료, 5/5 GREEN, `ASM-T10`, `AUD-20261120-010`) | `done` |
| **TASK-011** | CAE E1~E3 선형 정적 FEA 구현 (`plan.md` §24, `scipy`+`0040`, `CaePanel.tsx`, PDF 보고서) | PR #97 (`main` 머지 완료, 5/5 GREEN, `CAE-T01~T04`, `AUD-20261120-011`) | `done` |
| **TASK-017** | CAM NX 고도화 C5~C8 (3축 자유곡면·2축 선반·3+2 인덱싱·덱셀 스톡 시뮬·Siemens/Heidenhain/Lathe 후처리, `0041`) | PR #101 (`main` 머지 완료, 5/5 GREEN, `CAM-T06~T10`, `AUD-20261120-012`) | `done` |
| **TASK-018** | CAE Simcenter 고도화 E4~E7 (2차 Tet10·모달·좌굴·열전달/열응력, `0042`, `CaePanel` 온도 모드) | PR #102 (`main` 머지 완료, 5/5 GREEN, `CAE-T06~T10`, `AUD-20261120-013`) | `done` |
| **TASK-019** | 조립 95점 고도화 A5~A7 (대형 어셈블리 BVH $O(N\log N)$/LOD · 기구학 모션 · 분해도 2D DXF/풍선/BOM) | PR #103 (`main` 머지 완료, 5/5 GREEN, `ASM-T11~T13`, `AUD-20261120-014`) | `done` |
| **TASK-020** | CAM 95점 고도화 C9~C10 (`0043`, 동시 5축 Swarf/Multi-Axis RTCP · G코드 역파싱/한계/스톡 검증기) | PR #104 (`main` 머지 완료, 5/5 GREEN, `CAM-T11~T12`, `AUD-20261120-015`) | `done` |
| **TASK-021** | CAE 95점 고도화 E8~E10 (`0044`, 다물체 접촉 · 비선형 J2 탄소성 NR · 과도 열전달 Backward Euler) | PR #105 (`main` 머지 완료, 5/5 GREEN, `CAE-T11~T13`, `AUD-20261120-016`) | `done` |
| **TASK-034** | UI 메뉴명 'AI Copilot' → 'AI-assistant' 변경 | PR #118 (`origin/main` 머지 완료, `AUD-20261121-001`) | `done` |
| **TASK-035** | Antigravity Full Design Pipeline UI/UX 고도화 (디자인 토큰·듀얼 테마·반응형) | `design-tokens.json`, `ThemeToggle`, `AUD-20261121-002` | `audit-pending` |

---

## 3. 외부 입력 / 사용자 PC 대기 항목 (이관 보존 — `docs/HANDOFF.md` §4 연계)
- **R1.0/R2.2 Windows PC 패치 재검증** (`TC-158` 7·8단계, `BUG-53`·`BUG-54` 사용자 PC 최종 확인)
- **실도면 DXF 성능 측정** (`TC-97`, 2D 제작 도면 ≥10개)
- **G1 기준정보 실데이터(1~8 시트) + 실제 공급자 정보(`AXCAD_SUPPLIER_FILE`) + UAT**
