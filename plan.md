# AX-CAD 개발 계획 (plan.md)

> 중견제조기업용 In-house 2D/3D CAD + 한국표준 자동 견적 시스템
> 작성일: 2026-10-09 · 방법론: **Hybrid (Waterfall → Agile Sprint)** · 근거: `Research/` 14개 문서, `CLAUDE.md`, `DESIGN.md`, `.claude/rules/*`, `.claude/memory/MEMORY.md`

---

## 0. 요약

| 구분 | 방식 | 기간(예상) | 산출물 |
|---|---|---|---|
| **Phase W** 요구사항 · 아키텍처 · 3D 엔진 설계 | Waterfall (단계 게이트 G1~G3) | 6주 | SRS, 아키텍처 명세, ERD/DDL v1, OpenAPI v1, 3D 엔진 설계서 + PoC |
| **R1** 2D CAD | Agile S1~S4 (2주 스프린트) | 8주 | DXF 뷰어/작도/스냅/치수/Revision |
| **R2** 3D CAD | Agile S5~S7 | 6주 | Extrude/Revolve/Boolean, STEP·IGES, 3D 계측 |
| **R3** 견적 | Agile S8~S11 | 8주 | 메트릭 추출, 규칙 엔진, 원가 계산, 추적성, 견적서 PDF, 승인 |
| **R4** BOM·ERP 연계 + UAT | Agile S12 | 2주 | BOM, ERP REST 전송, 인수 테스트 |

상세 개발 문서(7단계 산출물)는 `docs/1~7.AX-CAD_*.md`에 있으며, 문서 지도·단계별 매핑·변경 통제 규칙은 [`docs/README.md`](docs/README.md)에서 관리한다. 아래 `docs/srs.md` 등의 산출물 경로는 해당 문서로 대체한다. 이 계획의 단계·스프린트·범위를 바꾸면 `docs/README.md` §2·§5도 함께 갱신한다.

총 약 **30주** (2026-10-12 착수 기준 → 2027-05 초 UAT 완료 예상). 순서는 사용자 지시대로 **2D → 3D → 견적**이다.
Research의 일부 문서(`P-바이브 코딩 구현 종합가이드`, `P-개발참조1`)는 BOM/Revision을 3D보다 앞에 두지만, 견적이 2D·3D 메트릭 모두를 입력으로 쓰므로 3D를 견적 앞에 둔다.

---

## 1. Research 분석 요약

### 1.1 문서별 핵심

| 문서 | 핵심 내용 | 계획 반영 위치 |
|---|---|---|
| G-CAD-Research, P-Research-CAD | OCCT 커널 + ezdxf, 커널 직접 개발 금지, DXF 우선·DWG는 후순위, 4단계 로드맵(뷰어→스냅→3D→연동), 바이브 코딩 시 "입력/출력/실패조건/테스트" 단위 지시 | 전체 순서, §5 작업 원칙 |
| P-CAD 시스템 아키텍처 | 3계층(클라이언트–CAD 엔진–기업시스템), Document Model 우선, 기업 연동 분리 | W2 아키텍처 |
| P-바이브 코딩 구현 종합가이드 | ERD 엔티티, 화면 목록, API 목록, 샘플 도면 3~5개 검증 원칙 | W2, 스프린트 백로그 |
| P-개발참조1·2·3 | 화면 설계서, PostgreSQL DDL/인덱스/트리거, API JSON 스키마(`success/data/error`), 단위 테스트 시나리오, OpenAPI, CI, RBAC 5역할 | W2 산출물, S1~S4 DoD |
| P-견적산정-확장1·2 | DXF 엔티티→견적 매핑표(LINE/LWPOLYLINE/CIRCLE/ARC/INSERT/TEXT), STEP Part/Assembly/부피/표면적, 견적 ERD(source_* / quote_* / trace), 규칙 JSON/YAML | S8~S9 |
| P-견적생성3·확장4·확장5 | FastAPI 구조, 뷰·트리거, 규칙 엔진, QuoteBuilder UI, TestClient 통합 테스트, ReportLab PDF + BackgroundTasks | S9~S11 |
| P-Design-System, P-shadcn/ui 템플릿 | FreeCAD 워크벤치 UX, 좌(구조)/중앙(캔버스)/우(속성)/하(상태), Zustand + TanStack Query, 견적 라인 ↔ 원천 엔티티 trace | 전 스프린트 UI (`DESIGN.md` 우선) |

