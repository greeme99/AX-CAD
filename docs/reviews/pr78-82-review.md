# PR #78~#82 독립 코드·보안 리뷰 (곡면 UI/잔여 1~3 · 내장 DWG 변환기)

> 작성일: 2026-11-20 | 리뷰어: Antigravity | 기준 커밋: `origin/main` `d32f572` (PR #78~#82 머지)
> 범위: `core/dxf/dwg_builtin.py`, `core/dxf/dwg.py`, `core/geometry/surface_edit.py`, `core/geometry/surface_analysis.py`, `backend/api/routes_model.py`, `0035_surface_delete_face.py`, `frontend/src/components/cad/SurfaceDialog.tsx`, `ThreeViewport.tsx`, `DwgTools.tsx`, `features.ts`, `model/[documentId]/page.tsx`

---

## 1. 요약

| 영역 | P0 | P1 | P2 | 합계 | 양호 확인 |
|---|---:|---:|---:|---:|---|
| **Backend / Core (내장 DWG · 곡면 잔여 1~3)** | 0 | 5 | 3 | **8** | `0035_surface_delete_face.py` 대칭·안전, `dwg.py` 격리(`-I`·`ENV_KEEP`·`_Job`·`_plain_file`·`REPORT_CAP`) 양호 |
| **Frontend (`SurfaceDialog` · `ThreeViewport` · `DwgTools`)** | 0 | 5 | 4 | **9** | `zebraMat()` 해제 정상, `DwgTools` `builtin` 버전 `"2000"` 폴백 정상 |
| **총계** | **0** | **10** | **7** | **17** | — |

---

## 2. Backend / Core 지적 사항

| ID | 등급 | 파일:라인 | 문제 | 재현 / 영향 | 수정안 |
|---|---|---|---|---|---|
| **R82-01** | **P1** | `core/geometry/surface_edit.py:356-360, 127-186` | `surface_cut(..., {"plane": ...}, keep="positive"\|"negative")`가 `surface_trim` 조기 반환으로 분할 검사(`368-371`) 우회; `surface_trim`이 빈 `TopoDS_Compound` 미거부 | 빗나간 평면으로 `keep="positive"` 호출 시 0-face 빈 compound 반환, `keep="negative"`는 원본 그대로 반환 (`GEOM_INVALID_PARAM` 누락) | `:358-359` 조기 반환 제거해 `surface_split` 경로 통과; `surface_trim`에 `math.isfinite` 및 `_count(shape, TopAbs_FACE) > 0` 검증 추가 |
| **R82-02** | **P1** | `core/geometry/surface_analysis.py:330-390` | `draft_analysis`에 (a) `MAX_FACES`/`MAX_SAMPLE_POINTS` DoS 상한 누락, (b) `BRepClass_FaceClassifier` 트림 면 필터 누락 | 5,000면 STEP에 `n_u=200, n_v=200` 요청 시 $2\times 10^8$회 `D1()` 호출로 워커 정지; 트림 곡면은 면 밖 UV 점으로 거짓 `undercut: True` | `len(faces)<=MAX_FACES`, `n_u*n_v*len(faces)<=MAX_SAMPLE_POINTS`, `BRepClass_FaceClassifier` `TopAbs_OUT` 필터 추가 |
| **R82-03** | **P1** | `backend/api/routes_model.py:1383-1385` | `_base(p)`에 `SurfFillParams` 누락(`target_feature_id`가 선택 필드라 `_OnBody` 미상속) | 몸체 부착 `SURF_FILL` 억제 시 빈 바이트 `(b"", None)` 반환으로 몸체 소실, 후속 피처 소비 시 억제 불가 | `_base(p)`에 `if isinstance(p, SurfFillParams): return p.target_feature_id` 추가 |
| **R82-04** | **P1** | `core/dxf/dwg_builtin.py:69-99, 118-136, 163-166` | 비신뢰 DWG `read()`에서 `linetypes.add`/`blocks.new` 이름 미정제·예외 미포착, 엔티티별 `try/except` 누락, `_line_style` `isfinite`/`bool` 미검사 | 불량 블록명·선종류 또는 단일 손상 엔티티가 전체 DWG 가져오기 422 크래시 유발; `linetype_scale=inf` 시 손상 DXF 출력 | `_safe()` 정제 + `try/except Exception` 래핑(실패 엔티티는 `skipped` 집계), `_line_style` 유한수·범위(`0<=weight<=211`) 검증 |
| **R82-05** | **P1** | `core/dxf/dwg_builtin.py:264-273, 463-475` | `write()`에서 `virtual_entities()`가 `depth >= MAX_EXPLODE_DEPTH` 검사 전에 호출되고 총 엔티티 예산 없음; `_hatch`가 고정 `FLATTEN_MM=0.01`로 꼭짓점 무제한 생성 | 대형 반경 곡선 해치(`R=1e8`) 내보내기 시 메모리 폭증 및 `DWG_SLOTS` 120초 점유(타 사용자 503 DoS) | `depth` 선검사, `_Writer` 총 엔티티 상한, `_hatch` 경계별 꼭짓점 상한/적응형 허용오차 적용 |
| **R82-06** | **P2** | `core/geometry/surface_edit.py:404-423` | `fill_hole`이 중복 모서리·닫힌 원통 seam 모서리·`Degenerated` 모서리 및 `len(faces)+1 <= MAX_SEW_FACES` 선검사 누락 | 중복/seam 모서리 선택 시 `BRepFill_Filling` 실패·깨진 패치, 500면 초과 시 불필요한 커널 연산 | `IsSame` 중복 제거, 전체 면 합산 출현 횟수 `!= 1` 및 `Degenerated_s` 거부, `MAX_SEW_FACES` 선검사 |
| **R82-07** | **P2** | `core/geometry/surface_analysis.py:92-124, 153-160, 220-231` | `curvature_map`이 `samples`에 `NaN`/`Inf` 저장 가능(JSON 500 위험); `continuity_check`가 `TopAbs_REVERSED` 부호 무시 + `MAX_FACES` 누락 + $O(F^2 E^2)$ 스캔 | 원뿔/구 특이점에서 JSON 500; 반전된 인접 면에서 200% G2 오탐; 대형 STEP 이차 지연 | 비유한 샘플 스킵, `_curvature_at` `REVERSED` 부호 반전, `TopExp.MapShapesAndAncestors_s` 인접 맵 전환 |
| **R82-08** | **P2** | `core/dxf/dwg_builtin.py:351-357` | `_Writer._polyline`이 2D `POLYLINE`의 `elevation` Z값 누락 및 `len(pts) < 2` 미검사 | Z≠0 2D 폴리라인 DXF→DWG 왕복 시 Z 높이 손실 | `len(pts) < 2` 가드 및 `a["elevation"]` 보존 |

---

## 3. Frontend 지적 사항

| ID | 등급 | 파일:라인 | 문제 | 영향 | 수정안 |
|---|---|---|---|---|---|
| **R82-F01** | **P1** | `frontend/src/lib/cad/features.ts:816` & `SurfaceDialog.tsx:279` | `Math.min(...ok)` / `Math.max(...ok)` 배열 스프레드 사용 (백엔드 상한 `MAX_SAMPLE_POINTS = 200_000`) | 135면 이상 STEP 곡률 지도(12만~20만 점)에서 `RangeError: Maximum call stack size exceeded` 브라우저 크래시 | 단일 패스 `for` 루프로 `lo`/`hi` 계산 |
| **R82-F02** | **P1** | `frontend/src/components/cad/SurfaceDialog.tsx:236-243` | `SurfaceAnalysis.run()`에 `AbortController`/요청 ID 가드 및 `busy` 비활성화 없음 | 분석 중 다른 피처 선택 시 이전 몸체 점군이 새 몸체 위에 덮어쓰여짐; 더블클릭 중복 커널 호출 | `AbortController` + 단조 `reqId` 언마운트 정리, `busy`/`aria-busy` 추가 |
| **R82-F03** | **P1** | `frontend/src/app/model/[documentId]/page.tsx:1166,1181,1240` | `Properties` `재생성` 버튼이 `isSurf(f)`·`isSheet(f)`를 제외하지 않아 Boolean 경로로 폴스루 | 모든 곡면 피처에서 `재생성` 클릭 시 `"대상과 도구 형상을 선택하세요."` 오류 | `regen()` 및 버튼 조건에 `isSurf(f)`·`isSheet(f)` 제외 추가 |
| **R82-F04** | **P1** | `frontend/src/components/cad/ThreeViewport.tsx:243-269,289-302` | ViewCube `wireG`·6면 `PlaneGeometry`/`CanvasTexture`/`MeshBasicMaterial`/`EdgesGeometry`/`LineBasicMaterial` 클린업 미해제 | 스케치 전환·라우트 이동·React 19 StrictMode 시 GPU VRAM 누수 | `wireG.dispose()` 즉시 호출 및 클린업에서 `cubeScene` 순회 `dispose()` |
| **R82-F05** | **P1** | `frontend/src/components/cad/ThreeViewport.tsx:337-346` | `new THREE.EdgesGeometry(g, 20)` 무조건 생성 후 `mesh.edges?.length` 분기에서 미해제 덮어쓰기 | 매 메시 재빌드마다 `BufferGeometry` 누수 + CPU 낭비 | `else` 분기로 이동 |
| **R82-F06** | **P2** | `frontend/src/components/cad/ThreeViewport.tsx:381-386` | `cloud` effect 클린업이 `ctx.current === null`(이미 해제된 renderer)에도 `c.draw()` 호출 | 언마운트 시 해제된 WebGLRenderer 렌더 호출 | `if (ctx.current) c.draw();` 가드 |
| **R82-F07** | **P2** | `frontend/src/components/cad/ThreeViewport.tsx:103` | 렌더 중 `selRef.current = ...` 직접 변이 | React 19 concurrent render 규칙 위반 | `useLayoutEffect`로 이동 |
| **R82-F08** | **P2** | `frontend/src/components/cad/SurfaceDialog.tsx:38,210` | `setD({ ...d, refs })` 스테일 클로저 위험 및 제출 버튼 `aria-busy` 누락 | 상태 경합 및 접근성 불일치 | `setD((prev) => ({ ...prev, refs }))`, `aria-busy={busy}` |
| **R82-F09** | **P2** | `frontend/src/components/cad/DwgTools.tsx:29,271` | `program === null` 시 빈 `()` 출력; 일괄 가져오기 링크에 `<Link>` 대신 raw `<a>` 사용 | UI 깨짐 및 불필요한 전체 페이지 리로드 | `{c.program ? \` (${c.program})\` : ""}`, `<Link>` 교체 |

---

## 4. 회귀 테스트 및 Claude Code 인계
- 회귀 테스트 고정: `tests/test_pr78_82_edge.py` (`strict=True` xfail로 `R82-01`·`R82-02`·`R82-06`·`R82-08` 재현 고정 + 경계 가드)
- 인계 요약: 백엔드 `R82-01~08` (`fix/claude-pr78-82-backend`), 프론트엔드 `R82-F01~F09` (`fix/claude-pr78-82-frontend`, `TASK-008`과 통합 처리 가능).
