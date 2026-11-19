# SPEC — CAM · CAE (`plan.md` §24) 상세 구현 명세서

> **작성자**: Antigravity (2026-11-20) | **상태**: 의사결정 6건 사용자 확정 완료 (`DEC-CAM-01~04` = A, `DEC-CAE-01~02` = A)
> **선행 조건**: `plan.md` §23.10 완료 (곡면 리뷰 `TASK-005` · 곡면 UI `TASK-003` · 조립 A1~A4 `TASK-002`, 마이그레이션 `0035`~`0037`)
> **구현 담당**: Claude Code (C1→C4, E1→E3 단계별 PR) | **리뷰·검증**: Antigravity

---

## 1. 확정 의사결정 요약

| ID | 결정 | 확정 내용 |
|---|---|---|
| **DEC-CAM-01** | **A** | 착수 순서: **C1(공구·2.5축 밀링) → C2(3D 인식·시뮬) → C3(후처리·견적) → C4(레이저)** |
| **DEC-CAM-02** | **A** | 후처리기: **Fanuc 일반형 밀링** (`G0/G1/G2/G3/G81/M3/M5/M30`) + **일반 레이저** (`M3/M5` 파워 제어) 2종 |
| **DEC-CAM-03** | **A** | 공구·절삭조건 마스터: 기존 **G1 엑셀 `17_공구절삭조건` 시트 추가 + `ACTIVE` 버전 동결** (감사·견적 일관성 유지) |
| **DEC-CAM-04** | **A** | 견적 연계: **선택적 연동** — CAM 작업이 있으면 실경로 가공시간 사용 + `quote_trace.source_type = 'CAM_TOOLPATH'`, 없으면 기존 `machining.py` 추정치 유지 |
| **DEC-CAE-01** | **A** | FEA 스택: **하이브리드 내장** — `scipy` (BSD-3, ~35MB, `uv.lock` R3.0 `UPGRADE` 1회) 희소 선형 솔버 + 자체 Tet4 선형 정적 솔버(`core/cae/fea.py`), GPL 비감염 유지 |
| **DEC-CAE-02** | **A** | CAE 범위: **단품 선형 정적 FEA**(변위·von Mises·안전율)만 구현, CFD·비선형·접촉·열·모달 제외 |

---

## 2. 데이터베이스 마이그레이션 설계 (조립 `0035`~`0037` 이후)

### `0038_cam_master_and_setups.py`
1. **`cam_tools`** (기준정보 버전 종속, `ACTIVE` 동결 트리거 적용):
   - `tool_id` (PK), `version_id` (FK `master_versions.version_id`), `tool_code` (UNIQUE per version, `T01`~`T99`), `tool_type` (`ENDMILL_FLAT`, `ENDMILL_BALL`, `DRILL`, `CHAMFER`, `FACEMILL`, `LASER_NOZZLE`), `diameter_mm` (`NUMERIC(8,3) > 0`), `flutes` (`SMALLINT >= 1`), `material_group` (`VARCHAR(32)`), `spindle_rpm` (`INT CHECK (100 <= spindle_rpm <= 60000)`), `feed_xy_mm_min` (`NUMERIC(10,2) > 0`), `feed_z_mm_min` (`NUMERIC(10,2) > 0`), `stepdown_mm` (`NUMERIC(8,3) > 0`), `stepover_ratio` (`NUMERIC(4,3) CHECK (0.05 <= stepover_ratio <= 0.95)`), `laser_power_pct` (`NUMERIC(5,2)` NULLABLE).
2. **`cam_setups`** (문서별 CAM 작업 정의):
   - `setup_id` (PK), `document_id` (FK `documents`), `feature_id` (FK `features`, NULLABLE — 2D 레이저는 `revision_id` 참조), `revision_id` (FK `revisions`, NULLABLE), `kind` (`MILL_25D`, `LASER_2D`), `post_target` (`FANUC_MILL`, `GENERIC_LASER`), `wcs_origin` (`JSONB` `[x,y,z]`), `safe_z_mm` (`NUMERIC(8,2) DEFAULT 20`), `stock_margin_mm` (`NUMERIC(8,2) DEFAULT 0`), `created_by`, `created_at`.
