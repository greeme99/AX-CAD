# SPEC — 곡면(S6~S8) 프론트엔드 UI 및 3D 품질 분석 오버레이 구현 명세

> **대상 작업**: `docs/TASKS.md` `TASK-003` (구현: Claude Code) / `TASK-004` (리뷰·검증: Antigravity)
> **작성자**: Antigravity (2026-11-19) | **기준 커밋**: `main-23t1bb` (`eceec7e`, PR #77)
> **전제**: 백엔드 커널(`core/geometry/surface*.py`) 및 API(`backend/api/routes_model.py`, 마이그레이션 0033~0034)는 구현·테스트 완료. **백엔드·DB·의존성 무변경 원칙.**

---

## 1. 목표 및 범위

| 구분 | 범위 |
|---|---|
| **포함 (In-Scope)** | ① S6 곡면 생성 7종 대화상자 (`SurfaceDialog`)<br>② S7 곡면 편집 6종 대화상자 (`SurfaceDialog`)<br>③ S8 품질 분석 4종 패널 (`SurfaceAnalysisPanel`) + `ThreeViewport` 3D 색 지도 오버레이<br>④ `FeatureTree`·`FeatureTimeline` `SURF_*` 라벨/표시 및 `model/[documentId]/page.tsx` 진입점 연결<br>⑤ 순수 로직 단위 테스트(`vitest`) + E2E/수동 검증 체크리스트 |
| **제외 (Out-of-Scope)** | 백엔드 API/커널 변경, 신규 npm 패키지 추가, NURBS 제어점 3D 직접 드래그 편집 |

---

## 2. 백엔드 API 계약 (검증 완료 — `backend/api/routes_model.py`)

### 2.1 S6 곡면 생성 7종 (`POST /api/documents/{docId}/features`)
`createBody(feature_type, params)` 패턴(`SheetDialog.tsx` 동일) 사용.

| `feature_type` | 한국어 라벨 | 필수/선택 파라미터 및 제약 |
|---|---|---|
| `SURF_LOFT` | 곡면 로프트 | `sketch_ids: number[]` (2~20개), `ruled: boolean` (기본 `false`), `solid: boolean` (기본 `false`) |
| `SURF_SWEEP` | 곡면 스윕 | `profile_sketch_id: number`, `path_sketch_id: number`, `frenet: boolean` (기본 `false`) |
| `SURF_EXTRUDE` | 곡면 돌출 | `sketch_feature_id: number`, `distance: number` (`0 < d ≤ 1e6`), `direction: "+Z" \| "-Z"` |
| `SURF_REVOLVE` | 곡면 회전 | `sketch_feature_id: number`, `axis_point: [x, y]`, `axis_dir: [dx, dy]` (비영벡터), `angle_deg: number` (`0 < a ≤ 360`) |
| `SURF_BOUNDARY` | 경계 곡면 | `edge_point_sets: number[][][]` (3~4개 곡선, 각 곡선 ≥2개 3D 점 `[x,y,z]`), `continuity: "G0" \| "G1" \| "G2"` |
| `SURF_FILL` | 채움 곡면 | `edge_point_sets: number[][][]` (2~12개 곡선), `continuity: "G0" \| "G1" \| "G2"`, `interior_points?: number[][] \| null` |
| `SURF_OFFSET` | 곡면 오프셋 | `target_feature_id: number`, `offset: number` (`!== 0`, 유한수) |

### 2.2 S7 곡면 편집 6종 (`POST /api/documents/{docId}/features`)

| `feature_type` | 한국어 라벨 | 필수/선택 파라미터 및 제약 |
|---|---|---|
| `SURF_EXTEND` | 곡면 연장 | `target_feature_id: number`, `distance: number` (`0 < d ≤ 1e6`), `mode: "natural" \| "linear"` |
| `SURF_TRIM` | 평면 자르기 | `target_feature_id: number`, `plane_origin: [x,y,z]`, `plane_normal: [nx,ny,nz]` (비영벡터), `keep_side: "positive" \| "negative"` |
| `SURF_SPLIT` | 평면 분할 | `target_feature_id: number`, `plane_origin: [x,y,z]`, `plane_normal: [nx,ny,nz]` (비영벡터) |
| `SURF_KNIT` | 꿰매기(솔리드화) | `face_feature_ids: number[]` (1~500개), `tolerance: number` (`0 < t ≤ 1.0`, 기본 `1e-3`) |
| `SURF_THICKEN` | 두께 부여(솔리드화) | `target_feature_id: number`, `thickness: number` (`!== 0`, 유한수) |
| `SURF_REPLACE` | 면 교체 | `target_feature_id: number`, `new_face_feature_id: number`, `face_index: number` (`>= 0` 정수) |

### 2.3 S8 품질 분석 4종 (읽기 전용 `POST /api/features/{featureId}/...`)

| 엔드포인트 | 요청 Body (기본값) | 응답 JSON 구조 |
|---|---|---|
| `/curvature-map` | `{ n_u?: 2..200 (20), n_v?: 2..200 (20) }` | `{ samples: {u,v,x,y,z,gauss,mean,k_min,k_max}[], stats: {gauss_min,gauss_max,mean_min,mean_max,k_min_global,k_max_global} (number\|null), n_u, n_v }` |
| `/continuity-check` | `{ n_samples?: 2..200 (20), g0_tol?: (0,1] (1e-3), g1_tol_deg?: (0,90] (1.0), g2_tol_pct?: (0,100] (5.0) }` | `{ edges: {edge_idx, g0_max_mm, g1_max_deg, g2_max_pct, pass_g0, pass_g1, pass_g2}[], summary: {total_edges, g0_pass, g1_pass, g2_pass, all_pass} }` |
| `/draft-analysis` | `{ pull_direction: [dx,dy,dz], n_u?: 2..200 (10), n_v?: 2..200 (10) }` | `{ faces: {face_idx, draft_min_deg, draft_max_deg, draft_avg_deg, n_samples}[], pull_direction: [number,number,number] }` |
| `/zebra-stripes` | `{ stripe_dir?: [sx,sy,sz] \| null, n_u?: 2..200 (40), n_v?: 2..200 (40) }` | `{ samples: {u,v,x,y,z,intensity,stripe}[], stripe_dir: [number,number,number], n_u, n_v }` |

---

## 3. 프론트엔드 모듈 설계 (기존 패턴 정합)

| 파일 | 작업 | 책임 |
|---|---|---|
| `frontend/src/lib/cad/features.ts` | 수정 | `Feature["feature_type"]`에 `SURF_*` 13종 추가, `isSurface(f)` 헬퍼, `featureName()` 한국어 라벨, 13종 파라미터 빌더/클라이언트 검증 함수(`surfLoftParams` 등, 오류 시 한국어 string 반환) |
| `frontend/src/lib/cad/surfaceAnalysis.ts` | **신규** | DOM/Three 비의존 순수 모듈: API 응답 타입 4종, `mapScalarToRgb(val, min, max)` (Blue→Cyan→Green→Yellow→Red), `draftColor(avgDeg, minRequiredDeg)` (양호 Green / 주의 Amber / 언더컷·불량 Red), `buildPointCloudColors()` (curvature·zebra용 `Float32Array` position/color), `buildDraftFaceColors()` (face_idx별 색상 맵) |
| `frontend/src/components/cad/SurfaceDialog.tsx` | **신규** | `SheetDialog.tsx`와 동일 모달 패턴. 상단 2그룹(`생성 S6` / `편집 S7`) + 모드 선택 드롭다운, 대상 스케치/바디 선택, `SURF_BOUNDARY`/`SURF_FILL` 점 목록 입력(JSON/행 단위 입력 + 샘플 프리셋 1종), 제출 전 클라이언트 검증 + `errText(err)` 에러 표시 |
| `frontend/src/components/cad/SurfaceAnalysisPanel.tsx` | **신규** | 인라인 사이드패널. 선택된 피처(`brep_key` 보유 OK 바디) 대상 4종 탭(`곡률` / `연속성` / `구배` / `제브라`). 곡률 지표 선택(`gauss \| mean \| k_min \| k_max`), 구배 기준각(기본 `2°`)·방향 프리셋(`+Z/-Z/+X/+Y`), 컬러바 범례(min/mid/max), 연속성 모서리 합불 표, "오버레이 지우기" 버튼 |
| `frontend/src/components/cad/ThreeViewport.tsx` | 수정 | `analysisOverlay?: SurfaceAnalysisOverlay \| null` prop 추가. `curvature`/`zebra`: `THREE.Points` (vertexColors, sizeAttenuation=false, size=5~6px) 또는 UV 격자 `THREE.Mesh` 오버레이; `draft`: 대상 메쉬 `userData.faces` 삼각형 범위에 면별 반투명 컬러 오버레이 메쉬 생성. **해제/갱신 시 geometry·material `dispose()` 필수** |
| `frontend/src/app/model/[documentId]/page.tsx` | 수정 | 3D 툴바에 "곡면" 버튼(`SurfaceDialog`) 및 "곡면 분석" 토글(`SurfaceAnalysisPanel`) 추가, `analysisOverlay` 상태를 `ThreeViewport`에 전달(피처 변경/삭제 시 오버레이 자동 초기화) |

---

## 4. 경계 조건 및 필수 방어 규칙

1. **0 / 비유한수 입력 차단**: `SURF_OFFSET.offset === 0`, `SURF_THICKEN.thickness === 0`, `NaN`/`Infinity`는 API 호출 전 차단.
2. **영벡터 차단**: `SURF_REVOLVE.axis_dir`, `SURF_TRIM.plane_normal`, `SURF_SPLIT.plane_normal`, `draft-analysis.pull_direction`, `zebra-stripes.stripe_dir`의 노름 `< 1e-9`이면 차단.
3. **퇴화 통계 방어**: `curvature-map`에서 평면/구면 등으로 `min === max`이거나 `stats.* === null`일 때 0 나눗셈 없이 중립색(`#22c55e`) 반환.
4. **빈 모서리/면 방어**: 단일 면 바디의 `continuity-check` (`total_edges === 0`) 시 빈 상태 안내("공유 모서리 없음 — 2개 이상 면이 연결된 형상 필요") 표시.
5. **409 `FEATURE_NOT_BUILT` 방어**: 스케치·규격품·억제된 피처에서 분석 실행 시 비활성화 또는 명확한 안내 표시.
6. **WebGL 누수 방지 & React 19 규칙**: `ThreeViewport.tsx` `useEffect` cleanup에서 생성한 오버레이 `BufferGeometry`·`Material`을 모두 `dispose()`하며, `useEffect` 콜백은 반드시 블록 바디(`{ ... }`)로 작성(커밋 `0861b93` 준수).
7. **디자인 일관성**: `DESIGN.md` 토큰(`--color-*`) 준수, 신규 의존성 추가 금지.

---

## 5. 수용 기준 및 테스트 시나리오 (Claude Code 구현 + Antigravity 검증)

### 5.1 단위 테스트 (`vitest` — `frontend/src/lib/cad/__tests__/surface.test.ts` 신규)
| TC | 검증 내용 | 기대 결과 |
|---|---|---|
| `SURF-U01` | S6 7종 파라미터 빌더 정상/오류 케이스 (`offset=0`, `sketch_ids<2`, `angle_deg>360`, 영벡터 `axis_dir`) | 정상 시 API 스키마 일치 객체, 오류 시 한국어 에러 string |
| `SURF-U02` | S7 6종 파라미터 빌더 정상/오류 케이스 (`thickness=0`, `face_feature_ids=[]`, 영벡터 `plane_normal`, `face_index<0`) | 정상 시 API 스키마 일치 객체, 오류 시 한국어 에러 string |
| `SURF-U03` | `mapScalarToRgb` 정상 범위 + 퇴화(`min===max`, `null`, `NaN`) | `[0..1, 0..1, 0..1]` 유한 RGB 반환, 0 나눗셈 없음 |
| `SURF-U04` | `draftColor` 임계각 경계 (`<0` 언더컷, `0..minDeg` 주의, `>=minDeg` 양호) | 구간별 정확한 색상/등급 반환 |
| `SURF-U05` | `buildPointCloudColors` (curvature 4종 지표 + zebra `stripe` 0/1 흑백) | `positions.length === samples.length * 3`, `colors.length === samples.length * 3` |

### 5.2 통합/회귀 검증 게이트
- `pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test` 전수 통과
- 기존 3D 모델러 기능(스케치, 돌출/포켓, 판금 `SheetDialog`, 조립, 측정) 회귀 없음
- 브라우저 수동/E2E 확인(Antigravity `TASK-004` 리뷰 단계):
  1. 스케치 2개 → `SURF_LOFT` 생성 → 3D 뷰어 렌더링 및 타임라인 표시
  2. `SURF_THICKEN` 또는 `SURF_OFFSET` 실행 → 결과 바디 갱신
  3. 분석 패널에서 `곡률`/`구배`/`제브라`/`연속성` 4종 실행 → 3D 오버레이 표시 및 "지우기" 후 원복 확인

---

## 6. Claude Code 전달용 실행 프롬프트

```text
docs/AGENT_CONTRACT.md, docs/TASKS.md, docs/specs/surface-ui-spec.md를 먼저 읽어.
TASK-003 (곡면 S6~S8 프론트엔드 대화상자·품질 분석 3D 색 지도 UI)을 담당해.

제약:
- 별도 Git worktree에서 작업해 (브랜치: feat/claude-surface-ui).
- 수정 가능: frontend/src/**
- 수정 금지: backend/**, core/**, frontend/package.json, main 직접 푸시
- 완료 기준:
  1. surface-ui-spec.md §2~§4 요구사항 및 경계 조건 전수 구현
  2. frontend/src/lib/cad/__tests__/surface.test.ts (SURF-U01~U05) 추가
  3. pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test 통과
- 완료 후 docs/TASKS.md 상태 갱신(review) 및 변경 파일·검증 결과·남은 리스크를 보고해.
```
