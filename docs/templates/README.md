# AX-CAD 표준 · 회사 업무용 CAD 템플릿 샘플 (`docs/templates/`)

AutoCAD(`.dwt`/`.dxf`), ZWCAD 및 AX-CAD 2D Draft / 3D Model에서 바로 활용할 수 있는 표준 템플릿 샘플입니다.

| 파일 | 코드 | 용도 | 기본 용지 · 축척 | 주요 포함 구성 |
|---|---|---|---|---|
| `AXCAD_COMPANY_INTERNAL_A3.dxf` | `COMPANY_INTERNAL` | **회사 업무용 (비공식 · 2D/3D 공통)** | A3 · `1:1` | 간이 표제란, 비공식 배너, `MEMO`·`QUOTE_REF` 레이어, 자재/공정 메모표, `SYM_INTERNAL_STAMP` |
| `AXCAD_MECHANICAL_KS_A3.dxf` | `MECHANICAL_KS` | **기계 · 제조 표준 (KS 제3각법)** | A3 · `1:1` | KS 윤곽선·중심마크, 제3각법 표제란, 부품란(BOM), `SYM_ROUGHNESS`·`SYM_GDT_PERP`·`SYM_THIRD_ANGLE`, 일반공차 주서 |
| `AXCAD_ARCHITECTURAL_KS_A1.dxf` | `ARCHITECTURAL_KS` | **건축 평면도 표준** | A1 · `1:100` | `A-WALL/DOOR/WIND/GRID` 레이어, 마감재 일람표, `SYM_NORTH`·`SYM_SECTION`·`BLK_DOOR_900`·`BLK_WINDOW_1800` |
| `AXCAD_INTERIOR_RENO_A3.dxf` | `INTERIOR_RENO` | **인테리어 리노베이션** | A3 · `1:50` | `I-DEMO/WALL/FURN/LIGHT/FINISH` 레이어, 마감·조명 일람표, `BLK_DESK_1600`·`BLK_SOFA_2100`·`SYM_LED_DOWNLIGHT` |
| `AXCAD_MEP_FACILITY_A3.dxf` | `MEP_FACILITY` | **전기 · 배관 · 공조(HVAC) 설비** | A3 · `1:100` | `E-POWER/LIGHT`, `P-WATER/DRAIN`, `M-HVAC` 레이어, 설비 범례표, `SYM_PANEL_BOARD`·`SYM_OUTLET`·`SYM_HVAC_FCU`·`SYM_VALVE` |

## AX-CAD에서 바로 사용하기
- **새 도면 (2D Draft / 3D Model)**: 프로젝트 화면 → **`새 도면`** → **도면 템플릿** 선택(기본값: `회사 업무용 (비공식 · 2D/3D 공통)`).
- **샘플 다운로드 API**: `GET /api/templates/{code}/download?sheet=A3`
- **AutoCAD/ZWCAD `.dwt` 변환**: 샘플 `.dxf`를 연 뒤 `[다른 이름으로 저장] → AutoCAD 도면 템플릿(*.dwt)`로 저장하고 `OP → 파일 → 템플릿 설정 → QNEW`에 등록하면 새 도면 시작 시 자동 적용됩니다.