3. **`cam_operations`**:
   - `operation_id` (PK), `setup_id` (FK `cam_setups` ON DELETE CASCADE), `seq` (`INT`), `op_type` (`CONTOUR`, `POCKET`, `DRILL`, `LASER_CUT`), `tool_code` (`VARCHAR(32)`), `params` (`JSONB`), `status` (`PENDING`, `OK`, `ERROR`), `error_code`, `cycle_time_s` (`NUMERIC(12,2)`), `cut_length_mm` (`NUMERIC(12,2)`), `rapid_length_mm` (`NUMERIC(12,2)`), `toolpath_path` (저장소 상대 경로 — `var/cam/<setup_id>/<op_id>.json`).
4. **`quote_trace` 확장**: `source_type` CHECK에 `'CAM_TOOLPATH'` 추가 (downgrade 시 기존 `CAM_TOOLPATH` 행 존재하면 거부 — 감사 증적 보존 규칙 준수).

### `0039_cae_studies.py`
1. **`cae_studies`**:
   - `study_id` (PK), `document_id` (FK `documents`), `feature_id` (FK `features`), `material_code` (`VARCHAR(32)`), `youngs_modulus_mpa` (`NUMERIC(12,2) > 0`), `poissons_ratio` (`NUMERIC(5,4) CHECK (0 < v < 0.495)`), `yield_strength_mpa` (`NUMERIC(10,2) > 0`), `mesh_size_mm` (`NUMERIC(8,3) > 0`), `restraints` (`JSONB` — `FaceRef[]` 고정면), `loads` (`JSONB` — `[{type: 'FORCE'|'PRESSURE', face_ref: FaceRef, vector_n?: Vec3, magnitude_mpa?: float}]`), `status` (`PENDING`, `SOLVED`, `ERROR`), `error_code`, `node_count`, `element_count`, `max_disp_mm`, `max_von_mises_mpa`, `min_safety_factor`, `result_path` (`var/cae/<study_id>/result.json`), `created_by`, `created_at`.

---

## 3. CAM 상세 명세 (C1 ~ C4)

### C1. 공구·절삭조건 마스터 + 2.5축 밀링 (`core/cam/tools.py`, `core/cam/mill25d.py`)
- **G1 가져오기**: `core/quote_engine/g1_excel.py`에 선택 시트 `17_공구절삭조건` 파서 추가(시트 미존재 시 기존 G1 호환 유지).
- **2.5축 경로 알고리즘 (`mill25d.py`)**:
  - `contour(loops, tool, depth, stepdown, side="OUTSIDE"|"INSIDE", lead_in_r)`: 2D 폴리곤 오프셋(`±R_tool`), 다중 Z 패스, 아크 리드인/리드아웃.
  - `pocket(outer, islands, tool, depth, stepdown, stepover)`: 등고선 평행 오프셋(inward offset 반복) + 섬(island) 회피 + 미절삭 잔여 영역 0 보장.
  - `drill(points, tool, depth, peck_mm=None)`: G81/G83 드릴 사이클 + 최근접 이웃(nearest-neighbor) 이동 경로 최적화.
- **시간 산출**: `cycle_time_s = (cut_length / feed_xy + plunge_length / feed_z + rapid_length / RAPID_MM_MIN) * 60` (`Decimal` 반올림은 견적 연계 시 적용).
- **하드 리소스 상한**: `MAX_SEGMENTS = 500_000`, `MAX_Z_PASSES = 500`, `TIMEOUT = 60s` (별도 워커 프로세스 실행).

