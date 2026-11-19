# SPEC — 조립(Assembly) 완전 수준 고도화 A1~A4 구현 명세

> **대상 작업**: `docs/TASKS.md` `TASK-001` (명세: Antigravity) → `TASK-002` (구현: Claude Code)
> **작성자**: Antigravity (2026-11-19) | **근거**: `plan.md` §23.9~§23.10 | **기준 커밋**: `main-23t1bb` (`eceec7e`, 마이그레이션 `0034`)
> **구현 전략**: A1 → A2 → A3 → A4 **4개 독립 PR**로 순차 머지 (각 PR마다 마이그레이션·단위/API 테스트·CI 녹색 확보).

---

## 0. 기존 자산 및 의도적 제외 (Ponytail)

- **현재 구현(`0025`, `core/geometry/assembly.py`, `backend/api/routes_assembly.py`)**:
  - 단일 레벨 조립(최대 200부품), 면 메이트 5종(`COINCIDENT`, `DISTANCE`, `PARALLEL`, `ANGLE`, `CONCENTRIC` — 평면·원통만), Levenberg-Marquardt 솔버, 부피 간섭 검사, 1단 BOM, Compound STEP 내보내기.
- **의도적 제외 (구현 금지, UI에서 미지원 안내)**:
  - 동역학/운동 시뮬레이션(모터·스프링·접촉력), 기어/랙/캠/나사 운동 메이트, 유연 하위 조립, 조립 내 Top-down 부품 편집, 용접/배관/하네스, >1,000부품 경량화, Configuration 표.

---

## 1. A1 — 메이트·참조 확대 · 자유도 · 과구속 진단 (마이그레이션 `0035`)

### 1.1 DB 마이그레이션 (`0035_assembly_mates_a1.py`)
`assembly_mates` 테이블 확장 (기존 행 하위 호환):
- `mate_type` CHECK 확장: 기존 5종 + `TANGENT`, `PERPENDICULAR`, `LOCK`, `SYMMETRIC`, `WIDTH`, `LIMIT_DISTANCE`, `LIMIT_ANGLE`
- 신규 컬럼:
  - `value_min: Float | None` (한계 메이트 하한)
  - `value_max: Float | None` (한계 메이트 상한)
  - `suppressed: Boolean` (default `False`, 억제 시 솔버에서 제외)
  - `extra: JSONB | None` (`SYMMETRIC` 대칭 기준면 `plane_side`, `WIDTH` 4면 `a2`/`b2`, `LOCK` 잠금 시점 상대 변환 `rel_t`/`rel_r`)
- `a`, `b` JSONB 확장: 기존 `{"component_id": int, "face": FaceSig}` 호환 유지 + `"ref_kind": "FACE" | "EDGE" | "VERTEX"` (생략 시 `"FACE"`), `"ref": Sig` 지원.

### 1.2 참조 기하 확대 (`core/geometry/assembly.py`)
`ref_geom(shape, side)`로 기존 `face_geom()` 확장:
| `ref_kind` | 지원 형상 | 추출 기하 (`part` 좌표계) |
|---|---|---|
| `FACE` | `PLANE`, `CYLINDER`, **`CONE`**, **`SPHERE`** | `PLANE`: `{p, n}`, `CYLINDER`: `{p, n, r}`, `CONE`: `{p: apex, n: axis, half_angle}`, `SPHERE`: `{p: center, r}` |
| `EDGE` | `LINE`, `CIRCLE` | `LINE`: `{p: mid, n: dir}` (축 취급), `CIRCLE`: `{p: center, n: axis, r}` (원통 동심/일치 호환) |
| `VERTEX` | `POINT` | `{p: xyz}` (점-점, 점-평면, 점-축 일치/거리) |

### 1.3 신규 메이트 잔차 수식 (ADR-13 Q2 솔버 함정 준수)
> **필수 준수**: 평면 마주봄 잔차는 `na × nb`(2차 안장점 회피), 회전 변수는 부품 크기로 스케일, 평행/일치 같은 방향 안장점은 풀이 전 자유 부품 뒤집기 처리.