### 1.2 Research 코드 초안의 결함 (그대로 복사 금지)

Research의 코드 초안은 "초안" 수준이다. 아래는 구현 시 반드시 수정할 항목이다.

| # | 위치 | 결함 | 조치 |
|---|---|---|---|
| D1 | 개발참조2 `fn_audit_log()` | `new.id`를 참조하지만 모든 테이블 PK가 `document_id` 등 → 런타임 오류 | `TG_ARGV`로 PK 컬럼명 전달 또는 `to_jsonb(new)->>pk` 사용 |
| D2 | 견적산정-확장2 `source_file.unique(file_hash)` | 같은 파일을 다른 견적 요청에 재사용 불가 | `unique(source_document_id, file_hash)`로 변경 |
| D3 | DXF 파서 초안 | CIRCLE을 "절단길이(2πr)"로 계산하면서 규칙은 "구멍 개수"로 사용, ARC/LWPOLYLINE 길이 미계산, `3.1415926535` 하드코딩 | 엔티티별 메트릭을 `length/area/count`로 분리, `math.pi`, ezdxf 기하 유틸 사용 |
| D4 | DXF 파서 초안 | modelspace의 INSERT만 1회 카운트하고 블록 내부를 전개하지 않음 → 수량 과소 산정 | `Insert.virtual_entities()`로 전개 + 중첩 블록 깊이·개수 상한 |
| D5 | 규칙 엔진 초안 | 매칭 실패 시 `qty=0` 라인 생성 → `quote_line.qty > 0` CHECK 위반 | 0 수량은 라인 생성 대신 validation_log WARN |
| D6 | 견적 엔진 초안 | 금액을 `float` + `round()`로 계산 | 금액·단가는 `Decimal`, 원 단위 절사/반올림 규칙을 기준정보로 명시 |
| D7 | PDF 초안 | ReportLab 기본 `Helvetica` → 한글 깨짐 | 한글 TTF(예: 나눔고딕, OFL) 등록 |
| D8 | STEP 파서 초안 | 하드코딩 stub | S7에서 OCCT 실측(`GProp`, `Bnd_Box`)으로 교체 |
| D9 | 업로드 API 초안 | 파일을 base64 JSON으로 전송 | `multipart/form-data`(`UploadFile`) + 크기/매직바이트/타임아웃 검증 |
| D10 | shadcn 템플릿 | HSL 토큰이 `DESIGN.md` hex 토큰과 다름 | `DESIGN.md`가 SoT, Research 토큰은 사용 안 함 |

### 1.3 문서 간 모순과 이 계획의 결정

| 쟁점 | Research | 이 계획의 결정 (가정) |
|---|---|---|
| 주 클라이언트 | 아키텍처 문서는 "데스크톱(Qt) 우선" | `CLAUDE.md`/ADR-03에 따라 **Web(Next.js) 주력**, PyQt 데스크톱은 범위 제외(후속) |
| 3D 연산 위치 | 웹은 "서버 CAD 서비스"로 | OCCT는 **백엔드(Python)**에서 실행, 웹은 테셀레이션 메시만 렌더링(Three.js) |
| OCCT 바인딩 | pythonocc-core 언급 | pythonocc-core는 주로 conda 배포라 `uv`와 충돌 가능 → **W3 스파이크에서 CadQuery/OCP(pip) vs pythonocc 비교 후 확정** |
| 견적 원가 공식 | Research 견적 문서엔 일반관리비/이윤/VAT 체계가 없음 (`.claude/rules/quote-engine.md`에만 있음) | 공식 구조는 rules 기준으로 채택하되, **비율 값(5~8%, 7~15%)은 사내 기준으로 W1에서 확정** |

---

## 2. 하이브리드 방법론

### 2.1 적용 원칙

```text
[Waterfall: 바뀌면 비싼 것]                 [Agile: 피드백으로 다듬을 것]
 W1 요구사항 ─G1─ W2 아키텍처 ─G2─ W3 3D 엔진 설계 ─G3─▶ S1 … S12 (2주 스프린트)
 · 모듈 경계 · 데이터 모델 · API 계약          · 2D/3D 세부 기능
 · OCCT 바인딩 · 기하 검증 파이프라인          · 견적 규칙·공식·UI
 · 보안 경계 · 단위계(mm)                     · 단가/공정 기준 반영
```

