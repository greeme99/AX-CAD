# AGENT_CONTRACT — Claude Code ↔ Antigravity 협업 계약

> 이 문서는 **Claude Code (메인 개발 AI Coding Agent)**와 **Google Antigravity (탐색·설계·문서화·리뷰·병렬 검증 파트너)**가 `AX-CAD` 저장소에서 충돌 없이 시너지를 내며 협업하기 위한 공통 규칙이다.

---

## 1. 핵심 운영 원칙

1. **한 파일, 한 시점, 한 에이전트**: 두 에이전트가 같은 워킹 디렉터리나 같은 브랜치에서 동시에 파일을 수정하지 않는다.
2. **Worktree 격리 필수**: 코드·문서를 수정할 때는 반드시 세션별 Git worktree에서 작업한다.
   - Claude Code: Desktop의 Worktree 세션 또는 `claude --worktree claude-<task-id>` (`.claude/worktrees/`)
   - Antigravity: **New Worktree Mode** (탐색·읽기 전용 리서치만 `Local Mode` 허용)
3. **계획은 공유 문서로, 구현은 분리 브랜치로**: 작업 할당은 `docs/TASKS.md`, 상태 인계는 `docs/HANDOFF.md`, 아키텍처 결정은 `.claude/memory/MEMORY.md` 및 `docs/DECISIONS.md`를 통해 공유한다.
4. **`main`은 사람이 통제**: 어떤 에이전트도 `main` 브랜치에 직접 커밋·푸시하지 않는다. 모든 반영은 기능 브랜치 → CI 통과 → 사람 리뷰 → PR 머지로 진행한다.
5. **실행 포트·DB 분리**: 병렬 기동 시 포트와 테스트 DB를 분리해 충돌을 차단한다.

---

## 2. 역할 분담 (Role Separation)

| 단계 / 영역 | Claude Code (메인 개발) | Google Antigravity (협업 파트너) | 공유 산출물 |
|---|---|---|---|
| **1. 탐색·명세** | 구현 가능성·기존 커널/API 영향 확인 | 코드베이스 탐색, 요구사항·엣지 케이스·보안/성능 리스크 분석 | `docs/TASKS.md`, `docs/` |
| **2. 설계** | 기술 구현안·데이터/스키마 설계 | 대안 비교, 경계 조건, 테스트 전략 수립 | `docs/DECISIONS.md` |
| **3. 구현** | `core/`, `backend/`, `frontend/` 핵심 구현·리팩터링·단위 테스트 | 구현 브랜치 직접 수정 금지 (별도 합의된 독립 UI/보조 태스크는 전용 worktree에서만) | 기능 브랜치 |
| **4. 검증·리뷰** | 린트·타입체크·단위 테스트 통과 | Diff 리뷰, 요구사항 충족 확인, E2E/회귀 테스트 시나리오 작성·검증 | 리뷰 리포트, `docs/HANDOFF.md` |
| **5. 통합·배포** | 리뷰 피드백 반영 및 PR 생성 | 문서 동기화 및 최종 체크리스트 확인 | PR (사람 최종 머지) |

---

## 3. 절대 규칙 (Hard Rules)

1. `main` 브랜치 직접 커밋·푸시 금지 (`git push --force`, `git reset --hard` 금지).
2. 다른 에이전트가 소유한 브랜치, worktree, `docs/TASKS.md` 항목을 임의로 수정하지 않는다.
3. 시크릿, `.env`, `.env.*`, 배포/서명 키(`AXCAD_PATCH_KEY`), 기밀 실도면은 읽거나 커밋·출력하지 않는다.
4. 아래 **공유 민감 파일**은 사람 승인 없이 수정하지 않는다:
   - 의존성: `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/pnpm-lock.yaml`
   - DB 스키마: `backend/db/migrations/**`, `alembic.ini`
   - CI/배포/보안: `.github/workflows/**`, `deploy/**`, `installer/**`
5. 테스트·린트·타입체크가 실패한 상태에서 "완료"라고 보고하지 않는다.
6. AX-CAD 고유 지침(`CLAUDE.md`, `HARNESS.md`, `DESIGN.md`, `.claude/rules/*.md`, `.claude/memory/MEMORY.md`)을 두 에이전트 모두 동일하게 준수한다.

---

## 4. 브랜치 및 파일 소유권 규칙

### 브랜치 네이밍
- **Claude Code**: `feat/claude-<task-id>`, `fix/claude-<task-id>`, `refactor/claude-<task-id>`
- **Antigravity**: `docs/antigravity-<task-id>`, `review/antigravity-<task-id>`, `test/antigravity-<task-id>`, (독립 구현 할당 시) `feat/antigravity-<task-id>`

