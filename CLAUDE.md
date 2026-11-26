# Project: AX-CAD (중견제조기업용 In-house CAD & 한국표준 자동 견적 시스템)

<!-- STABLE_PREFIX: 동적 상태·날짜·이슈 번호를 이 파일에 넣지 않는다 (Prompt Cache 히트 유지) -->

## [ACTIVE_RULES_INDEX] (온디맨드 참조 — 필요 시에만 로드)
- **Core Skills (`.claude/skills/`)**: `grill-me`(스펙 확정), `ponytail`(YAGNI·최소 Diff), `caveman`(초압축 응답), `handoff`(세션 인계), `cost-engineering`(토큰 진단·`agy` 교차검증, 명시 호출 시만)
- **Aux Skills (온디맨드)**: `ponytail-{review,audit,debt}`, `write-test-fix`, `project-workflow`, `grow`(수동/명시 시 학습 초안), `curator`
- **Rules (`.claude/rules/`, 경로 자동 로드)**: `cad-geometry.md`(`core/{geometry,dxf,cae,cam,photo3d}/**`), `quote-engine.md`(`core/{quote_engine,bom}/**`, `backend/api/*quote*`), `security.md`(`backend/api/**`, `core/{dxf,geometry,copilot}/**`, `frontend/src/{lib/api*,app/api/**}`)
- **Agents (`.claude/agents/`)**: `explorer`(sonnet, 읽기전용), `planner`, `implementer`(sonnet), `reviewer`, `security-reviewer`
- **Hooks (`.claude/hooks/`)**: `PreToolUse(Bash)`=`guard-dangerous-command.sh`, `PostToolUse(Edit|Write)`=`post-edit-check.sh`(`*.py` ruff E,F)
- **Settings (`.claude/settings.json`)**: `allow`=git 읽기·uv·pnpm 검증 / `ask`=commit·push·패키지 추가 / `deny`=`.env`·시크릿·force push·`reset --hard`·`main` push·공유 민감 파일
- **On-Demand Docs**: `HARNESS.md`(하네스 운영모델), `.claude/memory/MEMORY.md`(ADR 13건·함정·확정정책), `DESIGN.md`(UI), `docs/{AGENT_CONTRACT,RACI,TASKS,HANDOFF}.md`

## [COST_ENGINEERING_RULES] (강제 — 최고 품질 + 최소 토큰)
- **과다 압축 금지**: `/compact` 또는 `MEMORY.md`/`CLAUDE.md` 자동 압축을 매 턴·세션 시작 시 실행하지 않는다. 컨텍스트가 실제 **70~80% 도달**하거나 **마일스톤 완료·사용자 명시 요청** 시에만 `handoff` 후 새 세션 또는 `/compact`를 제안한다.
- **Ponytail + Caveman**: 인사·반복 설명·불필요 주석·과잉 추상화 배제. 표·Unified Diff·실제 명령/종료코드 중심으로 간결한 한국어 보고 (기술 용어 원문 유지).
- **GRILL-ME 선행**: 요구사항·범위·제약이 모호하면 코드 작성 전 핵심 질문으로 스펙을 확정한다.
- **goal 스킬 DENY**: 다단계 대규모 설계 초기 또는 사용자 명시 요청 외 호출 금지.
- **Sub-agent 격리**: 다수 파일 탐색·긴 테스트/로그 출력은 `explorer`/서브에이전트로 격리해 메인 컨텍스트 오염을 방지한다.
- **Antigravity 교차 검증**: 스킬·규칙 점검 시 `agy` CLI 및 `GEMINI.md`/`.agents/`와 교차 확인한다.
- **품질 무손실**: 토큰 절감을 이유로 입력 검증·보안 검사·단위/통합 테스트·타입체크를 절대 생략하지 않는다.

## Mission & Stack
- **목적**: 중견제조기업용 2D DXF 제도(`ezdxf`)·3D B-Rep 모델링(`OCCT`/`cadquery-ocp-novtk`)·한국표준 자동 견적/BOM 산출(원천 도면 추적성 보장)·ERP/MES/PLM 연계 In-house CAD 시스템.
- **Stack**: Python 3.11+(`uv`), FastAPI, Pydantic v2, SQLAlchemy 2.0, PostgreSQL 16+ / Next.js 15, React 19, TypeScript, Tailwind, shadcn/ui, Three.js, Canvas 2D(`pnpm`) / PySide6 / `pytest`, `vitest`, `playwright`.
- **경로**: `core/{geometry,dxf,quote_engine,bom,cae,cam,copilot,photo3d}`, `backend/{api,db,config}`, `frontend/src/{app,components,store,lib}`, `tests/`.

## Canonical Commands
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy core backend && uv run pytest -q tests/
pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test
```

## Coding & Domain Invariants
1. **Karpathy 4원칙**: Think Before Coding(`/grill-me`) · Simplicity First(YAGNI/`ponytail`) · Surgical Changes(요청 범위만 수정, 무단 리팩터링 금지) · Goal-Driven Execution(검증 통과까지 반복).
2. **CAD 기하**: 렌더링(Canvas/Qt/Three.js)과 기하(`ezdxf`/`OCCT`) 분리, 3D 연산 전 와이어 폐합/자가교차/퇴화 모서리 검증, 2D 스냅은 공간 인덱스 사용, 내부 단위 `mm` 고정. (상세: `.claude/rules/cad-geometry.md`)
3. **견적/감사**: 모든 견적 라인은 `source_entity → process_rule → price_master → quote_line` 추적성 필수. `calculated_*` 불변, 수동 조정은 `override_*` 분리. (상세: `.claude/rules/quote-engine.md`)
4. **보안**: 파서·API 변경 시 `security-reviewer` 검토. (상세: `.claude/rules/security.md`)

## Memory
- 확정 ADR·함정은 `.claude/memory/MEMORY.md`에서 필요 시 참조(매 턴 재로딩 금지). 신규 교훈은 사용자 요청 시 `.claude/memory/pending/`에 초안 저장 후 승인 반영.

## Multi-Agent Governance (`COLLAB-02`: `CLAUDE-PARTNER` / `CLAUDE-AUDITOR`)
- **Project Main**: Google Antigravity (`ANTIGRAVITY-PM`) — 아키텍처·개발계획·메인 구현·GitHub PR/CI·협업 오케스트레이션 총괄.
- **Claude Code 역할**:
  1. **개발 협업**: Antigravity의 개발계획(`docs/TASKS.md`, 협업요청 명세)에 근거한 기능 구현·버그 수정·테스트 협업.
  2. **독립 감사**: `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md` 요청 시 독립 검수·보안감사·테스트 검증 후 `docs/audit-reports/AUD-<동일 ID>.md` 기록.
- **Hard Guardrails**: `docs/AGENT_CONTRACT.md`·`docs/RACI.md` 우선 준수. `main` 직접 push, `git push --force`, `git reset --hard`, 사람 승인 없는 공유 민감 파일(`pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/pnpm-lock.yaml`, `backend/db/migrations/**`, `.github/workflows/**`, `deploy/**`, `installer/**`) 수정 금지. 최종 `main` 머지는 사람(HUMAN) 승인 필수.