- **Waterfall 대상**: 나중에 바꾸면 전체가 흔들리는 결정. 문서 모델, DB 스키마 골격, API 응답 규격, 3D 커널 선택/형상 직렬화/검증 파이프라인, 보안 경계.
- **Agile 대상**: 사용자 피드백으로 다듬어야 하는 것. 작도 UX, 스냅 우선순위, 견적 규칙·단가 매핑, 견적서 양식.
- **연결 규칙**: G3에서 기준선(baseline)을 동결한다. 스프린트 중 기준선 변경은 **변경요청(CR)**으로만 하고 영향 분석을 거친 뒤 다음 스프린트 계획에 반영한다.

### 2.2 Waterfall 단계 게이트

| 게이트 | 통과 조건 | 승인자 |
|---|---|---|
| **G1** 요구사항 승인 | SRS 확정, 샘플 도면 확보(DXF ≥15, STEP ≥5), 사내 단가표·공정표·원가 비율 확보, 수락 기준 합의 | PO + 설계/견적 대표 |
| **G2** 아키텍처 승인 | 모듈 경계·문서 모델·ERD v1·DDL v1·OpenAPI v1·보안 설계 리뷰 완료, 레포/CI 골격 동작 | 아키텍트 + 보안 리뷰 |
| **G3** 3D 엔진 설계 승인 | OCCT 바인딩 확정, PoC로 Extrude→STEP→재import 부피 오차 ≤ 1e-6 상대오차, 실패 케이스(open wire, self-intersection) 검출 확인 | 아키텍트 + 기술 리드 |

### 2.3 Agile 스프린트 운영

- **주기**: 2주. Planning(1h) → Daily(15m) → Review(데모, 샘플 도면 기반) → Retro.
- **DoR (착수 조건)**: 입력/출력/실패조건/테스트케이스가 정의된 스토리 (Research "바이브 코딩" 원칙).
- **DoD (완료 조건)**:
  1. `uv run ruff check . && uv run ruff format --check .` 통과
  2. `uv run mypy core backend` 통과
  3. `uv run pytest -v tests/` 통과 (해당 영역 Verification Matrix 테스트 포함)
  4. 프론트 변경 시 `pnpm lint && pnpm typecheck && pnpm test` 통과
  5. 파서·업로드·인증 변경 시 `security-reviewer` 검토
  6. 샘플 도면 회귀 테스트 통과
- **역할 흐름**: `explorer` → `planner` → `implementer` → `reviewer` (+ `security-reviewer`).

---

## 3. Phase W — Waterfall (6주)

### W1. 요구사항 정의 (1.5주, 2026-10-12 ~ 10-21)

| 작업 | 산출물 |
|---|---|
| 사용자 역할별 업무 흐름 정의 (설계/생산·품질/견적 담당) | SRS (`docs/srs.md`) |
| 범위 확정: 포함/제외 (§8 참고) | 범위 명세 |
| 샘플 도면 수집·익명화: DXF ≥15 (판금/절곡/블록 반복 포함), STEP ≥5 (단품/조립품) | `tests/fixtures/` 원본 + 기대값 시트 |
| 견적 기준정보 수집: 재질·비중·단가, 공정별 표준공수·임률, 일반관리비율·이윤율, 절사 규칙, 견적서 양식 | 기준정보 시트 (S8 시드 데이터) |
| 수락 기준: 샘플별 기대 메트릭(절단길이, 구멍 수, 부피) 및 기대 견적 금액 | 수락 테스트 표 |

### W2. 시스템 아키텍처 설계 (2주, 10-22 ~ 11-04)

| 작업 | 산출물 / 근거 |
|---|---|
| 모듈 경계 확정: `core/dxf`, `core/geometry`, `core/quote_engine`, `core/bom`, `backend/api|db|services`, `frontend/` (`CLAUDE.md` 구조) | `docs/architecture.md` |
| **Document Model** 정의: Project→Document→Revision→Entity(handle 보존), 단위 mm 고정 | 아키텍처 문서 |
| ERD v1 통합: CAD(P-개발참조1) + 견적(P-견적산정-확장2) + 감사·연동, D1·D2 수정 반영 | `backend/db` Alembic 초기 마이그레이션 |
| API 규격: 공통 응답 `{success, data, error}`, 오류코드 체계, OpenAPI v1 (FastAPI `response_model`로 생성) | `docs/openapi.yaml` |
| 보안 설계: JWT, RBAC(ADMIN/DESIGNER/REVIEWER/MANUFACTURING/VIEWER), 업로드 검증(확장자·매직바이트·크기·타임아웃·블록 재귀 상한), 승인 전 export 차단 | `docs/security.md` |
| 레포 골격 + CI: `uv`/`pnpm` 초기화, ruff/mypy/pytest/vitest 실행, docker-compose(api+postgres16) | 동작하는 빈 앱 + CI 녹색 |