| `mate_type` | 잔차 조건 (만족 시 `|r| ≤ 1e-7`) | 비고 |
|---|---|---|
| `TANGENT` | 평면-원통: `|(pc - pp)·np| - r_cyl = 0` (flip으로 내/외접 방향), 원통-원통: `dist(axis_a, axis_b) - (r_a ± r_b) = 0` + 축 평행 `na × nb = 0`, 평면-구: `|(ps - pp)·np| - r_s = 0` | |
| `PERPENDICULAR` | `na · nb = 0` (평면-평면, 평면-축, 축-축) | 1 DOF 제약 |
| `LOCK` | `t_rel - t_rel0 = 0` (3식) + `log(R_rel · R_rel0ᵀ) = 0` (3식) | 등록 시점 상대 변환을 `extra`에 고정, 6 DOF 제약 |
| `SYMMETRIC` | 두 면/점 `a`, `b`의 중점이 기준면 `extra.plane_side` 위 (`((pa+pb)/2 - pm)·nm = 0`) + 법선 대칭(`(na+nb) × nm = 0` 또는 `na·nm + nb·nm = 0`) | 3개 컴포넌트 참여 가능 |
| `WIDTH` | `a1`, `a2` 중점과 `b1`, `b2` 중점이 법선 방향 일치 (`((pa1+pa2)/2 - (pb1+pb2)/2)·na1 = 0`) + 평행 | `extra`에 `a2`, `b2` 저장 |
| `LIMIT_DISTANCE` | `d = (pb - pa)·na`일 때 `d < value_min` → `d - value_min`, `d > value_max` → `d - value_max`, 범위 내 → `0` | 범위 내 자유도 1 유지 |
| `LIMIT_ANGLE` | `θ = angle(na, nb)`일 때 범위 밖 초과분만 잔차, 범위 내 → `0` | `0 ≤ value_min ≤ value_max ≤ 180` |

### 1.4 자유도(DOF) & 과구속 진단 & API
- **DOF 계산**: 풀이 완료 시점 활성 Jacobian `J`에 대해 부품별(6열 블록) SVD로 미구속 자유도 `dof_by_component: {component_id: 0..6}` 반환 (`fixed=True`는 `0`).
- **과구속 진단**: 잔차 `> SOLVED_TOL`인 메이트가 있을 때, 각 불만족 메이트를 1개씩 제외한 부분 풀이(상한 20회/예산 5초) 또는 Jacobian 행 선형종속 분석으로 `conflicting_mate_ids: int[]` 반환.
- **신규/확장 API**:
  - `PATCH /api/mates/{mate_id}`: `{ suppressed?: bool, value?: float, value_min?: float, value_max?: float, flip?: bool }`
  - `POST /api/documents/{id}/assembly/solve` 응답 확장: `{ placements, mates, dof_by_component, conflicting_mate_ids, overconstrained: bool }`

---

## 2. A2 — 조립 구조: 하위 조립 · 패턴 · 미러 · 교체 (마이그레이션 `0036`)

### 2.1 DB 마이그레이션 (`0036_assembly_structure_a2.py`)
- `assembly_components` 확장:
  - `source_feature_id`: `NULL` 허용 (`component_kind == "SUBASSEMBLY"`일 때)
  - `component_kind: Text` (`"PART" | "SUBASSEMBLY"`, default `"PART"`)
  - `visible: Boolean` (default `True`), `suppressed: Boolean` (default `False`)
  - `pattern_id: BigInteger | None` (FK `assembly_patterns.pattern_id` ON DELETE CASCADE)
  - `pattern_index: Integer | None`
  - `mirrored_from_id: BigInteger | None`, `mirror_plane: JSONB | None` (`{origin: [x,y,z], normal: [nx,ny,nz]}`)
- 신규 테이블 `assembly_patterns`:
  - `pattern_id` PK, `document_id` FK(`documents` CASCADE), `source_component_id` FK(`assembly_components` CASCADE), `pattern_type` (`"LINEAR" | "CIRCULAR"`), `params` JSONB (`LINEAR`: `{dir, spacing, count, dir2?, spacing2?, count2?}`, `CIRCULAR`: `{axis_origin, axis_dir, angle_deg, count, equal_spacing}`), `created_by`, `created_at`.
  - **주의**: `backend/cli.py` `APP_DELETABLE` 점검(초안 삭제 대상 여부 확인).

### 2.2 핵심 규칙 및 안전 가드
- **하위 조립(강체)**: `source_document_id`가 `doc_type == "ASSEMBLY"`인 문서를 삽입.
  - **순환 참조 차단**: 삽입 전 조립 그래프 DFS 검사 → 자기 참조·순환 발견 시 `409 ASSEMBLY_CYCLE`.
  - **깊이·수량 상한**: 중첩 깊이 `> 8` → `422 ASSEMBLY_DEPTH_EXCEEDED`, 재귀 펼침 총 부품 수 `> 1,000` → `422 ASSEMBLY_LIMIT_EXCEEDED`.
  - 상위 솔버에서는 하위 조립을 **1개 강체(6 DOF)**로 풀이하고, 렌더링·간섭·BOM·질량 시 월드 변환 `T_parent · T_child`로 펼친다.