### C2. 3D 피처 자동 인식 + 시뮬레이션 (`core/cam/recognize.py`, `CamPanel.tsx`)
- **피처 인식 (`recognize.py`)**: OCCT `TopoDS_Shape`에서 Z축 수직 원통면(관통/막힌 구멍: 중심·지름·깊이) 및 평면 바닥 포켓(폐곡선 경계·깊이)을 자동 추출해 `cam_operations` 초안 생성.
- **프론트 3D 시뮬레이션 (`frontend/src/components/cad/CamPanel.tsx`)**:
  - `ThreeViewport.tsx` 오버레이로 절삭 경로(실선, `--color-primary`)·급속 이송(점선, `--color-warning`) 렌더링.
  - 재생/일시정지·진행 슬라이더(0~100%)·공구 마커 표시.
  - **WebGL 클린업 필수**: 언마운트/재계산 시 `BufferGeometry`·`LineBasicMaterial`·`LineDashedMaterial` 모두 `dispose()`.

### C3. 후처리기 + 견적 연계 (`core/cam/post.py`, `backend/api/routes_cam.py`)
- **후처리기 2종**:
  - `FANUC_MILL`: `G90 G17 G21 G40 G49 G80`, `T{n} M06`, `S{rpm} M03`, `G00`/`G01`/`G02`/`G03`/`G81`, `M05`, `M30`.
  - `GENERIC_LASER`: `G90 G21`, `M03 S{power}`, `G01`/`G02`/`G03`, `M05`, `M30`.
- **안전·보안 가드레일**:
  1. 모든 출력 파일 최상단에 `(AX-CAD SIMULATION/DRAFT ONLY - VERIFY ON DRY-RUN BEFORE MACHINING)` 고정 삽입.
  2. 프로그램명·주석에서 제어문자(`\r`, `\n`, `\x00`~`\x1f`, `(`, `)`) 제거/거부 (G코드 명령 주입 차단).
  3. `spindle_rpm`, `feed_xy`, `feed_z` 상한 검증 및 `NaN`/`Inf` 차단(`allow_inf_nan=False`).
- **견적 연계 (`core/quote_engine/cost.py`)**:
  - 문서에 유효(`status='OK'`)한 `cam_setups`가 있으면 해당 공정의 `cycle_time_s` 합계를 가공 시간으로 사용하고 `quote_trace`에 `source_type='CAM_TOOLPATH'`, `ref_id=setup_id` 기록.
  - 없으면 기존 `machining.py` 추정치로 무변경 폴백.

### C4. 레이저/판금 절단 경로 (`core/cam/laser.py`)
- 입력: 판금 전개도 DXF(`SHEET_*` `CUT` 레이어) 또는 2D DXF 리비전.
- 처리: (1) 커프(kerf/2) 오프셋, (2) **내부 구멍 우선 절단 → 외곽 마지막 절단** 위상 정렬, (3) 내부 구멍은 안쪽·외곽은 바깥쪽 리드인/리드아웃 배치(경계 충돌 검사), (4) 인접 직선 공용선(common-line) 중복 절단 제거 옵션.

### CAM API 계약 (`backend/api/routes_cam.py`)
| Method | Path | 역할 |
|---|---|---|
| `GET` | `/api/cam/tools` | 활성 기준정보 버전의 공구 목록 조회 |
| `POST` | `/api/documents/{doc_id}/cam/setups` | CAM 셋업 생성 |
| `GET` | `/api/documents/{doc_id}/cam/setups` | 문서 CAM 셋업 및 공정 목록 조회 |
| `POST` | `/api/cam/setups/{setup_id}/recognize` | 3D 구멍/포켓 자동 인식 → 공정 초안 생성 |
| `POST` | `/api/cam/setups/{setup_id}/operations` | 공정 추가·경로 계산 (워커 실행) |
| `GET` | `/api/cam/setups/{setup_id}/toolpath` | 시뮬레이션용 세그먼트 JSON 반환 |
| `GET` | `/api/cam/setups/{setup_id}/gcode` | 후처리 G코드 파일 다운로드 (`text/plain`, `.nc`) |

---

## 4. CAE 상세 명세 (E1 ~ E3)

