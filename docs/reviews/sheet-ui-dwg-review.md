# 판금 S1~S5 · DWG(`acadrust`) · UI Phase 0·3 독립 코드·보안 리뷰

> 작성일: 2026-11-20 | 리뷰어: Antigravity | 기준 커밋: `eceec7e` (PR #75~#77 머지)
> 베이스라인 테스트: `uv run pytest -q tests/test_sheetmetal.py` → **55 passed (9.00s)**

---

## 1. 리뷰 요약

| 영역 | 대상 파일 | P0 | P1 | P2 | 합계 |
|---|---|---:|---:|---:|---:|
| **Backend / Core (판금·견적·DWG)** | `sheetmetal.py`, `unfold.py`, `routes_model.py`, `cost.py`, `dfm.py`, `dwg.py`, `main.py` | 2 | 6 | 4 | **12** |
| **Frontend (`SheetDialog`·UI Phase 0·3)** | `ThreeViewport.tsx`, `model/[documentId]/page.tsx`, `FeatureTree.tsx`, `FeatureTimeline.tsx`, `SheetDialog.tsx`, `features.ts`, `DynamicInput.tsx`, `viewer/[revisionId]/page.tsx`, `ui.tsx`, `i18n.tsx`, `globals.css` | 3 | 7 | 3 | **13** |
| **총계** | — | **5** | **13** | **7** | **25** |

- **보안 스캔**: `frontend/src/` 내 `dangerouslySetInnerHTML` 0건, `SheetDialog.tsx` URL 파라미터 `encodeURIComponent` 적용 확인. (단 `core/dxf/dwg.py` 출력 심볼릭 링크 미검증 P1 및 `model/[documentId]/page.tsx:742` `docId` 인코딩 누락 방어 보강 필요)

---

## 2. Backend / Core 지적 사항 (`TASK-007` 대상)

| ID | 등급 | 파일:라인 | 문제 | 재현 / 영향 | 수정안 |
|---|---|---|---|---|---|
| **SH-01** | **P0** | `core/geometry/sheetmetal.py:621-630, 703-720` & `core/geometry/unfold.py:463-488` | `_flat_cut_tool`이 Z 방향 `±(t*10 + 100)` mm(240 mm) 프리즘을 만들고 `FoldMap.fold_tools`가 대상 평면 경계 클리핑 없이 모든 평면 프레임에 적용 | U채널(100×50, 90° UP 플랜지 2개) 또는 180° 헴에서 `UNFOLD → CUT → REFOLD` 시 50 mm 떨어진 **반대편 플랜지까지 유령 구멍 관통** | `_flat_cut_tool` Z 범위를 `(-0.1, t + 0.1)`로 제한하고 각 2D 컷 면을 해당 평면의 전개 2D 외곽에 클리핑 후 `inv[fi]` 변환 |
| **SH-02** | **P0** | `core/geometry/sheetmetal.py:967-1041` & `core/geometry/unfold.py:271-275, 425-429` | `convert_sharp_spec`이 (1) 내·외측 모서리에 동일 `bend_radius` 적용(`R <= t` 시 `unfold.py:274`에서 `r_in = R - t <= 0`), (2) 비평면 면은 `MAX_FACES` 미계수 + O(E×F×edges) 중첩 탐색, (3) 모든 모서리가 `rip_edges`일 때 OCCT 미처리 예외 | 날카로운 L브래킷(`t=2, R=1`) 전개 시 음수 굽힘 여유(`r_in = -1`); 5천+ B-spline STEP CPU DoS; `rip_edges=전체` 500 크래시 | 전체 면 수 `<= MAX_FACES` 검증, `TopExp.MapShapesAndAncestors_s`(O(E)), 오목 `R`/볼록 `R+t` 구분, 빈 필렛 거부 및 `fillet.Build()` `GeomError` 래핑, `r_in > 0` 어설션 |
| **SH-03** | **P1** | `core/geometry/sheetmetal.py:222-224, 755-756` | `simple(f)`가 `miter_*`, `relief_*`, `close_*`, `spec["cuts"]`를 무시해 `flat_analytic(spec)` 오탐 실행 | 오목(L/U) 베이스에 마이터/컷 적용된 정상 플랜지가 `SHEET_INVALID: "Flanges overlap in the flat pattern"` 거짓 오류로 거부 | `simple()`에 `not spec.get("cuts")` 및 6개 코너 옵션 모두 `None` 조건 추가 |
| **SH-04** | **P1** | `core/geometry/sheetmetal.py:951-964, 1082-1088` | `show_flat=True` 시 `sheet_job`이 `folded()` → `flat_body()`(재전개+`flat` 컷 중복 절단) → `flat()` 총 3회 전개 수행 | `SHEET_UNFOLD` 커널 비용 3배 + 동일 평면 컷 2회 적용으로 `GEOM_BOOLEAN_FAILED` 위험 | `sheet_job`에서 `fp = flat(spec, shape0)` 1회 계산 후 `flat_body(spec, fp)`에 전달, `flat_body` 중복 `_cut` 제거 |
| **SH-05** | **P1** | `backend/api/routes_model.py:1143-1156` | `_bend_values`가 표의 `bd90_mm`로 `k` 역산 시 표의 `inner_radius_mm` 대신 사용자 지정 `r` 사용 | `inner_radius_mm=2, bd90_mm=3.5` 표에 `bend_radius=10, k_factor=None` 요청 시 `k=1.525` → 거짓 `422 SHEET_TABLE_INVALID` | `r_tbl = float(row.inner_radius_mm)` 기준으로 `ba90`·`k` 계산 |
| **SH-06** | **P1** | `core/dxf/dwg.py:273-278` | `_read_result(result)`가 심볼릭 링크·Windows 리파스 포인트 미검사 | 악성 DWG로 변환기(`ODA`/`LibreDWG`/`acadrust`) 침해 시 출력 파일을 심볼릭 링크로 치환해 로컬 파일 유출 가능 | `result.lstat()`으로 `is_symlink()`·`S_ISREG`·Windows `0x400` 리파스 속성 차단 |
| **SH-07** | **P1** | `core/dxf/dwg.py:348-386, 423-436` | (1) `export_drawing()`이 `acadrust`/`ODA` `_run()` 종료 코드 무시, (2) `info()`가 `acadrust`에 `R12` 포함 반환(`DWG`는 `R12` 금지) | 변환 실패한 부분 DWG가 200 반환; UI에서 `R12` 선택 시 항상 422 실패 | `code = _run(...)` 검사 후 `code != 0`이면 `DxfError("DWG_CONVERT_FAILED", ..., 422)`; `acadrust` `versions`에서 `"R12"` 제외 |
| **SH-08** | **P1** | `core/geometry/sheetmetal.py:162-184, 276-283, 1154-1158` | `outline()` 구멍 자기교차·중첩·원 `radius<=0` 미검증, `check()` `NaN`/`Inf` 통과, `_form_tools()` `depth<=0`/`NaN` 미검증 | 직접 호출 시 `blank_area_mm2` 이중 차감 또는 OCCT `Standard_ConstructionError` | `math.isfinite` 가드, 구멍 `_simple(lp)`·비중첩·`rad > TOL` 및 성형 `0.05 <= depth <= 100` 검증 추가 |
| **SH-09** | **P2** | `core/quote_engine/cost.py:471-480, 504-515` | 다중 몸체 3D 판금 문서가 `NEST` → `BBOX`로 경고 없이 폴백; 2D `BBOX`는 `2 * allowance` 누락 | 다중 부품 견적 과소·과대 및 2D/3D `BBOX` 불일치 | 다중 몸체 폴백 경고(`QUOTE_NEST_MULTIBODY_FALLBACK`) 추가, 2D `BBOX` 여유 통일 |
| **SH-10** | **P2** | `core/dxf/dwg.py:163-165, 389-414` & `backend/api/main.py:1544` | Windows `_Job`에 `JOB_MEMORY(0x200)` 누락(`PROCESS_MEMORY`만 설정); `selftest()`가 `ezdxf.DXFError` 미포착 | 자식 프로세스 분기 시 4GB 상한 우회; 손상된 변환 출력에 500 반환 | `JobMemoryLimit = MEMORY_CAP` 추가; `selftest()` `ezdxf.DXFError` → 422 `DWG_CONVERT_FAILED` |
| **SH-11** | **P2** | `core/geometry/sheetmetal.py:675-683, 856` & `routes_model.py:1245-1252` | `bend_sequence`가 부모-자식 플랜지 계층 무시; `folded()`가 `cuts` 정렬 가정; `FLAT` 상태에서 `SHEET_FLANGE` 허용 | 하위 플랜지 절곡 순서 역전; 비정렬 컷 누락; 전개 중 플랜지 추가 오동작 | 계층 깊이 기준 정렬, `folded()` 내부 `cuts` 정렬, `mode == "FLAT"` 시 `SHEET_FLANGE` 거부 |
| **SH-12** | **P2** | `core/copilot/dfm.py:349, 366` | `SHEET_HOLE_NEAR_BEND`가 선분 대신 무한 직선 거리 사용; `SHEET_FLANGE_OVERLAP`이 마이터·반대 굽힘 방향(`up`) 무시 | 오목 외곽·마이터·양방향 플랜지 DFM 오탐 | `sheetmetal._seg_dist` 사용 및 마이터/`up` 반대 시 겹침 경고 제외 |

---

## 3. Frontend (`SheetDialog`·UI Phase 0·3) 지적 사항 (`TASK-008` 대상)

| ID | 등급 | 파일:라인 | 문제 | 영향 | 수정안 |
|---|---|---|---|---|---|
| **UI-01** | **P0** | `frontend/src/components/cad/ThreeViewport.tsx:227-254, 274-287, 322-331` | **WebGL VRAM 누수**: ViewCube의 6개 `PlaneGeometry`·`CanvasTexture`·`MeshBasicMaterial`·`BoxGeometry`·`EdgesGeometry`·`LineBasicMaterial` 미해제 + 스케치 세션마다 2번째 `WebGLRenderer` 생성. L322 `EdgesGeometry`도 L326 덮어쓰기 시 미해제 | 반복 모델링·스케치 시 GPU 메모리 누수 및 브라우저 WebGL 컨텍스트 한도(8~16) 초과 | 클린업에서 모든 ViewCube 지오메트리·텍스처·머티리얼 `dispose()` + `cubeRenderer.forceContextLoss()`. L322 `EdgesGeometry`를 `else` 분기로 이동 |
| **UI-02** | **P0** | `frontend/src/app/model/[documentId]/page.tsx:1164-1184, 1239-1243` | 우측 `Properties`의 `regen()`·"재생성" 버튼이 `isSheet(f)`를 제외하지 않아 Boolean 경로로 폴스루 | 모든 `SHEET_*` 피처에서 "재생성" 클릭 시 `"대상과 도구 형상을 선택하세요."` 오류 | L1165/L1239에 `isSheet(f)` 제외 가드 추가 + 읽기 전용 판금 요약 표시(또는 전용 편집기) |
| **UI-03** | **P0** | `frontend/src/components/cad/FeatureTree.tsx:21-35` | `summary()`가 `SHEET_*`를 처리하지 않고 `BooleanParams`로 캐스팅 | 트리에서 모든 판금 피처가 `#undefined undefined #undefined`로 깨져 표시 | `if (isSheet(f))` 분기 추가(`T{thickness}`, `L{length} {angle}°` 등 정상 포맷) |
| **UI-04** | **P1** | `frontend/src/components/cad/ThreeViewport.tsx:36-43, 210-212, 266-272` | 카메라는 Z-up인데 `CUBE_FACES`가 Y-up 좌표(`정면`=-Z, `평면`=+Y); 스케치 모드에서도 ViewCube 클릭 가능; `MeasurePanel`(`top-3 right-3`)과 겹침 | ViewCube 클릭 시 잘못된 방향 회전, 스케치 평면 카메라 이탈, 측정 패널 가림 | `CUBE_FACES`를 Z-up 좌표로 수정, `sketchPlane != null` 시 숨김/비활성, `MeasurePanel` 오프셋 조정(`top-28 right-3`) |
| **UI-05** | **P1** | `frontend/src/lib/cad/features.ts:478, 498, 511, 606, 635, 646` | `sheet*Params`가 `fin()` 대신 `Number()` 사용 → 빈 문자열 `""`이 `0`으로 강제 변환 | `length`/`inset`/`edge`를 비워도 `0`(또는 변 `#0`)으로 통과·제출 | 모든 `sheet*Params`의 `Number(...)`를 `fin(...)`으로 교체 |
| **UI-06** | **P1** | `frontend/src/components/cad/SheetDialog.tsx:38-43, 263, 303-309` | `SHEET_DEFAULT.edge="0"`이 이미 `used`여도 자동 전환·검증 없음; `target` 변경 시 `edge`/`side` 미초기화 | 2번째 플랜지 추가 또는 대상 전환 시 즉시 400/409 서버 오류 | `target` 변경 시 `edge`/`side` 리셋, `info.data` 로드 시 첫 미사용 변 자동 선택, `sheetFlangeParams`에 `used` 검증 추가 |
| **UI-07** | **P1** | `frontend/src/components/cad/DynamicInput.tsx:33-86` & `viewer/[revisionId]/page.tsx:975, 995-999` | 포커스 없이 `Tab`/`#` 미동작, 뷰포트 경계 클램핑 없음, 숫자 미검증·`Enter` 후 빈값 고정, `F12`가 입력 가드 뒤에 위치, `viewer` `keydown` `useEffect`에 의존성 배열 누락(마우스 이동마다 리스너 재등록) | 키보드 우선 2D 작도 불가, 화면 끝 잘림, 포인터 이동 시 이벤트 리스너 폭증 | 좌표 클램핑, 유한 숫자 검증, `F12` 가드 상단 이동, `keydown` `useEffect` 의존성/ref 정리 |
| **UI-08** | **P1** | `frontend/src/components/cad/FeatureTimeline.tsx:6-11` | `ICON` 맵에 미존재 키(`PATTERN_LINEAR` 등)가 있고 `PATTERN` 및 `SHEET_*` 8종 누락 | 판금·패턴 타임라인이 모두 기본 `"●"` 표시 | `Record<Feature["feature_type"], string>` 타입 강제 + 누락 9종 아이콘 추가 |
| **UI-09** | **P1** | `frontend/src/app/model/[documentId]/page.tsx:1636-1689` & `ui.tsx:96-102` | `InlinePanel`(320px) + 우측 `속성`(320px) + 좌측 트리(256px) = 896px 동시 표시; `InlinePanel` `Escape`가 중첩 `<Modal>` 위로도 동작 | 1280px 노트북에서 3D 뷰포트 폭 ~384px로 압착; 모달 닫을 때 인라인 패널 동시 종료 | 인라인 다이얼로그 활성 시 우측 `속성` 숨김; `Escape` 핸들러에 `if (document.querySelector("dialog[open]")) return;` 가드 |
| **UI-10** | **P1** | `frontend/src/lib/i18n.tsx:133-140` & `LocaleSwitcher.tsx:1-21` | `LocaleSwitcher` 미마운트·`useI18n()` 호출 0곳(데드 코드), `localStorage`·`documentElement.lang` 미동기화, provider `useMemo` 누락, `<option>` 다크모드 배경 미지정 | 언어 전환 UI 없음 및 Windows 다크모드 드롭다운 가독성 저하 | 헤더에 `LocaleSwitcher` 배치, Phase 0·3 컴포넌트 `useI18n()` 연결, `useMemo`+`localStorage` 동기화, `<option className="bg-card text-foreground">` |
| **UI-11** | **P2** | `frontend/src/lib/cad/features.ts:666` & `SheetDialog.tsx:342` | `SIDE` 플랜지 전환 시 숨겨진 코너 옵션(`relief*`/`miter*`/`close*`) 상태 미초기화 및 `close_*` 미차단 | 숨겨진 필드로 인한 검증 오류 또는 `SIDE`에 잘못된 `close_*` 전송 | `d.side` 설정 시 코너 필드 초기화/무시 및 `close_*` 차단 포함 |
| **UI-12** | **P2** | `frontend/src/components/cad/SheetDialog.tsx:312-336` | `CONTOUR` 스타일에서도 미사용 `length`/`angle`/`up` 필드 표시 및 검증 | 윤곽 플랜지 입력 혼란 및 미사용 필드 공백 시 제출 차단 | `d.style === "CONTOUR"`일 때 해당 입력 숨김 및 검증 스킵 |
| **UI-13** | **P2** | `globals.css:83-90`, `SheetDialog.tsx:343`, `FeatureTimeline.tsx:47`, `ThreeViewport.tsx:233-238` | `[data-theme="dark"]` 모드 액센트 오버라이드 누락(`DESIGN.md:74-77`), `border-border` 오타(`border-line`), 타임라인 오류 아이콘 누락, ViewCube 하드코딩 색상 | 다크모드 대비 저하 및 `DESIGN.md` 불일치 | 다크모드 액센트 오버라이드, `border-line`, 오류 `⚠` 아이콘, `token()` 적용 |

---

## 4. 자동 회귀 테스트 현황

Antigravity가 아래 독립 회귀 테스트를 추가하여 P0/P1 결함을 재현·고정했습니다. (현재 미수정 버그는 `xfail(strict=True)` / `it.fails`로 표시되어, Claude Code가 수정 완료 시 즉시 `XPASS`로 감지됨)

- Backend/Core: `tests/test_sheetmetal_edge.py`
  - 경계 가드(통과): S4 성형 6종 경계 깊이, 180°/270° 헴 전개 메트릭, `NEST` 정원판/초과 폴백
  - 결함 노출(`strict xfail`): `SH-01`(U채널 유령 구멍), `SH-02`(날카로운 모서리 `R<=t` 음수 반경 & `rip_edges=전체` 크래시), `SH-03`(마이터 오목 플랜지 오탐), `SH-07`(`acadrust` 종료코드 무시 & `R12` 노출), `SH-08`(자기교차 구멍/`NaN` 통과)
- Frontend: `frontend/src/lib/cad/sheet.edge.test.ts`
  - 경계 가드 + 결함 노출(`it.fails`): `UI-05`(빈 문자열 `""` → `0` 변환), `UI-11`(`SIDE` 플랜지 `close_*` 누출), `UI-12`(`CONTOUR` 빈 `length`/`angle` 거부)

---

## 5. Claude Code 인계 프롬프트

### A. `TASK-007` (Backend 판금·DWG 결함 수정)
```text
docs/reviews/sheet-ui-dwg-review.md §2의 SH-01~SH-12(P0 2건, P1 6건, P2 4건)를 수정해 주세요.
- 브랜치: fix/claude-sheet-dwg-review (별도 worktree)
- 허용 범위: core/geometry/sheetmetal.py, core/geometry/unfold.py, core/dxf/dwg.py, core/quote_engine/cost.py, core/copilot/dfm.py, backend/api/routes_model.py, backend/api/main.py, tests/test_sheetmetal_edge.py
- 수정 후 tests/test_sheetmetal_edge.py의 해당 @pytest.mark.xfail을 제거해 모든 테스트가 일반 PASS 되도록 만들고, 아래 게이트 통과를 확인해 주세요:
  uv run ruff check . && uv run ruff format --check . && uv run mypy core backend && uv run pytest -q tests/
```

### B. `TASK-008` (Frontend `SheetDialog`·UI Phase 0·3 결함 수정)
```text
docs/reviews/sheet-ui-dwg-review.md §3의 UI-01~UI-13(P0 3건, P1 7건, P2 3건)를 수정해 주세요.
- 브랜치: fix/claude-ui-phase03-review (별도 worktree)
- 허용 범위: frontend/src/** (TASK-003 곡면 UI 신규 파일과 무충돌)
- 수정 후 frontend/src/lib/cad/sheet.edge.test.ts의 it.fails를 일반 it로 전환하고, 아래 게이트 통과를 확인해 주세요:
  pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test
```
