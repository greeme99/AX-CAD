# AGENT_CONTRACT — ANTIGRAVITY-PM ↔ CLAUDE-AUDITOR 협업 계약 (V2.0)

> **발효일**: 2026-11-20 (`COLLAB-02`로 기존 Claude Code 중심 `COLLAB-01` 대체)
> **Project Main**: **Google Antigravity (`ANTIGRAVITY-PM`)** — 프로젝트 관리 총괄·메인 구현·GitHub/PR/CI 오케스트레이션
> **Independent Auditor**: **Claude Code (`CLAUDE-AUDITOR`)** — `docs/audit-requests/` 기반 제한된 코드 검수·보안 감사·테스트 검증 전용

---

## 1. 4계층 무결성 원칙

1. **권한 계층**: `ANTIGRAVITY-PM`은 구현·계획·PR 생성 권한을 가지며, `CLAUDE-AUDITOR`는 `.claude/settings.json`의 `deny` 규칙으로 소스 코드(`core/**`, `backend/**`, `frontend/src/**`, `frontend/e2e/**`, `sdk/**`) 및 인프라(`migrations/**`, `.github/workflows/**`, `deploy/**`, `installer/**`) 편집이 기술적으로 차단된다.
2. **공간 계층**: 모든 구현은 `ANTIGRAVITY-PM`의 `New Worktree Mode` 격리 브랜치에서 수행한다. `CLAUDE-AUDITOR`는 읽기 전용 또는 별도 검증 세션에서만 동작한다.
3. **증거 계층**: Audit 요청은 `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md`, Audit 결과는 `docs/audit-reports/AUD-<동일 ID>.md`로 1:1 추적하며, 실제 실행 명령·종료 코드·핵심 로그를 의무 기록한다.
4. **승인 계층**: `main` 머지, 릴리스/배포, 의존성·락파일 변경, DB 마이그레이션, CI/CD 변경, 시크릿 접근은 `HUMAN`의 명시 승인 없이는 실행하지 않는다.

---

## 2. 절대 규칙 (Hard Rules)

1. `main` 직접 커밋·푸시 금지 (`git push --force`, `git reset --hard` 금지).
2. `CLAUDE-AUDITOR`에게 기능 구현, 버그 수정, 리팩터링, 의존성 변경, DB 마이그레이션, CI/CD·배포 변경을 지시하지 않는다.
3. `CLAUDE-AUDITOR`가 직접 수정한 코드는 병합·채택하지 않는다. 산출물은 보고서·권고·테스트 해석·위험 목록으로 제한한다.
4. `.env`, `.env.*`, 시크릿, `AXCAD_PATCH_KEY`, 기밀 실도면은 양측 모두 열람·출력·커밋 금지.
5. 아래 **공유 민감 파일**은 `HUMAN` 승인 없이 수정 금지:
   - `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/pnpm-lock.yaml`
   - `backend/db/migrations/**`, `alembic.ini`
   - `.github/workflows/**`, `deploy/**`, `installer/**`
6. 검증 게이트(린트·타입체크·단위 테스트·필요 시 E2E) 통과 전 완료 선언 금지. 실패·위험을 추정으로 덮지 않는다.

---

## 3. 브랜치 · GitHub · 파일 소유권 규칙

### 브랜치 네이밍 (`ANTIGRAVITY-PM` 단독 생성)
- 구현: `feat/antigravity-<task-id>`, `fix/antigravity-<task-id>`
- 문서·계획·거버넌스: `docs/antigravity-<task-id>`, `chore/antigravity-<task-id>`
- 테스트: `test/antigravity-<task-id>`

### GitHub 연동 총괄 (`ANTIGRAVITY-PM`)
- 인증 계정: `gh auth status` → `greeme99` (HTTPS, scopes: `repo`, `workflow`, `read:org`)
- 담당 범위: 브랜치 푸시(`git push -u origin <branch>`), PR 생성(`gh pr create --base main`), CI 감시(`gh pr checks` / `gh run view --log-failed`), 리뷰 반영, 머지 준비 보고.
- **최종 머지·릴리스 실행은 `HUMAN` 승인 후에만 수행한다.**

### 파일 소유권
| 경로 | ANTIGRAVITY-PM | CLAUDE-AUDITOR |
|---|---|---|
| `core/**`, `backend/**`, `frontend/**`, `tests/**`, `sdk/**` | **주 구현·테스트 소유** | 읽기 전용 (`Edit`/`Write` `deny`) |
| `docs/TASKS.md`, `docs/HANDOFF.md`, `docs/specs/**`, `docs/audit-requests/**` | **작성·관리 소유** | 읽기 (요청서 `상태` 필드만 갱신 가능) |
| `docs/audit-reports/**` | 읽기·반영 | **작성 소유** |
| 민감 파일(`pyproject.toml`, `migrations/**`, `.github/**`, `deploy/**`, `installer/**`) | `HUMAN` 승인 후 수정 | **금지 (`deny`)** |

---

## 4. 포트 및 실행 환경 분리 (`tools/agent-dev.ps1`)

| 모드 / 에이전트 | Backend (FastAPI) | Frontend (Next.js) | `AXCAD_API_ORIGIN` |
|---|---:|---:|---|
| **기본 단독 실행** (`AX-CAD_Start.bat`) | `8000` | `3000` | `http://localhost:8000` |
| **Antigravity 병렬 worktree** | `8200` | `3200` | `http://localhost:8200` |
| **Claude Audit 검증 전용** | `8100` | `3100` | `http://localhost:8100` |

---

## 5. Audit Request / Report 프로토콜

1. `ANTIGRAVITY-PM`이 구현·검증 게이트 통과 후 `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md` 생성 (`TEMPLATE.md` 준수).
2. `HUMAN`이 `CLAUDE-AUDITOR`에 해당 요청 파일 경로 전달 및 "Audit 시작" 승인.
3. `CLAUDE-AUDITOR`는 범위 내 diff 검토 및 요청된 테스트만 실행 → `docs/audit-reports/AUD-<동일 ID>.md` 작성 (`blocker`/`high`/`medium`/`low`/`informational` 분류).
4. `ANTIGRAVITY-PM`이 `blocker`/`high` 이슈를 자신의 구현 브랜치에서 수정 후 재검증. `medium` 이하는 수정 또는 근거와 함께 `HUMAN` 에스컬레이션.
5. `CLAUDE-AUDITOR`는 `approved`를 단독 확정하지 않으며, 최종 PR 머지는 `HUMAN`이 승인한다.

---

## 6. 필수 검증 게이트

```bash
# Backend / CAD Core
uv run ruff check . && uv run ruff format --check .
uv run mypy --platform linux core backend
uv run pytest -q tests/

# Frontend
pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test
```
