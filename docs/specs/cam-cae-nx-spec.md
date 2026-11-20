# SPEC — CAM · CAE NX 수준 고도화 상세 명세서 (`CAM-CAE-NX-01`)

> **작성자**: `ANTIGRAVITY-PM` (2026-11-20) | **상태**: 사용자 4대 의사결정 확정 완료 (`CAM-CAE-NX-01`)
> **선행 조건**: R3.0 `origin/main` (`e223894`, PR #95 CAM C1~C4 `0039`, PR #97 CAE E1~E3 `0040` 완료)
> **의존성 원칙**: 기존 스택(`OCCT 8.0` + `NumPy` + `SciPy` BSD-3) 100% 유지 — 신규 외부 의존성 0, GPL 비감염.

---

## 1. 확정 의사결정 요약 (`CAM-CAE-NX-01`)

| 항목 | 확정 내용 |
|---|---|
| **CAM 고도화 (`C5~C8`)** | 3축 자유곡면(황삭·등고선/스캘럽 정삭) + 2축 선반(Turning) + 3+2축 인덱싱 + 스톡 제거/충돌 시뮬레이션 + Siemens 840D / Heidenhain TNC / Fanuc Lathe 후처리기 |
| **CAE 고도화 (`E4~E7`)** | 2차 Tet10 정밀 요소 + 고유진동수(Modal) + 선형 좌굴(Buckling) + 정상상태 열전달/열응력(Thermal-Structural) |
| **의존성 정책** | `OCCT` + `NumPy` + `SciPy`(BSD-3) 유지, 신규 패키지 추가 없음 |
| **실행 순서** | 명세(`cam-cae-nx-spec.md`) + `docs/8` V1.3 동기화 → `TASK-017`(CAM C5~C8) → `TASK-018`(CAE E4~E7) |

---

## 2. 데이터베이스 마이그레이션 설계

### `0041_cam_nx_upgrade.py` (Revises `0040`)
- `cam_setups.kind` CHECK 확장: `('MILL_25D', 'LASER_2D', 'MILL_3D', 'TURNING_2AX', 'INDEXED_5AX')`
- `cam_setups.post_target` CHECK 확장: `('FANUC_MILL', 'GENERIC_LASER', 'SIEMENS_840D', 'HEIDENHAIN_TNC', 'FANUC_LATHE')`
- `cam_operations.op_type` CHECK 확장: `('CONTOUR', 'POCKET', 'DRILL', 'LASER_CUT', 'ROUGH_3D', 'FINISH_WATERLINE', 'FINISH_SCALLOP', 'TURN_ROUGH', 'TURN_FINISH', 'TURN_GROOVE', 'TURN_THREAD', 'INDEXED_3PLUS2')`

### `0042_cae_nx_upgrade.py` (Revises `0041`)
- `cae_studies` 컬럼 확장:
  - `analysis_type text NOT NULL DEFAULT 'STATIC' CHECK (analysis_type IN ('STATIC', 'MODAL', 'BUCKLING', 'THERMAL', 'THERMAL_STRUCTURAL'))`
  - `element_order smallint NOT NULL DEFAULT 1 CHECK (element_order IN (1, 2))` (`1` = Tet4, `2` = Tet10)
  - `density_kg_m3 numeric(10,2) DEFAULT 7850 CHECK (density_kg_m3 > 0)`
  - `thermal_conductivity_w_mk numeric(10,4) DEFAULT 50.0 CHECK (thermal_conductivity_w_mk > 0)`
  - `thermal_expansion_1_k numeric(12,9) DEFAULT 0.000012 CHECK (thermal_expansion_1_k >= 0)`
  - `ambient_temp_c numeric(8,2) DEFAULT 20.0`
  - `thermal_bcs jsonb NOT NULL DEFAULT '[]'::jsonb`
  - `mode_count smallint DEFAULT 6 CHECK (mode_count BETWEEN 1 AND 20)`
  - `summary_metrics jsonb NOT NULL DEFAULT '{}'::jsonb` (고유진동수 `frequencies_hz`, 좌굴하중계수 `buckling_factors`, 최대/최소 온도 `min_temp_c`/`max_temp_c`)

---

## 3. CAM NX 고도화 상세 명세 (`TASK-017`: C5 ~ C8)

### C5. 3축 자유곡면 가공 (`core/cam/mill3d.py`)
- **B-Rep 높이맵 / 드롭커터 샘플링**: OCCT `TopoDS_Shape` 또는 분석 곡면에 대해 볼엔드밀(`ENDMILL_BALL`)·플랫엔드밀 반경 보정 드롭커터 Z-map 생성 (`MAX_GRID_PTS = 200_000`).
- **공정 3종**:
  - `rough_3d(...)`: Z-레벨 적응형 황삭(스톡 여유량 `stock_allowance_mm` 유지, 다중 Z 슬라이스).
  - `finish_waterline(...)`: 등고선(Z-constant) 정삭 경로(급경사 영역).
  - `finish_scallop(...)`: 볼엔드밀 스캘럽 높이 `h = R - sqrt(R² - (s/2)²)` 기반 일정 스캘럽(`target_scallop_mm`) 스텝오버 정삭.

### C6. 2축 CNC 선반 가공 (`core/cam/turning.py`)
- **회전체 프로파일 `[(z, x_radius), ...]` 입력** (직경 프로그래밍 `X = 2 * r`, 축 방향 `Z`):
  - `turn_rough(...)`: 외경(OD)/내경(ID) 다중 패스 황삭(`doc_mm`, 여유량 `finish_allowance_mm`, 공구 노즈 R 보정).
  - `turn_finish(...)`: 프로파일 최종 정삭.
  - `turn_groove(...)`: 홈 가공(플런지 + 스텝오버).
  - `turn_thread(...)`: `G76` 나사 절삭 사이클(피치 `pitch_mm`, 나사산 깊이, 다중 패스).

### C7. 3+2축 인덱싱 가공 (`core/cam/indexed5ax.py`)
- **경사 가공면(Tilted Workplane)**: 목표 법선 벡터 `n = (nx, ny, nz)` → 회전축 각도 `(A, B, C)` 추출 및 로컬 WCS 변환 행렬 계산.
- **컨트롤러별 틸트 명령**:
  - Fanuc: `G68.2 X.. Y.. Z.. I.. J.. K..` / `G53.1` / `G69`
  - Siemens 840D: `CYCLE800(...)` / `CYCLE800()`
  - Heidenhain TNC: `PLANE SPATIAL SPA.. SPB.. SPC.. STAY` / `PLANE RESET`
- **홀더/생크 충돌 검사**: 공구 돌출 길이(`flute_length_mm`, `holder_radius_mm`) 대비 워크피스 바운딩/높이맵 간섭 검사.

### C8. 스톡 제거·충돌 시뮬레이션 & 다중 컨트롤러 후처리기 (`core/cam/stock_sim.py`, `core/cam/post.py`)
- **2.5D/3D 덱셀(Dexel/Height-field) 스톡 시뮬레이션 (`stock_sim.py`)**:
  - 초기 블랭크 스톡 Z-그리드에서 각 절삭 세그먼트를 스윕 제거 → 제거 부피(`removed_volume_mm3`), 잔삭량(`remaining_volume_mm3`), **급속 이송 중 스톡 충돌(RAPID_COLLISION)** 및 **타깃 과절삭(GOUGE)** 건수 반환.
- **신규 후처리기 3종 (`post.py`)**:
  - `SIEMENS_840D`: `; AX-CAD SIMULATION/DRAFT ONLY...`, `G90 G17 G71 G40`, `T="..." D1 M6`, `CYCLE800`, `M30`
  - `HEIDENHAIN_TNC`: `; AX-CAD SIMULATION/DRAFT ONLY...`, `BEGIN PGM ... MM`, `TOOL CALL`, `L X.. Y.. Z.. F..`, `PLANE SPATIAL`, `END PGM ... MM`
  - `FANUC_LATHE`: `G99 G21 G40`, `G50 S{max_rpm}`, `G96 S{css} M03`, `G00/G01/G76`, `M05`, `M30`
  - 모든 후처리기 동일하게 안전 헤더 강제 + 제어문자/주석 주입 422 차단.

---

## 4. CAE Simcenter 고도화 상세 명세 (`TASK-018`: E4 ~ E7)

### E4. 2차 사면체 요소 Tet10 (`core/cae/tet10.py`)
- 기존 Tet4 메시의 6개 모서리 중점을 자동 생성해 10절점 2차 사면체(**Tet10**)로 승격 (`upgrade_tet4_to_tet10`, `MAX_NODES = 100_000` 사전 검증).
- 4점 Gauss 적분($\xi_a, \eta_a, \zeta_a$ 대칭 구적)으로 30×30 요소 강성 행렬 $K_e = \sum_{g=1}^{4} w_g |J_g| B_g^T D B_g$ 조립 → **전단/체적 잠김(locking) 원천 해소**.

### E5. 고유진동수(모달) 해석 (`core/cae/modal.py`)
- 일관/집중 질량 행렬 $M$ 조립 ($\rho$ = `density_kg_m3`, 단위 일관성: $\text{kg/m}^3 \to \text{ton/mm}^3 = 10^{-12}$로 N·mm·s·MPa 정합).
- `scipy.sparse.linalg.eigsh(K_ff, k=mode_count, M=M_ff, sigma=0.0, which='LM')`로 최저차 `k`개 고유진동수 $f_i = \frac{\sqrt{\omega_i^2}}{2\pi}$ (Hz) 및 정규화 모드 형상 $\phi_i$ 산출.
- 검증 기준(`CAE-T07`): 외팔보 1차 굽힘 고유진동수 해석해 $f_1 = \frac{1.8751^2}{2\pi L^2}\sqrt{\frac{E I}{\rho A}}$ 대비 **±3% 이내**.

### E6. 선형 좌굴 해석 (`core/cae/buckling.py`)
- 1단계 정적 해석으로 초기 응력장 $\sigma_0$ 산출 → 요소별 기하 강성 행렬 $K_G(\sigma_0)$ 조립.
- 일반화 고유치 문제 $K_{ff} \phi = -\lambda K_{G,ff} \phi$ 풀이 → 최소 양의 좌굴 하중 계수 $\lambda_{cr}$ 및 좌굴 모드 형상 산출.
- 검증 기준(`CAE-T08`): 외팔 기둥 축압축 오일러 좌굴 하중 $P_{cr} = \frac{\pi^2 E I}{(2L)^2}$ 대비 $\lambda_{cr} \cdot P$ 오차 **±5% 이내**.

### E7. 정상상태 열전달 및 열응력 연성 해석 (`core/cae/thermal.py`)
- 3D 스칼라 열전도 강성 행렬 $K_T$ 조립 ($k$ = `thermal_conductivity_w_mk`), 경계조건: 고정 온도(`FIXED_TEMP`, Dirichlet), 대류(`CONVECTION`, Robin $h(T - T_\infty)$), 열유속(`HEAT_FLUX`).
- 온도 분포 $T$ 풀이 후 열변형률 $\varepsilon_{th} = \alpha (T - T_{ref}) [1,1,1,0,0,0]^T$에 의한 등가 열하중 벡터 $f_{th} = \int B^T D \varepsilon_{th} dV$ 조립 → 정적 FEA 연성 풀이로 열변위 및 열응력 $\sigma = D(B u_e - \varepsilon_{th})$ 산출.
- 검증 기준(`CAE-T09`): 1D 열전도 선형 온도 구배(오차 `≤ 1e-4`) + 양단 구속 봉 열응력 $\sigma = E \alpha \Delta T$ (오차 **±2% 이내**).

---

## 5. 검증 테스트 시나리오 (`CAM-T06~T10`, `CAE-T06~T10`)

| TC | 모듈 | 검증 내용 | 통과 기준 |
|---|---|---|---|
| `CAM-T06` | C5 (`mill3d.py`) | 3D 돔/곡면 볼엔드밀 황삭 + 스캘럽 정삭(`h=0.05mm`) | 스캘럽 스텝오버 공식 일치, Z-map 충돌 0 |
| `CAM-T07` | C6 (`turning.py`) | 다단 축 OD 황삭/정삭/홈/`G76` 나사 + `FANUC_LATHE` 후처리 | 직경 `X=2r`, `G96`/`G76` 출력, 주입 차단 |
| `CAM-T08` | C7 (`indexed5ax.py`) | 경사면(45°) 3+2 인덱싱 → Fanuc `G68.2` / Siemens `CYCLE800` / Heidenhain `PLANE SPATIAL` | 오일러 각도 역검증 `≤ 1e-6`, 홀더 충돌 감지 |
| `CAM-T09` | C8 (`stock_sim.py`) | 덱셀 스톡 제거 시뮬레이션 + 급속 충돌/과절삭(gouge) 탐지 | 제거 부피 일치, 의도적 급속 관통 시 `RAPID_COLLISION > 0` |
| `CAM-T10` | C5~C8 API | `0041` 마이그레이션 + `routes_cam.py` 3D/선반/3+2/Siemens/Heidenhain E2E | 201/200 정상, 제어문자 주입 422 |
| `CAE-T06` | E4 (`tet10.py`) | 2차 Tet10 외팔보 순수 3D FEA(보정 계수 없이) | 처짐 `PL³/(3EI)` **≤ ±2%** (Tet4 잠김 해소 검증) |
| `CAE-T07` | E5 (`modal.py`) | 강재 외팔보 고유진동수 1차~3차 모드 | 1차 굽힘 고유진동수 해석해 대비 **≤ ±3%** |
| `CAE-T08` | E6 (`buckling.py`) | 외팔 기둥 축압축 오일러 좌굴 | $\lambda_{cr} P$ vs $\pi^2 E I / (2L)^2$ **≤ ±5%** |
| `CAE-T09` | E7 (`thermal.py`) | 1D 정상상태 열전도 + 양단 구속 봉 열응력 | 온도 구배 오차 `≤ 1e-4`, 열응력 $E\alpha\Delta T$ **≤ ±2%** |
| `CAE-T10` | E4~E7 API + UI | `0042` 마이그레이션 + `routes_cae.py` MODAL/BUCKLING/THERMAL + `CaePanel` | 200 정상, PDF 보고서 면책 문구 포함, `dispose()` 0 누수 |
