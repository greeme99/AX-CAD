# Project: AX-CAD (중견제조기업용 In-house CAD & 한국표준 자동 견적 시스템)

상위 하네스 지침(@HARNESS.md)을 따르며, 이 파일은 AX-CAD 프로젝트의 고유 사실, 아키텍처, 표준 명령 및 엔지니어링 규칙을 정의한다.

## [ACTIVE_RULES_INDEX] — 스킬·규칙·에이전트·훅·설정 레지스트리

### Skills (`.claude/skills/`, 온디맨드 호출)
| 스킬 | 트리거 | 역할 |
|---|---|---|
| **ponytail** | `/ponytail`, "게으르게", "최소한으로" | YAGNI 사다리 강제 (lite/full/ultra) |
| **ponytail-review** | `/ponytail-review`, "과잉 구현 리뷰" | diff 대상 복잡성 사냥 |
| **ponytail-audit** | `/ponytail-audit`, "저장소 감사" | 전체 코드베이스 과잉 구현 스캔 |
| **ponytail-debt** | `/ponytail-debt`, "포니테일 부채" | `ponytail:` 주석 장부 수집 |
| **grill-me** | `/grill-me`, 모호한 요구사항 | 구현 전 범위·제약·성공 기준 확정 |
| **grow** | 비단순 작업 완료 후 자동 | 메모리·스킬 학습루프 (pending→승인) |
| **curator** | `/curator`, 주 1회 예약 | 미사용 agent-created 스킬 정리 |
| **project-workflow** | `/project-workflow` | 프로젝트 설치·테스트·빌드 절차 |
| **write-test-fix** | `/write-test-fix`, 버그 수정 | 재현 테스트 → 최소 수정 → 회귀 검증 |
| **cost-engineering** | `/cost-engineering`, "토큰 최적화" | CLAUDE.md·메모리·설정·규칙·훅 토큰 비용 진단·압축 |

### Rules (`.claude/rules/`, 경로 매칭 시 자동 로드)
| 규칙 | 대상 경로 | 내용 |
|---|---|---|
| **cad-geometry.md** | `core/geometry/**`, `core/dxf/**` | 2D/3D 기하·렌더링 분리·스냅·단위 |
| **quote-engine.md** | `core/quote_engine/**`, `**/quote*/**` | 한국표준 원가 체계·추적성·감사 |
| **security.md** | `backend/api/**`, `core/**/*.py`, `frontend/src/lib/api*` | CAD 파일 파싱·API·시크릿 보안 |

### Agents (`.claude/agents/`, 서브에이전트 위임)
| 에이전트 | 모델 | 도구 | 역할 |
|---|---|---|---|
| **explorer** | sonnet | Read,Grep,Glob,Bash | 읽기 전용 코드베이스 조사 |
| **planner** | — | Read,Grep,Glob | 최소 변경 계획 수립 |
| **implementer** | sonnet | Read,Edit,Write,Grep,Glob,Bash | 승인된 계획 실행 |
| **reviewer** | — | Read,Grep,Glob,Bash | 구현 결과 독립 검토 |
| **security-reviewer** | — | Read,Grep,Glob | 인증·입력·시크릿 보안 검토 |

### Hooks (`.claude/hooks/`)
| 훅 | 시점 | 동작 |
|---|---|---|
| **guard-dangerous-command** | PreToolUse(Bash) | `rm -rf /`, force push 등 위험 명령 차단 |
| **post-edit-check** | PostToolUse(Edit\|Write) | Python 파일 편집 시 ruff E,F 검사 |

### Settings (`.claude/settings.json`)
- **allow**: git 기본(status/diff/log/add/commit/push -u/fetch/merge/stash/branch/checkout/rebase), uv(pytest/ruff/mypy/sync), pnpm(test/lint/typecheck/build/install/dev)
- **deny**: `.env`/시크릿 읽기, `git push --force`, `git reset --hard`

