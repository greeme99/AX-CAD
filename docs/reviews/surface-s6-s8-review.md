# REVIEW — 곡면(S6~S8) 백엔드 커널 · API 독립 코드/보안 리뷰

> **리뷰어**: Antigravity | **기준일**: 2026-11-19 (`main-23t1bb` `eceec7e`, PR #77)
> **리뷰 대상**: `core/geometry/surface.py`, `core/geometry/surface_edit.py`, `core/geometry/surface_analysis.py`, `backend/api/routes_model.py` (S6~S8)
> **기준 테스트**: `uv run pytest -q tests/test_surface*.py` → **66 passed (1.52s)** (기존 테스트 통과, 단 아래 9건의 미검출 보안·기하·성능 결함 발견)

---

## 1. 리뷰 요약 (심각도별)

| 심각도 | 파일:라인 | 분류 | 문제 | 수정안 |
|---|---|---|---|---|
| **P0** | `backend/api/routes_model.py:630-642`<br>`core/geometry/surface.py:289-291` | 보안 / DoS | `SurfBoundaryParams.edge_point_sets` 및 `SurfFillParams.edge_point_sets`·`interior_points`에 **점 차원(3D)·곡선당 점 수·`interior_points` 개수·`allow_inf_nan=False` 검증 전무**. 2D 점 `[0,0]` → `_gp(p)` `IndexError`(500), 대량 `interior_points` → `BRepFill_Filling` CPU 폭주/워커 타임아웃 DoS | `Point3 = Annotated[list[Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]], Field(min_length=3, max_length=3)]` 도입; 곡선당 `min_length=2, max_length=2000`, `interior_points` `max_length=200` + `surface.py`에 `MAX_INTERIOR_POINTS = 200` 가드 추가 |
| **P1** | `backend/api/routes_model.py:625-626, 664-665, 672-673, 648, 686` | 보안 / 검증 | `axis_point`, `axis_dir`, `plane_origin`, `plane_normal`이 bare `list[float]`라 `NaN`/`Infinity` 통과 (`surface_revolve`에서 `math.hypot(nan,0) != 0` 통과 후 OCCT 크래시 가능). `offset`/`thickness` API 상한(`le=1e6`/`1e4`) 누락 | `Coord = Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]` 적용, `offset` `ge=-1e6, le=1e6`, `thickness` `ge=-1e4, le=1e4` 명시 |
| **P1** | `core/geometry/surface_edit.py:82-133` | 기하 정확성 | `surface_extend`가 경계 연장이 아니라 **면 전체를 법선/U방향으로 `BRepPrimAPI_MakePrism` 압출**함 (`natural`은 두께 솔리드 생성, `linear`는 평면 자가겹침 퇴화 위험). `d1v`도 영벡터일 때 `Normalize()` 미방어 | `GeomLib.ExtendSurfByLength_s` 또는 자유 경계 모서리 외측 접선 확장으로 재구현 + `d1u`/`d1v` 모두 영벡터 시 `GeomError` 반환 |
| **P1** | `core/geometry/surface_analysis.py:169-215` | 성능 / DoS | `continuity_check`가 상수 `MAX_FACES = 1000`을 정의만 하고 **미검사**. 4중 루프(`F² × E²`) + `seen_edges` 선형 탐색으로 대형 STEP/복합 바디에서 워커 타임아웃 유발 | `if len(faces) > MAX_FACES: raise GeomError(...)` 추가 + OCCT `TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, map)` O(E) 인접 탐색으로 교체 |
| **P1** | `core/geometry/surface_analysis.py:51-54, 405-408` | 기능 정확성 | `curvature_map`·`zebra_stripes`가 `TopExp_Explorer`의 **첫 번째 면만** 분석하고 나머지 면 무시 (`draft_analysis`는 전체 면 순회 — UI 연동 시 다중 면 바디에서 1개 면만 색 지도 표시됨) | 전체 면 순회(면당 `MAX_SAMPLE_POINTS // n_faces` 배분) 또는 선택적 `face_index: int \| None = None` 지원 |
| **P1** | `core/geometry/surface.py:376-379` | 기하 / 견적 | `_surf_job`이 `sheet_measure()`만 호출해 `SurfLoftParams(solid=True)`로 생성된 솔리드(`kind="solid"`)에 **`volume_mm3` 누락** | `n_solids > 0`이면 `solid_measure()`(또는 `sheet_measure` 통합)로 `volume_mm3` 기록 |
| **P2** | `core/geometry/surface.py:157-164` | 검증 / 500 | `nurbs_curve_control`의 `weights`에 `w <= 0` 또는 `NaN`/`Inf` 검증 없음 → OCCT `Standard_ConstructionError` 미래핑(500) | `if not (math.isfinite(w) and w > 0): raise GeomError("GEOM_INVALID_PARAM", ...)` 추가 |
| **P2** | `core/geometry/surface_edit.py:322-342` | 기하 안정성 | `surface_replace`가 `TopExp_Explorer`로 면 수집(compound에서 중복 면 방문 가능) 및 `new_face_shape` 면 존재 여부 미검증 | `TopExp.MapShapes_s`로 고유 면 목록 추출 + `new_face_shape` 최소 1개 면 확인 |
| **P2** | `backend/api/routes_model.py:1098-1105` | 검증 | `SURF_KNIT`의 `face_feature_ids` 중복 ID 또는 억제(`suppressed=True`)·형상 없는 피처(`SKETCH`/`STANDARD`) 사전 차단 미흡 | 중복 ID 제거/거부 + `brep_key is None` 시 `GEOM_INVALID_PARAM` 반환 |

---

## 2. 회귀 테스트 추가 권장 항목 (Claude Code 수정 시)

1. `test_surface_boundary_rejects_bad_points`: 2D 점 `[0,0]`, `NaN` 좌표, `interior_points` 201개 이상 → `422 REQUEST_INVALID`
2. `test_surface_revolve_trim_rejects_nan_and_zero`: `NaN` 축/법선, 영벡터 법선 → `422` 또는 `GeomError`
3. `test_surface_loft_solid_has_volume`: `SURF_LOFT(solid=True)` → `metrics["kind"] == "solid"` 및 `metrics["volume_mm3"] > 0`
4. `test_continuity_check_large_face_cap_and_fast_adjacency`: `MAX_FACES` 초과 시 `GeomError`, 다면체 O(E) 처리 검증
5. `test_curvature_and_zebra_multiface`: 2개 이상 면을 가진 바디에서 모든 면 샘플 반환 확인

---

## 3. Claude Code 전달용 수정 프롬프트

```text
docs/AGENT_CONTRACT.md, docs/reviews/surface-s6-s8-review.md를 먼저 읽어.
TASK-005 (곡면 S6~S8 백엔드·API 리뷰 지적 P0/P1/P2 9건 수정)을 담당해.

제약:
- 별도 Git worktree에서 작업해 (브랜치: fix/claude-surface-review).
- 수정 가능: core/geometry/surface*.py, backend/api/routes_model.py, tests/test_surface*.py
- 완료 기준:
  1. surface-s6-s8-review.md P0 1건 + P1 5건 + P2 3건 해결
  2. §2 회귀 테스트 5건 추가
  3. uv run ruff check . && uv run ruff format --check . && uv run mypy core backend && uv run pytest -q tests/test_surface*.py 통과
- 완료 후 docs/TASKS.md 상태 갱신(review) 및 변경 요약·검증 결과를 보고해.
```