### W3. 3D 엔진 설계 (2.5주, 11-05 ~ 11-21)

| 작업 | 산출물 |
|---|---|
| OCCT 바인딩 스파이크: CadQuery/OCP vs pythonocc-core — `uv` 설치성, macOS/Linux 휠, 라이선스(LGPL) | 선택 ADR (`.claude/memory/pending/`) |
| 형상 서비스 인터페이스: `sketch → wire 검증 → face → prism/revol → boolean → shapefix → validate` | 3D 엔진 설계서 |
| 검증 파이프라인: `wire.IsClosed`, self-intersection, degenerate edge, `BRepCheck_Analyzer`, `ShapeFix` (`.claude/rules/cad-geometry.md`) | 설계서 + 실패 케이스 목록 |
| 형상 영속화: Feature 파라미터(JSON) + 결과 BREP/STEP 파일 (재생성 가능성 우선) | 저장 포맷 정의 |
| 렌더링 경로: 서버 테셀레이션 → 메시(JSON/glTF) → Three.js | 메시 규격 |
| 계측 API: 부피/표면적(`GProp_GProps`), BBox(`Bnd_Box`) — 견적 입력 계약 | 메트릭 스키마 |
| **PoC**: 사각 스케치 Extrude → STEP AP242 export → 재import → 부피 비교 | `tests/test_geometry.py` PoC 통과 → **G3** |

---

## 4. Agile 스프린트 백로그

### R1 — 2D CAD (S1~S4, 11-23 ~ 2027-01-15)

#### S1. DXF 가져오기 + 2D 뷰어
- **목표**: 샘플 DXF를 업로드해 웹 캔버스에 정확히 렌더링.
- **저장소 결정(2026-10-09 grill)**: S1은 DB 없이 파일 저장(`var/`)으로 진행하고 PostgreSQL·Alembic은 S4에서 도입한다(YAGNI). 업로드는 임시로 `POST /api/dxf`, 렌더는 `GET /api/revisions/{id}/render`(S4 이후에도 유지). 테스트 픽스처는 ezdxf로 합성 생성한다.
- **백로그**: `POST /api/dxf`(multipart, D9), ezdxf 파싱(modelspace/paperspace/blocks 구분), 레이어 추출, 렌더 데이터 API, Canvas2D 뷰어(커서 기준 줌, 팬), 레이어 on/off 패널, AppShell(`DESIGN.md` 토큰).
- **검증**: `pytest tests/test_dxf.py` — 샘플별 엔티티·레이어 수 일치, 손상 파일 거부, 파일 크기/확장자 위반 거부. vitest — 줌 후 커서 아래 월드 좌표 유지.

#### S2. 2D 작도 + 편집
- **목표**: 선/원/호/폴리라인 작도·수정 후 DXF로 저장.
- **백로그**: 도구 상태머신(선택→명령→대상→완료), 이동/복사/삭제, Undo/Redo(command 패턴), 속성 패널, DXF export(라운드트립).
- **검증**: 작도→저장→재로드 시 좌표 오차 ≤ 1e-6, Undo 후 원상 복구, 상태머신 전이 테스트.

#### S3. 스냅 엔진 + 치수
- **목표**: Endpoint/Midpoint/Center(+Grid) 정밀 스냅, 치수 기입.
- **백로그**: 공간 인덱스(그리드 버킷 우선, 성능 부족 시 R-tree), 스냅 후보 우선순위·반경(px→월드 변환), 스냅 마커 오버레이, 선형/각도 치수.
- **검증**: 반경 내 최근접점 선택, 후보 없을 때 원좌표 유지, 1만 엔티티에서 스냅 질의 < 5ms(벤치 테스트).

#### S4. Revision · 감사 · 하드닝
- **목표**: 저장 시 Revision 생성과 감사 로그, 업로드 보안 완료.
- **백로그**: PostgreSQL 16 + Alembic 도입(S1 파일 저장소 이전), 프로젝트/도면 목록, Revision 생성·current_revision 갱신(트리거 D1 수정본), audit_log, JWT 로그인 + RBAC, XREF·재귀 블록·타임아웃 방어.
- **검증**: 트리거 테스트(TestClient + 테스트 DB), 권한 없는 사용자 403, 악성 DXF 샘플 거부. `security-reviewer` 검토.
- **R1 릴리스 리뷰**: 설계 담당자 대상 데모 + 피드백 반영 백로그 갱신.