### E1. 3D 사면체(Tet4) 메시 + 경계조건 (`core/cae/mesh.py`)
- **라이선스/의존성 원칙**:
  - Python 의존성은 **`scipy`** (BSD-3)만 추가 (`pyproject.toml`/`uv.lock` 변경 시 사람 승인 + `THIRD_PARTY_NOTICES.md` 갱신 + R3.0 `UPGRADE` 패치 등급).
  - 표면 삼각화(`BRepMesh_IncrementalMesh`)는 기존 OCCT(LGPL) 사용, 볼륨 사면체화(Tet4)는 순수 Python/SciPy(`scipy.spatial.Delaunay` + 내부 점 필터링 + 품질/야코비안 검증) 기본 내장.
  - 선택적으로 사용자 PC에 `AXCAD_GMSH_PATH`가 설정된 경우에만 외부 `gmsh` CLI를 DWG 변환기와 동일한 격리 어댑터(비밀 환경변수 제거·Job Object·심볼릭 링크 거부)로 호출 가능.
- **경계조건 매핑**: `core/geometry/refs.py`의 `FaceRef` 서명으로 고정면(`u_x=u_y=u_z=0`)과 하중면(집중/분포 하중 `FORCE`, 수직 압력 `PRESSURE`) 노드를 자동 선택.
- **하드 상한**: `MAX_NODES = 100_000`, `MAX_ELEMENTS = 400_000`, 최소 요소 품질(야코비안 > 0) 검증.

### E2. 선형 정적 FEA 솔버 (`core/cae/fea.py`, `backend/api/routes_cae.py`)
- **수식**: 4절점 사면체(Tet4) 등방 선형 탄성 강성 행렬 $K_e = V_e B^T D B$ 조립 (`scipy.sparse.coo_matrix` → `csr_matrix`).
- **경계조건 적용**: 고정 DOF 제거(또는 대칭 페널티/소거) 후 `scipy.sparse.linalg.spsolve` (DOF ≤ 60,000) / `cg` + Jacobi preconditioner (DOF > 60,000).
- **강체 모드(구속 부족) 감지**: 고정 노드 < 3개 또는 공선 배치, 또는 해의 상대 잔차 > `1e-4` / 특이 행렬 시 `422 CAE_UNDERCONSTRAINED` 반환.
- **후처리**:
  - 요소별 응력 텐서 $\sigma = D B u_e$ → von Mises 응력 $\sigma_{vm}$ → 절점 가중 평균.
  - 최소 안전율 `min_safety_factor = yield_strength_mpa / max_von_mises_mpa`.
- **워커 격리**: `TIMEOUT = 120s`, 메모리 상한(`RLIMIT_AS` / Windows `JOB_MEMORY`).

### E3. 3D 결과 색 지도 + PDF 보고서 (`CaePanel.tsx`)
- **색 지도 공용화**: `TASK-003`의 `frontend/src/lib/cad/surfaceAnalysis.ts` 컬러바·버텍스 색 매핑 유틸 재사용.
- **뷰어 기능**:
  - 표시 모드 전환: `von Mises 응력 (MPa)` / `변위 (mm)` / `안전율`.
  - 변위 과장 슬라이더 (`0×` ~ `50×`), 최대/최소 마커 표시, 초기화 버튼.
  - `ThreeViewport.tsx` 모든 오버레이 버퍼 `dispose()` 보장.
- **PDF 보고서 (`GET /api/cae/studies/{study_id}/report?format=pdf`)**: 부품 정보·재질 물성·경계조건·메시 통계·최대 변위/응력/안전율 요약 + **"선형 정적 근사 해석 참고용"** 면책 문구 필수 삽입.

### CAE API 계약 (`backend/api/routes_cae.py`)
| Method | Path | 역할 |
|---|---|---|
| `POST` | `/api/documents/{doc_id}/cae/studies` | 해석 스터디 생성 (재질·구속면·하중면) |
| `POST` | `/api/cae/studies/{study_id}/solve` | 메시 생성 + FEA 풀이 실행 (워커) |
| `GET` | `/api/cae/studies/{study_id}` | 해석 요약·상태 조회 |
| `GET` | `/api/cae/studies/{study_id}/result` | 3D 색 지도용 변위·von Mises·안전율 배열 반환 |
| `GET` | `/api/cae/studies/{study_id}/report` | PDF 해석 보고서 다운로드 |

