# Project: AX-CAD (중견제조기업용 In-house CAD & 한국표준 자동 견적 시스템)

상위 하네스 지침(@HARNESS.md)을 따르며, 이 파일은 AX-CAD 프로젝트의 고유 사실, 아키텍처, 표준 명령 및 엔지니어링 규칙을 정의한다.

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
- **3계층 구조**: 클라이언트(Desktop Qt / Web UI) ↔ CAD 엔진(Geometry / DXF / 견적 서비스) ↔ 기업 시스템(ERP / MES / PLM)
```text
AX-CAD/
├── core/
│   ├── geometry/        # OCCT 기반 3D 솔리드 연산, B-Rep, Boolean, STEP/IGES 입출력
│   ├── dxf/             # ezdxf 기반 2D 엔티티 파싱, 레이아웃, 스냅, DXF 입출력
│   ├── quote_engine/    # 한국표준 견적 산출 규칙(재료비/가공비/경비), 공정 매핑, 산출근거 추적
│   └── bom/             # 파트 트리, 규격품 매칭, BOM 구조 생성 및 변환
├── backend/
│   ├── api/             # FastAPI 엔드포인트 (도면 업로드, 견적 생성, ERP 연동)
│   ├── db/              # PostgreSQL DDL, 모델, 마이그레이션 (Alembic)
│   └── services/        # 도면 버전 관리, 승인 워크플로, 감사 로그
├── frontend/
│   ├── src/app/         # Next.js App Router (프로젝트, 도면, 3D 모델, 견적 대시보드)
│   ├── src/components/  # shadcn/ui 쉘, CAD 캔버스, 툴 팔레트, 견적 빌더/테이블
│   └── src/store/       # Zustand / TanStack Query (CAD 뷰포트 상태 및 견적 상태)
├── desktop/             # PyQt5 기반 데스크톱 독립형 클라이언트 (옵션)
├── tests/               # pytest 단위/통합 테스트 (도면 샘플 3~5개 기반 검증)
└── Research/            # 요구사항, 시스템 아키텍처, 디자인 시스템 및 견적 연구 문서
```

## Canonical Commands
```bash
# [Python Backend & CAD Engine]
# 환경 설치 (uv 기준)
uv sync
# 포맷 및 린트 검사
uv run ruff check . && uv run ruff format --check .
# 정적 타입 검사
uv run mypy core backend
# 단위 테스트 (CAD 기하연산 및 견적 산출)
uv run pytest -v tests/
# 백엔드 서버 실행
uv run uvicorn backend.api.main:app --reload --port 8000

# [Frontend Web UI]
# 의존성 설치
pnpm install
# 린트 및 포맷
pnpm lint
# 타입 검사
pnpm typecheck
# UI 테스트
pnpm test
# 개발 서버 실행
pnpm dev
# 빌드
pnpm build
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
| 변경 영역 | 필수 검증 항목 | 검증 방법 |
|---|---|---|
| **2D DXF 처리** | DXF 파싱 무결성, 레이어 추출, 스냅 좌표 정확도 | 샘플 DXF(선/원/치수 포함) 대상 `pytest tests/test_dxf.py` |
| **3D OCCT 모델링** | B-Rep 생성 유효성, Boolean 연산, STEP 입출력 | `pytest tests/test_geometry.py` 형상 부피/면적 오차 검증 |
| **견적 산출 엔진** | 재료비/가공비/외주비/경비 계산, 단가표 매핑, 수량 오차 | `pytest tests/test_quote.py` 기준 견적서 일치 테스트 |
| **데이터베이스 / API** | PostgreSQL DDL 일관성, API Response JSON 규격 | 스키마 마이그레이션 확인 및 FastAPI TestClient 응답 검증 |
| **UI 컴포넌트** | 캔버스 반응성, 도구 상태 머신, 견적 테이블 렌더링 | `pnpm test` 또는 컴포넌트 마운트 검증 |
| **보안 및 파일 검증** | 악의적 DXF/STEP 업로드 차단, 경로 탈출 방지 | `security-reviewer` 검토 및 파일 형식 화이트리스트 검사 |

## Memory
- 프로젝트 고유 사실과 아키텍처 결정(ADR)은 `.claude/memory/MEMORY.md`에 유지한다.
- 세션 시작 시 `.claude/memory/MEMORY.md`를 필독하여 기 확정된 규격과 규칙을 재확인하지 않는다.
- 새로운 교훈이나 규격 변경은 `.claude/memory/pending/`에 초안을 남기고 사용자 승인 후 반영한다 (`grow` 스킬 준수).