### Memory
- **MEMORY.md**: ADR 13건 + 주요 함정 요약 + 확정 정책
- **pending/**: 승인 대기 초안 (`grow` 스킬이 생성)

## Mission
- **목적**: 중견제조기업을 위한 2D 제도, 3D 모델링 및 CAD 도면(DXF/STEP) 기반 한국표준 자동 견적·BOM 산출 사내 In-house CAD 시스템 구축
- **주요 사용자**: 설계·기술 엔지니어, 생산·품질 관리자, 영업·원가 견적 담당자
- **완료 기준**:
  1. ezdxf 기반 2D 제도(선/원/호/폴리라인/치수/레이어) 및 고정밀 스냅 엔진(Endpoint, Midpoint, Center) 동작
  2. OpenCASCADE(OCCT) 기반 3D B-Rep 모델링(Extrude, Revolve, Boolean) 및 STEP(AP242)/IGES 교환
  3. 도면 형상(절단길이, 면적, 부피, 가공공정) 기반 한국표준 견적서 자동 산출 및 원천 도면 객체 추적성(Traceability) 보장
  4. ERP/MES/PLM 연계 표준 REST API 인터페이스 및 PostgreSQL 기반 버전 관리 제공

## Stack
- **CAD Core / Geometry**: Python 3.11+, ezdxf (2D DXF 처리), OpenCASCADE (OCCT / CadQuery / PythonOCC), libdxfrw
- **API Backend**: FastAPI, Pydantic v2, SQLAlchemy 2.0 / SQLModel
- **Database**: PostgreSQL 16+ (도면 메타데이터, B-Rep 엔티티, BOM 계층, 견적 이력, 감사 로그)
- **Frontend (Web)**: Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS, shadcn/ui, Three.js / Canvas 2D
- **Desktop Client**: PyQt5 / PySide6 (대형 도면 및 고성능 기하 렌더링용 데스크톱 클라이언트)
- **Package Manager**: `uv` 또는 `poetry` (Python), `pnpm` (Node.js)
- **Testing**: `pytest` (단위 및 CAD/견적 계산 테스트), `vitest` / `playwright` (UI 테스트)

## Architecture
- **3계층**: 클라이언트(Web/Desktop) ↔ CAD 엔진(`core/`) ↔ 기업 시스템(ERP/MES/PLM)
- **주요 경로**: `core/{geometry,dxf,quote_engine,bom}`, `backend/{api,db,services}`, `frontend/src/{app,components,store}`, `tests/`, `Research/`

## Canonical Commands
```bash
uv sync                                              # Python 환경 설치
uv run ruff check . && uv run ruff format --check .  # 린트+포맷
uv run mypy core backend                             # 타입 검사
uv run pytest -v tests/                              # 단위 테스트
uv run uvicorn backend.api.main:app --reload --port 8000
pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build
pnpm dev                                             # 프런트 개발 서버
```

## Coding Rules
1. **Core Engineering Behavior (Karpathy 4원칙)**:
   - **Think Before Coding**: 요구사항이 모호할 경우 임의 판단하지 않고 `/grill-me` 절차로 성공 기준 확정.
   - **Simplicity First**: 투기적 추상화 금지. YAGNI 원칙 준수 (`ponytail` 과잉 구현 억제 준수).
   - **Surgical Changes**: 요청된 범위만 수정. 기존 코드의 무단 리팩터링 금지.
   - **Goal-Driven Execution**: 검증 가능한 성공 기준을 정의하고 통과할 때까지 점진적 루프 수행.
2. **CAD 기하 연산 원칙**:
   - 렌더링(Canvas/Qt/Three.js)과 기하 로직(ezdxf/OCCT)을 명확히 분리한다.
   - 3D 연산 시 열린 와이어(open wire), 자가교차(self-intersection), 퇴화 모서리(degenerate edge)에 대한 사전 검증 및 예외처리를 반드시 수행한다.
   - 2D 스냅 엔진은 부하를 방지하기 위해 마우스 이벤트 처리 시 공간 인덱스(R-tree 또는 그리드 버킷)를 사용한다.
3. **견적 엔진 원칙**:
   - 모든 견적 산출 항목은 "원천 도면 객체(Source Entity) → 공정/규칙(Process Rule) → 단가(Price Master) → 견적 라인(Quote Line)"으로 이어지는 추적성(Traceability) 데이터를 필수로 남긴다.
   - "자동 산출 데이터"와 "사용자 수동 조정값"은 컬럼을 분리하여 감사(Audit) 가능성을 유지한다.
4. **한국어 및 소통 규칙**:
   - 결과 보고는 간결한 한국어로 작성하며 핵심 데이터와 로직 위주로 기술한다.

## Verification Matrix
- **DXF/기하/견적**: `pytest tests/test_dxf.py test_geometry.py test_quote.py` (형상·금액 오차)
- **DB/API**: 마이그레이션 + FastAPI TestClient 응답 검증
- **UI**: `pnpm test` · 브라우저 확인 · **보안**: 파서·API 변경 시 `security-reviewer`

## Memory
- 프로젝트 고유 사실과 아키텍처 결정(ADR)은 `.claude/memory/MEMORY.md`에 유지한다.
- 세션 시작 시 `.claude/memory/MEMORY.md`를 필독하여 기 확정된 규격과 규칙을 재확인하지 않는다.
- 새로운 교훈이나 규격 변경은 `.claude/memory/pending/`에 초안을 남기고 사용자 승인 후 반영한다 (`grow` 스킬 준수).