- **패턴 자동 갱신**: 원본 부품(`source_component_id`)의 `placement`가 변경·풀이되면 `pattern_id`로 연결된 파생 인스턴스의 `placement`를 결정론적으로 재계산(`T_pattern_i · T_source`). 파생 인스턴스는 솔버에서 `fixed` 취급(원본 종속).
- **부품 교체 (`POST /api/components/{id}/replace`)**: `{ new_source_document_id, new_source_feature_id }`. 교체 후 해당 컴포넌트에 연결된 메이트의 기하 서명(`resolve()`)을 재검증 → 성공 시 `OK` 유지, 실패 시 메이트 상태 `BROKEN`으로 마킹(삭제하지 않음).

### 2.3 신규/확장 API
- `PATCH /api/components/{id}`: `visible`, `suppressed` 추가
- `POST /api/documents/{id}/assembly/patterns` / `DELETE /api/assembly/patterns/{pattern_id}`
- `POST /api/components/{id}/mirror` (`{ plane_origin, plane_normal }`)
- `POST /api/components/{id}/replace`

---

## 3. A3 — 간섭·여유 · 자유도 드래그 · 분해도 · 단면 (마이그레이션 `0037`)

### 3.1 DB 마이그레이션 (`0037_assembly_inspect_a3.py`)
- 신규 테이블 `assembly_ignored_pairs`: `(document_id, comp_a_id, comp_b_id, reason, created_by, created_at)` — `comp_a_id < comp_b_id` CHECK 제약 및 UNIQUE.
- 신규 테이블 `assembly_explodes`: `(explode_id PK, document_id FK CASCADE, name, steps JSONB)` — `steps = [{step_no, component_ids, delta: [dx,dy,dz]}]`.

### 3.2 간섭·접촉·여유 검사 확장 (`POST /api/documents/{id}/interference`)
- 요청 확장: `{ clearance: float (0..1000), expand_subassemblies: bool = True, include_ignored: bool = False }`
- 분류 기준 (BBox 팽창 `clearance`로 후보 쌍 필터 → `BRepExtrema_DistShapeShape` + 필요 시 `BRepAlgoAPI_Common`):
  - **INTERFERENCE (간섭)**: 공통 부피 `> 1e-6 mm³`
  - **CONTACT (접촉)**: 최소 거리 `≤ 1e-5 mm` AND 공통 부피 `≤ 1e-6 mm³` (간섭 아님!)
  - **CLEARANCE (여유 위반)**: `1e-5 mm < dist < clearance` (`clearance > 0`일 때)
- 무시 목록 관리: `POST /api/documents/{id}/assembly/ignored-pairs`, `DELETE /api/assembly/ignored-pairs/{id}`.
- 성능 예산: 1,000부품 펼침 기준 `INTERFERENCE_BUDGET_S = 120.0`, 공간 해시/BVH 브로드페이즈로 BBox 쌍 선별(O(N²) 단순 루프 회피).

### 3.3 자유도 드래그 · 분해도 · 단면
- **드래그 (`POST /api/documents/{id}/assembly/drag`)**: `{ component_id, target_delta_t: [dx,dy,dz], target_delta_r?: [rx,ry,rz], stop_on_clash: bool = false }`
  - 드래그 대상의 목표 위치를 초기 추정치로 넣고 활성 메이트(한계 메이트 포함)를 유지하며 LM 풀이 → `stop_on_clash=True`이고 간섭 발생 시 이진 탐색(최대 6회)으로 충돌 직전 위치 반환.
- **분해도 API**: `GET/POST /api/documents/{id}/assembly/explodes`, `PATCH/DELETE /api/assembly/explodes/{explode_id}`. 접기 시 원래 `placement` 복원(원본 `placement`는 변하지 않음).
- **단면 보기 (Frontend 전용)**: `ThreeViewport.tsx`에서 `renderer.localClippingEnabled = true` + `THREE.Plane` 슬라이더(`X/Y/Z` 및 오프셋, 반전).

---

## 4. A4 — 조립 산출물: 질량 · 다단 BOM · 조립도 · STEP XDE · 조립 견적

> DB 마이그레이션 없음(또는 `bom_items.parent_item_id`/`depth` 필요 시 `0038_assembly_bom_a4.py`).

