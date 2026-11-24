# AGENT_CONTRACT — ANTIGRAVITY-PM ↔ CLAUDE 협업 계약 (V2.1)

> **발효일**: 2026-11-25 (`COLLAB-02` 개정: Antigravity 개발계획 기반 Claude Code 개발 협업 확대 반영)
> **Project Main**: **Google Antigravity (`ANTIGRAVITY-PM`)** — 프로젝트 관리 총괄·개발계획 수립·메인 구현·협업 오케스트레이션·GitHub/PR/CI 총괄
> **Development & Audit Partner**: **Claude Code (`CLAUDE-PARTNER` / `CLAUDE-AUDITOR`)** — **Antigravity의 개발계획 및 협업요청에 따른 개발 협업 수행** + `docs/audit-requests/` 기반의 독립 코드 검수·보안 감사·테스트 검증

---

## 1. 4계층 무결성 원칙

1. **권한 계층**: `ANTIGRAVITY-PM`은 아키텍처·개발계획·메인 구현·PR 생성을 총괄하며, `CLAUDE`는 **Antigravity의 개발계획 및 협업요청에 의해 지정된 범위의 소스 코드(`core/**`, `backend/**`, `frontend/**`, `tests/**`, `sdk/**`) 개발 협업을 수행**한다. 독립 감사 모드에서는 코드 검수·보안 감사·테스트 검증 보고서를 작성한다.
2. **공간 계층**: 모든 구현은 격리 브랜치(`New Worktree Mode`)에서 수행한다.
3. **증거 계층**: 개발 협업은 `docs/TASKS.md` 및 작업 명세로 추적하며, Audit 요청은 `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md`, Audit 결과는 `docs/audit-reports/AUD-<동일 ID>.md`로 1:1 추적하여 실제 실행 명령·종료 코드·핵심 로그를 의무 기록한다.
4. **승인 계층**: `main` 머지, 릴리스/배포, 의존성·락파일 변경, DB 마이그레이션, CI/CD 변경, 시크릿 접근은 `HUMAN`의 명시 승인 없이는 실행하지 않는다.

---

## 2. 절대 규칙 (Hard Rules)

1. `main` 직접 커밋·푸시 금지 (`git push --force`, `git reset --hard` 금지).
2. Claude Code는 **Antigravity의 개발계획 및 협업요청에 의해 개발 협업(기능 구현·수정·리팩터링·테스트 작성)을 수행**한다. 사전 협업요청 없는 임의 독자 수정 코드는 병합·채택하지 않는다.
3. `.env`, `.env.*`, 시크릿, `AXCAD_PATCH_KEY`, 기밀 실도면은 양측 모두 열람·출력·커밋 금지.
4. 아래 **공유 민감 파일**은 `HUMAN` 승인 없이 수정 금지:
   - `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/pnpm-lock.yaml`
   - `backend/db/migrations/**`, `alembic.ini`
   - `.github/workflows/**`, `deploy/**`, `installer/**`
5. 검증 게이트(린트·타입체크·단위 테스트·필요 시 E2E) 통과 전 완료 선언 금지. 실패·위험을 추정으로 덮지 않는다.

---

## 3. 브랜치 · GitHub · 파일 소유권 규칙

### 브랜치 네이밍
- Antigravity 구현: `feat/antigravity-<task-id>`, `fix/antigravity-<task-id>`, `docs/antigravity-<task-id>`
- Claude 협업 구현: `feat/claude-<task-id>`, `fix/claude-<task-id>`, `test/claude-<task-id>`

### GitHub 연동 총괄 (`ANTIGRAVITY-PM`)
- 인증 계정: `gh auth status` → `greeme99` (HTTPS, scopes: `repo`, `workflow`, `read:org`)
- 담당 범위: 브랜치 푸시(`git push -u origin <branch>`), PR 생성(`gh pr create --base main`), CI 감시(`gh pr checks` / `gh run view --log-failed`), 리뷰 반영, 머지 준비 보고.
- **최종 머지·릴리스 실행은 `HUMAN` 승인 후에만 수행한다.**

### 파일 소유권
| 경로 | ANTIGRAVITY-PM | CLAUDE (PARTNER / AUDITOR) |
|---|---|---|
| `core/**`, `backend/**`, `frontend/**`, `tests/**`, `sdk/**` | **주 구현·테스트 총괄 소유** | **협업요청 시 개발·테스트 협업 수행** (사전 요청 없는 임의 수정 금지) |
| `docs/TASKS.md`, `docs/HANDOFF.md`, `docs/specs/**`, `docs/audit-requests/**` | **작성·관리 총괄 소유** | 읽기 및 태스크 완료 상태 갱신 |
| `docs/audit-reports/**` | 읽기·반영 | **감사 모드 시 작성 소유** |
| 민감 파일(`pyproject.toml`, `migrations/**`, `.github/**`, `deploy/**`, `installer/**`) | `HUMAN` 승인 후 수정 | `HUMAN` 승인 후 수정 |

---

## 4. 포트 및 실행 환경 분리 (`tools/agent-dev.ps1`)

| 모드 / 에이전트 | Backend (FastAPI) | Frontend (Next.js) | `AXCAD_API_ORIGIN` |
|---|---:|---:|---|
| **기본 단독 실행** (`AX-CAD_Start.bat`) | `8000` | `3000` | `http://localhost:8000` |
| **Antigravity 병렬 worktree** | `8200` | `3200` | `http://localhost:8200` |
| **Claude 개발협업 / Audit 검증** | `8100` | `3100` | `http://localhost:8100` |

---

## 5. 협업 및 Audit 프로토콜

1. **개발 협업 프로토콜**:
   - `ANTIGRAVITY-PM`이 개발계획 및 구현 명세를 수립하고 `docs/TASKS.md` 또는 이슈/요청서를 통해 작업 단위를 지정.
   - Claude Code가 해당 요청에 따라 격리 브랜치/작업공간에서 구현 및 단위 테스트 작성 진행.
   - 구현 완료 후 필수 검증 게이트를 거쳐 Antigravity 및 사용자(HUMAN)에게 검토 보고.
2. **독립 Audit 프로토콜**:
   - `ANTIGRAVITY-PM` 구현 후 검증 필요 시 `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md` 생성.
   - `HUMAN` 승인 하에 Claude Code가 독립 감사 모드로 진입하여 검수 리포트 `docs/audit-reports/AUD-<동일 ID>.md` 작성.

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