---

## 5. 검증 테스트 시나리오

| TC | 단계 | 검증 내용 | 통과 기준 |
|---|---|---|---|
| `CAM-T01` | C1 | 100×60 포켓(Ø10, 스텝오버 50%, 깊이 10, 절입 2) | 절삭 길이·시간 손계산 ±0.5%, 미절삭 섬 0 |
| `CAM-T02` | C2 | 구멍 4개 + 포켓 1개 3D 파트 자동 인식 | 좌표·깊이·지름 오차 `≤ 1e-6`, 시뮬레이션 렌더 오류 0 |
| `CAM-T03` | C3 | Fanuc G코드 생성 + 주석 개행 주입 시도 | 안전 헤더 존재, 제어문자 주입 차단(422), 견적 `CAM_TOOLPATH` trace 기록 |
| `CAM-T04` | C4 | 판금 전개도(외곽 + 내부 구멍 4개) 레이저 경로 | 내부 구멍 선절단 → 외곽 후절단, 리드인 충돌 0 |
| `CAM-T05` | C1~C3 | 세그먼트 상한 초과/NaN 파라미터 | 422 반환, 500·무한루프 0 |
| `CAE-T01` | E1~E2 | 외팔보(강재 `E=210 GPa`, `L=200`, `b=20`, `h=10`, 끝단 `F=100 N`) | 최대 처짐 `PL³/(3EI)` 대비 오차 **≤ ±2%**, 최대 굽힘응력 `Mc/I` ±5% |
| `CAE-T02` | E2 | 단순 지지 평판 균일 압력 | 해석해 중앙 처짐 대비 오차 **≤ ±3%**, `SF = Sy / σ_vm` 일치 |
| `CAE-T03` | E2 | 구속면 없음 / 공선 2점 구속 | `422 CAE_UNDERCONSTRAINED` 반환 |
| `CAE-T04` | E1~E2 | `MAX_NODES` 초과 초미세 메시 요청 | 422 거부, OOM 없음 |
| `CAE-T05` | E3 | Playwright E2E (CAM 경로·G코드 다운로드 + CAE 색 지도·PDF) | 콘솔·CSP 위반 0, WebGL 누수 0 |

---

## 6. Claude Code 단계별 인계 프롬프트

```text
docs/specs/cam-cae-spec.md를 기준으로 CAM·CAE를 단계별 PR로 구현해 주세요.
- 선행 조건: TASK-002(조립 A1~A4, 마이그레이션 0035~0037) 및 TASK-003(곡면 UI, surfaceAnalysis.ts) 완료 후 착수
- PR 분할 순서:
  1) feat/claude-cam-c1: 0038 마이그레이션 + G1 17_공구절삭조건 + core/cam/tools.py + mill25d.py + CAM-T01/T05
  2) feat/claude-cam-c2: core/cam/recognize.py + CamPanel.tsx 시뮬레이션 + CAM-T02
  3) feat/claude-cam-c3: core/cam/post.py(Fanuc/Laser) + routes_cam.py + quote_trace CAM_TOOLPATH + CAM-T03
  4) feat/claude-cam-c4: core/cam/laser.py + CAM-T04
  5) feat/claude-cae-e1-e2: scipy 의존성 추가(사람 승인 후) + 0039 마이그레이션 + core/cae/mesh.py + fea.py + routes_cae.py + CAE-T01~T04
  6) feat/claude-cae-e3: CaePanel.tsx(surfaceAnalysis.ts 공용) + PDF 보고서 + CAE-T05
- 각 PR 완료 전 백엔드(ruff/mypy/pytest) 및 프론트엔드(typecheck/lint/test) 게이트 통과 필수.
```