| 기능 | 엔드포인트 / 모듈 | 핵심 사양 |
|---|---|---|
| **질량 특성** | `GET /api/documents/{id}/assembly/mass` (`core/geometry/assembly_mass.py`) | 부품 재질 밀도(기준정보 `materials.density_g_cm3`, 미지정 시 기본 강재 7.85 또는 경고) × `BRepGProp.VolumeProperties_s` → 총 질량(kg), 무게중심 `[x,y,z]`, 평행축 정리 적용 관성 텐서 `[[Ixx,Ixy,Ixz],...]`. 해석값 대비 상대 오차 `≤ 1e-9`. |
| **다단 BOM** | `GET /api/documents/{id}/bom?mode=indented\|rolled_up` (`core/bom/`) | `indented`: 하위 조립 계층 트리(`level`, `parent_path`, `qty`), `rolled_up`: 동일 `(source_document_id, source_feature_id)` 합산 수량. |
| **STEP XDE 왕복** | `POST /api/projects/{id}/imports/assembly-step`, `GET /api/documents/{id}/assembly/export?format=step` (`core/geometry/exchange.py`) | `STEPCAFControl_Reader`/`Writer` + `TDocStd_Document` + `XCAFDoc_ShapeTool`: 하위 조립 계층·부품 이름·변환 행렬·색상 보존. 가져오기 시 부품 문서 + 조립 문서 자동 생성(보안: 최대 부품 1,000개, 깊이 8, 파일 크기 50MB, 워커 격리). |
| **조립도** | `POST /api/documents/{id}/drawing` 확장 (`explode_id?: int`) | 조립(또는 분해도) 전체 HLR 투상 + 부품별 풍선 번호(Bubble) + 부품표(Parts List) DXF `AXGEN_*` 레이어 생성. |
| **조립 견적** | `core/quote_engine/cost.py` (`compute_assembly_quote`) | 각 펼친 부품의 제조/구매 견적 합산 + 조립 공수 규칙 `A-ASM`(기준정보 공정 규칙: 부품 수 × 기본 조립 공수 + 체결 메이트 수 × 체결 공수). 모든 라인에 `component_id` 추적성(`quote_trace`) 유지. |

---

## 5. 완료 검증 사례 (`plan.md` §23.9 1:1 매핑)

| TC | 단계 | 검증 시나리오 | 통과 기준 |
|---|---|---|---|
| `ASM-T01` | A1 | 접선(평판 위 롤러 원통), 직각, 대칭, 잠금 메이트 | 풀이 후 잔차 `≤ 1e-7`, 원통 축–평면 거리 `= R` (오차 `≤ 1e-6`) |
| `ASM-T02` | A1 | 한계 메이트(슬라이더 10~50 mm) | 범위 내 자유도 1 유지, 범위 밖 드래그/풀이는 경계(10 또는 50)에 정지 |
| `ASM-T03` | A1 | 모서리·꼭짓점 참조(핀–구멍 원 모서리 동심) | 면 참조 동심과 동일 위치 (오차 `≤ 1e-6`) |
| `ASM-T04` | A1 | 과구속·억제 | 과구속 시 `conflicting_mate_ids` 보고, 억제 후 `residual ≤ 1e-7` |
| `ASM-T05` | A2 | 하위 조립 2단 + 원형 패턴 6개 + 순환 차단 | 펼친 부품 수·위치 = 손계산, 원본 이동 시 패턴 연동, 순환 시 `409 ASSEMBLY_CYCLE` |
| `ASM-T06` | A2 | 부품 교체(유사 형상 vs 다른 형상) | 동일 서명 시 메이트 유지, 소실 시 `BROKEN` 표시 |
| `ASM-T07` | A3 | 여유 검사(간격 0.3 mm, 기준 0.5 mm) + 접촉 + 무시 목록 | 0.3 mm 쌍만 `CLEARANCE` 보고(`1e-6`), 접촉 쌍은 간섭 아님, 무시 쌍 제외 |
| `ASM-T08` | A3 | 분해도 3단계 | 저장·재조회 후 위치 동일, 접기 시 원본 `placement` 복원 |
| `ASM-T09` | A4 | 질량 특성(강 블록 2개) + 다단 BOM + 조립 견적 | 질량·CoG·관성 = 해석값(상대 `1e-9`), 다단/합산 BOM = 손계산, 견적 trace = 부품 원천 |
| `ASM-T10` | A4 | STEP XDE 왕복 + 1,000부품 성능 | 왕복 후 부품 수·이름·배치·부피 동일; 1,000부품 풀이 `≤ 15s`, 간섭 `≤ 120s` |

---

## 6. Claude Code 전달용 실행 프롬프트 (단계별 PR)

```text
docs/AGENT_CONTRACT.md, docs/TASKS.md, docs/specs/assembly-a1-a4-spec.md를 먼저 읽어.
TASK-002 (조립 A1~A4)를 A1 → A2 → A3 → A4 순으로 단계별 PR로 구현해. 먼저 A1부터 착수해.

제약:
- 별도 Git worktree에서 작업해 (브랜치: feat/claude-assembly-a1).
- 수정 가능: core/geometry/**, backend/**, tests/**, frontend/src/components/cad/AssemblyWorkbench.tsx
- DB 마이그레이션(0035) 추가 전 스키마 변경 확인, APP_DELETABLE 회귀 테스트 포함.
- 완료 기준: A1 구현 + ASM-T01~T04 테스트 추가 + uv run ruff check . && uv run ruff format --check . && uv run mypy core backend && uv run pytest -q tests/ 통과.
- 완료 후 docs/TASKS.md 상태 갱신(review) 및 변경 요약·검증 결과·남은 리스크를 보고해.
```
