# GEMINI.md — AX-CAD ANTIGRAVITY-PM 운영 규칙

<!-- STABLE_PREFIX: 동적 상태·날짜·이슈 번호를 이 파일에 넣지 않는다 (캐시 히트 유지) -->
[ACTIVE_RULES_INDEX]
- Role (`COLLAB-02` 개정): **Antigravity (`ANTIGRAVITY-PM`)** = Project Main · 개발계획 수립 · 메인 구현 · 협업 오케스트레이션 · GitHub/PR/CI 총괄 / **Claude Code (`CLAUDE-PARTNER`)** = 독립 검수·보안감사 및 **Antigravity의 개발계획 및 협업요청에 따른 개발 협업 수행**
- On-Demand Context: `docs/AGENT_CONTRACT.md` → `docs/RACI.md` → `docs/TASKS.md` → `docs/HANDOFF.md` → `CLAUDE.md` → `.claude/memory/MEMORY.md` (UI=`DESIGN.md`, 도메인=`.claude/rules/*.md`, Review=`.agents/rules/axcad-review.md`, Cost=`.agents/rules/cost-engineering.md`)
- Cost Engineering Skills: `.agents/skills/{ponytail,caveman,handoff,grill-me,cost-engineering}` 온디맨드 로드 강제
- Mode/Branch: 읽기 전용=`Local Mode` / 구현·수정=`New Worktree Mode` (`feat|fix|docs|test|chore/antigravity-<id>`)
- Audit Pipeline: 요청=`docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md` → 보고=`docs/audit-reports/AUD-<동일 ID>.md`
- Ports: `.\tools\agent-dev.ps1 -Agent antigravity` (API `8200` / Web `3200`)

## 1. Hard Guardrails (위반 금지)
- `main` 직접 커밋·푸시, `git push --force`, `git reset --hard` 금지. 최종 `main` 머지·배포는 사람 승인 필수.
- **개발 협업 & 감사 원칙**: Antigravity가 개발계획 및 협업 범위를 총괄하며, Claude Code는 **Antigravity의 개발계획 및 협업요청에 의해 개발 협업을 수행**한다. (사전 계획/요청 없는 임의 코드 수정 병합은 금지하며, 감사 요청 시에는 독립 감사 모드로 검수·보고서 작성)
- `.env`, `.env.*`, 시크릿, `AXCAD_PATCH_KEY`, 기밀 실도면 열람·출력·커밋 금지.
- 사람 승인 없는 공유 민감 파일 수정 금지(`pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/pnpm-lock.yaml`, `backend/db/migrations/**`, `.github/workflows/**`, `deploy/**`, `installer/**`).

## 2. Cost Engineering (품질 100% 유지 + 토큰 최소화)
- **Caveman / Ponytail**: 인사·반복 설명·불필요 주석·과잉 추상화 금지. 표·Unified Diff·실제 명령/종료코드 중심으로 출력.
- **GRILL-ME**: 요구사항·스펙·제약이 모호하면 구현 전 핵심 질문으로 확정(`/grill-me`).
- **Handoff**: 작업 단위 종료 또는 컨텍스트 누적(20턴 초과/동일 오류 3회) 시 핵심 상태만 `docs/TASKS.md`·`docs/HANDOFF.md`에 기록 후 세션 전환(`/compact` 또는 새 세션).
- **goal**: 기본 **DENY**. 사용자 명시 요청 시에만 허용.
- **Subagent 격리**: 5개 이상 파일 횡단 탐색·로그 분석은 `research`, 브라우저 확인은 `browser`(`flash`) subagent에 위임해 메인 컨텍스트 오염 방지.

## 3. 검증 게이트 (통과 전 완료 보고 금지)
- Backend: `uv run ruff check . && uv run ruff format --check . && uv run mypy --platform linux core backend && uv run pytest -q tests/`
- Frontend: `pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test`