### R2 — 3D CAD (S5~S7, 2027-01-18 ~ 02-26)

#### S5. 스케치 → Extrude + 3D 뷰어
- **백로그**: 2D 폐곡선 선택 → 사전검증 → Extrude, 실패 사유 UI 표시, Three.js 뷰포트(회전/줌/팬), Feature Tree 패널.
- **검증**: `pytest tests/test_geometry.py` — 열린 와이어·자가교차 거부, 사각형 Extrude 부피 = w·h·d (상대오차 ≤ 1e-6).

#### S6. Revolve + Boolean
- **백로그**: Revolve(축 지정), Fuse/Cut/Common + ShapeFix, Feature 파라미터 수정 시 재생성.
- **검증**: 원통 Revolve 부피 = πr²h, Cut 후 부피 감소량 일치, 접촉면 일치 케이스에서 크래시 없이 검증 오류 반환.

#### S7. STEP/IGES 교환 + 3D 계측
- **백로그**: STEP AP242 / IGES import·export(XDE: 이름·조립구조 보존), Part/Assembly 트리, 부피·표면적·BBox 계측 API (D8 해소).
- **검증**: export→import 부피·면적 보존, 샘플 STEP 5종 파트 수·조립 구조 일치.
- **R2 릴리스 리뷰**.

### R3 — 견적 시스템 (S8~S11, 2027-03-02 ~ 04-23)

#### S8. 메트릭 추출 + 기준정보
- **백로그**: 2D 메트릭(외곽 절단길이, 구멍 수·직경, 절곡선 수·길이(레이어/선종 규칙), INSERT 반복 전개 D4, 폐곡선 면적), 표제란 TEXT/ATTRIB에서 품번·품명·재질·수량 추출, 3D 메트릭(S7 API 재사용), 기준정보 테이블(`material_master`, `process_rule`, `price_master`) + W1 시드.
- **검증**: `pytest tests/test_quote.py` — 샘플별 기대 메트릭 표(W1)와 일치.

#### S9. 규칙 엔진 + 한국표준 원가 계산
- **백로그**: 규칙 YAML 로더(버전 관리), 엔티티→공정→단가 매핑, 원가 체계:
  - 직접재료비 = 정미중량 × 단가 × (1 + 스크랩률)
  - 직접노무비 = 표준공수(M/H) × 임률
  - 제조간접비, 일반관리비(비율), 이윤(비율), VAT 10%
  - `Decimal` 금액(D6), 0 수량은 WARN(D5), `quote_trace` 생성(Source Entity → Rule → Price → Line)
- **검증**: 수기 계산 기준 견적 3건과 원 단위 일치, 모든 라인에 trace 존재, 단가 누락 시 ERROR 로그.

#### S10. 견적 UI + 수동 조정 + 검증
- **백로그**: Quote 화면(좌: 원천 파일 / 중: QuoteLineTable / 우: 요약·검증·TraceabilityDrawer), 라인 클릭 시 캔버스 엔티티 하이라이트, 수동 조정(`calculated_value` 보존 + `override_value` + `override_reason`), `POST /quote/{id}/validate`(누락 단가·중복·불일치).
- **검증**: override 후 원본값 불변(DB 테스트), trace 하이라이트 컴포넌트 테스트, 검증 규칙 단위 테스트.

#### S11. 견적서 출력 + 승인 + 견적 Revision
- **백로그**: 한국표준 견적서 PDF(공급자·공급받는자·품명·규격·수량·단가·공급가액·세액, 한글 폰트 D7, 산출근거 별첨), Excel 내보내기, 승인 워크플로(승인 전 export 차단), quote_revision.
- **검증**: PDF 생성 테스트(텍스트 추출로 합계·한글 확인), 미승인 견적 export 403.
- **R3 릴리스 리뷰**: 견적 담당자 대상 실제 견적 3건 병행 비교.

### R4 — BOM · ERP 연계 + UAT (S12, 2027-04-26 ~ 05-07)

- **백로그**: 도면/STEP 조립구조 기반 BOM 생성(품번 매핑), BOM CSV/JSON, ERP 전송 REST 어댑터 + `integration_job`(재시도·실패 목록), UAT.
- **검증**: BOM 수량 = INSERT 전개 수량, 전송 payload 스키마 테스트, UAT 체크리스트 통과.