### 기본 파일 소유권
| 경로 | Claude Code | Antigravity | 비고 |
|---|---|---|---|
| `core/**`, `backend/**` | 주 구현 소유 | 읽기·리뷰·테스트 제안 | OCCT/ezdxf/견적 코어 |
| `frontend/**` | 주 구현 소유 | 리뷰·UI/E2E 검증 (할당 시 전용 브랜치 구현) | `DESIGN.md` 준수 |
| `tests/**` | 단위·통합 테스트 | 테스트 시나리오·회귀/E2E 보강 | 동일 파일 동시 수정 금지 |
| `docs/**` | 구현 결과 반영 | 명세·리뷰·가이드·인계 주도 | `HANDOFF.md`는 턴제 갱신 |
| `pyproject.toml`, `frontend/package.json`, `backend/db/migrations/**`, `.github/workflows/**` | 사람 승인 후만 수정 | 수정 금지 | 공유 충돌 구역 |

---

## 5. 포트 및 실행 환경 분리

두 에이전트가 동시에 로컬 서버를 실행할 때는 `tools/agent-dev.ps1` 또는 아래 고정 포트를 사용한다.

| 모드 / 에이전트 | Backend (FastAPI) | Frontend (Next.js) | `AXCAD_API_ORIGIN` |
|---|---:|---:|---|
| **기본 단독 실행** (`AX-CAD_Start.bat`) | `8000` | `3000` | `http://localhost:8000` |
| **Claude Code 병렬 worktree** | `8100` | `3100` | `http://localhost:8100` |
| **Antigravity 병렬 worktree** | `8200` | `3200` | `http://localhost:8200` |

- 실행 예시:
  ```powershell
  # Claude Code worktree에서 실행
  .\tools\agent-dev.ps1 -Agent claude

  # Antigravity worktree에서 실행
  .\tools\agent-dev.ps1 -Agent antigravity
  ```
- **DB 주의**: `pytest`는 `TEST_DATABASE_URL` DB를 매 세션 초기화하므로, 두 에이전트가 동시에 `pytest`를 실행할 경우 별도 테스트 DB(예: `axcad_test` vs `axcad_test_ag`)를 지정하거나 순차 실행한다.

---

## 6. 완료 판정 및 검증 명령

변경 영역에 따라 커밋 전 아래 명령을 실행하고 결과를 보고한다.

```bash
# Backend / CAD Core 변경 시
uv run ruff check . && uv run ruff format --check .
uv run mypy core backend
uv run pytest -q tests/

# Frontend 변경 시
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend test
```

---

## 7. 작업 인계 형식 (`docs/TASKS.md` · `docs/HANDOFF.md`)

작업 시작 전 `docs/TASKS.md`에서 담당·허용 경로를 확인하고, 완료 시 아래 형식으로 요약해 인계한다.

```md
### TASK-XXX
- 담당: claude | antigravity
- 브랜치: feat/claude-xxx
- 상태: in-progress | review | done | blocked
- 허용/변경 범위: core/geometry/**, tests/test_xxx.py
- 검증 결과: ruff/mypy/pytest/frontend test 통과 여부
- 남은 리스크 / 다음 담당: antigravity-review | claude-fix | human-merge
```

---

## 8. 표준 프롬프트 템플릿

### Claude Code용 (구현)
```text
docs/AGENT_CONTRACT.md, docs/TASKS.md, docs/HANDOFF.md를 먼저 읽어.
TASK-XXX 작업을 담당해.

제약:
- 별도 Git worktree에서 작업해 (브랜치: feat/claude-xxx).
- 수정 가능: <허용 경로>
- 수정 금지: main 직접 푸시, 다른 에이전트 소유 작업, 사람 승인 없는 의존성/마이그레이션 변경
- 완료 기준: 구현 + 회귀 테스트 추가 + ruff/mypy/pytest(또는 frontend typecheck/lint/test) 통과
- 완료 후 docs/TASKS.md 상태 갱신 및 변경 요약·남은 리스크를 보고해.
```

### Antigravity용 (탐색·명세·리뷰)
```text
docs/AGENT_CONTRACT.md, docs/TASKS.md, docs/HANDOFF.md를 먼저 읽어.
TASK-XXX에 대한 설계 검증 및 리뷰 관점을 작성해.

제약:
- 코드 수정이 필요하면 New Worktree Mode에서 작업해 (브랜치: review/antigravity-xxx 또는 docs/antigravity-xxx).
- 수정 가능: docs/**, (합의 시) tests/**
- Claude Code가 소유한 구현 브랜치와 소스 파일은 수정하지 마.
- 산출물: 영향 범위, 경계 조건, 보안·성능 리스크, 수용 기준, 테스트 케이스 목록
- 완료 후 docs/TASKS.md 상태 갱신 및 인계 내용을 기록해.
```
