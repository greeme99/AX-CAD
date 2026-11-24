# RACI — AX-CAD 프로젝트 관리 및 에이전트 R&R (`COLLAB-02` 개정)

> **발효일**: 2026-11-25 | **전환 근거**: `docs/DECISIONS.md` (`COLLAB-02` 개정: Antigravity 개발계획 기반 Claude 개발 협업)
> **표기**: **R**=실행 책임(Responsible) · **A**=최종 승인(Accountable) · **C**=자문/검수(Consulted) · **I**=공유(Informed) · **—**=금지/제외

---

## 1. 운영 주체 정의

| 운영명 | 도구 | 역할 | 작업 모드 |
|---|---|---|---|
| **HUMAN (Owner)** | 사용자 | 최종 의사결정·`main` 머지·배포·민감 파일 승인 | 최종 게이트 |
| **ANTIGRAVITY-PM** | Google Antigravity | **Project Main · 개발계획 총괄 · 메인 구현 · 협업 오케스트레이터** | `New Worktree Mode` (탐색만 `Local Mode`) |
| **CLAUDE-PARTNER / AUDITOR** | Claude Code Desktop | **개발 협업 파트너 및 독립 Audit 에이전트** (Antigravity 계획/요청 기반 구현 협업 + 독립 코드/보안 검수) | 협업 브랜치 편집 / 감사 모드 시 읽기+보고서 |

---

## 2. RACI 매트릭스

| 영역 / 활동 | ANTIGRAVITY-PM | CLAUDE (PARTNER / AUDITOR) | HUMAN | 비고 / 통제 수단 |
|---|:---:|:---:|:---:|---|
| **프로젝트 총괄 (Project Main)** | **R** | I | **A** | `docs/TASKS.md`, `docs/HANDOFF.md` 소유 |
| **백로그 우선순위·작업 계획·명세** | **R** | C (요청 시) | **A** | `docs/specs/**` |
| **기능 구현·버그 수정·리팩터링** | **R** | **R (협업요청 시)** | **A** | Antigravity 개발계획 및 요청에 근거한 협업 |
| **단위·통합·E2E 테스트 작성** | **R** | **R (협업요청 시)** | **A** | `tests/**`, `frontend/e2e/**` |
| **Audit Request 발행** | **R** | I | I | `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md` |
| **코드 리뷰·보안 감사·테스트 검증** | C (요청·반영) | **R** | **A** | `docs/audit-reports/AUD-<동일 ID>.md`에만 기록 |
| **Audit 지적 반영 (수정 구현)** | **R** | **R (협업 시)** | **A** | 지정된 협업/수정 브랜치에서 수행 |
| **GitHub 브랜치·푸시·PR 생성·CI 감시** | **R** | **— (금지)** | **A** | `gh` CLI (`greeme99`), `main` 직접 push 금지 |
| **의존성·락파일·DB 마이그레이션·CI/CD** | R (승인 후) | **— (금지)** | **A (필수 승인)** | `pyproject.toml`, `uv.lock`, `migrations/**`, `.github/**` |
| **`main` 머지 · 릴리스 · 배포** | C (준비·보고) | **— (금지)** | **R / A** | 사람 단독 승인·실행 |
| **시크릿·`.env`·`AXCAD_PATCH_KEY`** | **— (금지)** | **— (금지)** | **R / A** | 양측 `deny` |

---

## 3. 협업 및 독립성 원칙
- **개발 협업**: Claude Code는 Antigravity가 수립한 개발계획 및 명시적 협업요청에 근거하여 기능 개발, 버그 수정, 테스트 보강을 수행한다. 사전 계획 없는 독자 수정은 병합하지 않는다.
- **독립 검증 (Audit)**: Audit 모드 수행 시 Claude Code는 Antigravity의 지시에 맹목적으로 따르지 않고 객관적이고 독립적인 검증자로 동작한다.
- **최종 통제**: 모든 PR 생성과 CI 오케스트레이션은 Antigravity가 총괄하며, 최종 `main` 머지는 사람(HUMAN)이 승인한다.