---

## 5. 공통 엔지니어링 원칙 (전 스프린트)

1. **렌더링과 기하 분리**: 캔버스/Three.js 코드에 기하 연산 금지. 모든 기하는 `core/`에서 수행하고 pytest로 검증.
2. **AI 보조 개발 단위**: 기능마다 "입력·출력·실패조건·테스트케이스"를 먼저 쓰고 구현 (Research 공통 권고).
3. **샘플 도면 회귀**: W1에서 만든 기대값 표를 모든 스프린트의 회귀 테스트로 사용.
4. **단위**: 내부 mm, 각도는 명시적으로 rad/deg 변환.
5. **추적성**: `quote_line`은 반드시 `source_entity_id` 또는 `source_part_id` 참조.
6. **최소 구현**: 요청되지 않은 추상화·의존성 금지. 의도적 단순화는 `ponytail:` 주석.

---

## 6. 마일스톤

| 마일스톤 | 예정일 | 판정 기준 |
|---|---|---|
| G1 요구사항 승인 | 2026-10-21 | §2.2 |
| G2 아키텍처 승인 | 2026-11-04 | §2.2 |
| G3 3D 엔진 설계 승인 | 2026-11-21 | PoC 통과 |
| R1 2D CAD | 2027-01-15 | CLAUDE.md 완료기준 1 |
| R2 3D CAD | 2027-02-26 | CLAUDE.md 완료기준 2 |
| R3 견적 | 2027-04-23 | CLAUDE.md 완료기준 3 |
| R4 연계 + UAT | 2027-05-07 | CLAUDE.md 완료기준 4 |

※ 연말연시 휴무는 반영하지 않았다. 반영 시 R1 이후 1~2주씩 순연된다.

---

## 7. 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| OCCT 바인딩 설치·빌드 문제 (`uv` 호환) | R2 전체 지연 | W3 스파이크를 첫 주에 수행, 실패 시 conda 기반 별도 지오메트리 워커로 격리 |
| 사내 단가·공수 기준 미확보 | R3 정확도 확보 불가 | G1 통과 조건에 포함, 미확보 시 R3 착수 보류 |
| 실도면의 비표준 레이어/블록 관행 | 메트릭 오추출 | 레이어·선종 매핑을 규칙 YAML로 설정화, 샘플 확대 |
| 웹 Canvas2D 대형 도면 성능 | 뷰어 반응성 저하 | S1에서 최대 샘플로 측정, 필요 시 뷰포트 컬링/WebGL 전환 |
| AI 생성 코드의 기하 오류 | 잘못된 견적 | 기하·금액은 항상 수치 테스트로 확정 (Research 리스크 항목) |
| 오픈소스 라이선스 (OCCT LGPL, ezdxf MIT) | 배포 제약 | W2에서 사내 배포 형태 기준 검토 |

---

## 8. 범위 제외 (후속 백로그)

다음은 Research에 언급되었지만 이번 계획 범위에서 뺐다. 필요가 확인되면 추가한다.

- DWG 읽기/쓰기 → DXF로 변환해 처리. 필요 시 별도 변환 계층 추가.
- Loft/Shell, 구속조건 솔버(Coincident/Parallel 등).
- PMI/GD&T 기반 품질비, AI 기반 문자/블록 의미 추출, 유사 견적 검색.
- PyQt 데스크톱 클라이언트.
- Kubernetes/Helm. 우선 docker-compose로 운영하고, 사내 인프라 요구가 생기면 추가.
- MES/PLM/QMS 실연동, SSO/LDAP, MFA.

---

## 9. 착수 전 확인 필요 항목 (미확인 · 가정)

1. **주 클라이언트**: Web(Next.js) 주력, 데스크톱 제외 — 가정 (§1.3).
2. **원가 비율·절사 규칙**: 일반관리비율, 이윤율, 원 단위 처리 — 사내 기준 필요.
3. **견적서 양식**: 사내 표준 양식 원본(PDF/Excel) 제공 여부.
4. **대상 공정 범위**: 판금(레이저/NCT/절곡) 우선인지, CNC 절삭 포함인지 — 3D 공수 규칙 범위가 달라진다.
5. **ERP 제품/인터페이스**: R4 어댑터 대상 (REST/DB/파일).
6. **팀 규모**: 일정은 풀스택 2~3명 + AI 보조 기준으로 추정했다.
