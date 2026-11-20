# DECISIONS & DRAFT SPEC — CAM · CAE (`plan.md` §24) 범위 확정 초안

> **작성자**: Antigravity (2026-11-20) | **근거**: `plan.md` §24 | **착수 조건**: `plan.md` §23.10(곡면 UI `TASK-003` + 곡면 리뷰 수정 `TASK-005` + 조립 A1~A4 `TASK-002` 완료 후)
> **목적**: 착수 전 사용자가 6개 의사결정(`DEC-*`)만 확정하면 즉시 Claude Code 구현 명세로 전환할 수 있도록 기술·라이선스·리스크 선택지를 사전 정리한다.

---

## 1. 착수 전 사용자 확정 필요 항목 (`/grill-me` 체크리스트)

| ID | 의사결정 항목 | 선택지 (권장 = ★) | 판단 포인트 |
|---|---|---|---|
| **DEC-CAM-01** | CAM 착수 순서 | **★A**: C1(공구·2.5축 밀링) → C2(3D 인식·시뮬) → C3(후처리·견적) → C4(레이저)<br>**B**: C4(판금/2D 레이저 절단) 최우선 → C1~C3 | 판금 S1~S5·전개도가 완료되어 있어 **B**는 즉시 현장 ROI가 높고, **A**는 `plan.md` §24 원안 순서 |
| **DEC-CAM-02** | G코드 후처리기 대상 | **★A**: Fanuc 일반형(밀링 `G0/G1/G2/G3/G81/M3/M5/M30`) + 일반 레이저(`M3/M5` 파워 제어) 2종<br>**B**: 사내 보유 CNC/레이저 특정 모델 1~2종 우선(컨트롤러 기종 제공 필요) | 기계별 전용 후처리기는 §24 제외 범위; 사내 기종 헤더/푸터 템플릿만 커스텀 가능 |
| **DEC-CAM-03** | 공구·절삭조건 마스터 | **★A**: 기존 G1 엑셀 양식에 `17_공구절삭조건` 시트 추가 + DB 버전 동결(`ACTIVE`)<br>**B**: CAM 전용 독립 테이블(버전 동결 없음) | **A**는 기존 감사·견적 추적성(`FN-16/18`)과 100% 일관 |
| **DEC-CAM-04** | CAM 시간 → 견적 연계 | **★A**: 선택적 연동(CAM 작업이 있으면 실경로 가공시간 사용, 없으면 기존 `machining.py` 추정치 유지, `quote_trace`에 `CAM_TOOLPATH` 기록)<br>**B**: 견적과 분리(시간 표시만) | **A**가 AX-CAD 핵심 차별점(추적성) 극대화 |
| **DEC-CAE-01** | FEA 메시·솔버 스택 (라이선스·설치본 크기 핵심) | **★A (하이브리드 내장)**: `scipy` sparse 솔버(BSD-3, ~35MB) + LGPL 메셔 또는 자체/경량 Tet4 솔버(`core/cae/`)<br>**B (외부 어댑터, DWG ODA 패턴)**: 사용자 PC에 `Gmsh`+`CalculiX(ccx)` 별도 설치 후 격리 호출(설치본 크기 +0MB, GPL 분리)<br>**C (최소 MVP)**: 2D 평면응력/보·판 해석만 순수 `numpy` | 아래 §2 라이선스·크기 비교표 참조 (**GPL 감염 차단 필수**) |
| **DEC-CAE-02** | CAE 범위 재확인 | **★A**: 단품 선형 정적 FEA(변위·von Mises·안전율)만, CFD·비선형·접촉 제외(`plan.md` §24 원안)<br>**B**: 열전도/모달 추가 (일정 2배) | CFD는 Windows 설치본 크기·OpenFOAM(GPLv3) 문제로 제외 유지 권장 |

---

## 2. CAE 메시·솔버 라이선스 및 Windows 설치본 리스크 비교

> AX-CAD는 **Windows PC 설치본**(`installer/windows`, Python 3.12 + `uv.lock` + `THIRD_PARTY_NOTICES.md`)으로 배포된다. `uv.lock` 변경 시 패치 등급은 **UPGRADE**(메이저 업그레이드)가 된다(`MEMORY.md` ADR-11).

| 후보 도구 | 역할 | 라이선스 | Windows 휠/바이너리 | 설치본 증가 | 리스크 / 판정 |
|---|---|---|---|---:|---|
| **`numpy`** (기존 설치됨) | 밀집 행렬 / 소규모 솔버 | BSD-3 | 이미 포함 | 0 MB | 3D Tet 메시(>5,000 자유도)는 밀집 행렬 메모리 폭발 → 희소 행렬 필수 |
| **`scipy`** (`scipy.sparse.linalg`) | 희소 선형 솔버 (`spsolve`/`cg`) | BSD-3 | 공식 win_amd64 휠 | ~35 MB | **안전(권장)**. 10만 자유도 선형 정적 FEA 1~3초 풀이 |
| **`gmsh`** | 3D 사면체 자동 메시 (STEP/BREP → Tet4/10) | **GPLv2+** (예외 조항 있음, 단 링크 주의) | 공식 win_amd64 휠 / CLI | ~45 MB | **주의**: Python 바인딩 내장 시 GPL 고지·호환성 검토 필수. 별도 CLI 호출 시 분리 가능 |
| **`CalculiX (ccx)`** | 검증된 산업용 FEA 솔버 (정적/모달/열) | **GPLv2** | Windows exe 존재 | ~30 MB (외부) | **외부 호출(DWG 패턴)** 시 GPL 분리 + 높은 해석 신뢰도. 단 사용자 별도 설치 필요 |
| **`OpenFOAM`** | CFD 유체 해석 | **GPLv3** | Windows 네이티브 미지원(WSL/Docker 의존) | >500 MB | **제외 확정**: PC 설치본(WSL 미요구) 아키텍처와 불일치 |

### 권장 아키텍처 (`DEC-CAE-01` A안 vs B안)
- **사내 폐쇄/간편 설치 최우선** → **A안**: `scipy` (BSD-3) 추가(R3.0 `UPGRADE` 1회) + 자체 Tet4 선형 정적 솔버(`core/cae/fea.py`) + 메셔는 LGPL 호환(`netgen`/OCCT 기반) 또는 선택적 외부 `gmsh` CLI.
- **해석 정밀도·무의존성 최우선** → **B안**: DWG 변환기(`ODA`/`LibreDWG`)와 동일하게 **외부 도구 격리 어댑터**(`AXCAD_GMSH_PATH`, `AXCAD_CCX_PATH`, 비밀 환경변수 제거·Job Object 메모리/시간 상한·프로세스 트리 종료).

---

## 3. 단계별 마일스톤 및 모듈 구조 초안

| 단계 | 범위 | 신규 모듈 / 마이그레이션(예정) | 선행 조건 |
|---|---|---|---|
| **C1** | 공구·절삭조건 마스터 + 2.5축 밀링 경로(윤곽·포켓 오프셋·드릴) | `core/cam/tools.py`, `core/cam/mill25d.py`, `0039_cam_setup` | 조립 A4 완료 |
| **C2** | 3D 구멍/포켓 피처 자동 인식 + 프론트 3D 공구경로 애니메이션 시뮬레이션 | `core/cam/recognize.py`, `frontend/src/components/cad/CamPanel.tsx` | C1 |
| **C3** | 후처리기(Fanuc 일반형) + G코드 내보내기 + 가공시간 견적(`quote_trace`) 연계 | `core/cam/post.py`, `backend/api/routes_cam.py`, `core/quote_engine/` | C2 |
| **C4** | 레이저/판금 절단 경로(전개도 DXF·2D 도면 → 리드인/리드아웃·내부 우선 절단 순서·공용선) | `core/cam/laser.py` | C1 (또는 독립 선행 가능) |
| **E1** | 자동 메시 생성 + 경계조건(고정면·분포하중·압력, `core/geometry/refs.py` 기하 서명 재사용) | `core/cae/mesh.py`, `0040_cae_studies` | `DEC-CAE-01` 확정 |
| **E2** | 선형 정적 FEA 솔버(변위·von Mises·안전율) + 해석해 검증 스위트 | `core/cae/fea.py`, `backend/api/routes_cae.py` | E1 |
| **E3** | 3D 결과 색 지도(변위 과장 슬라이더·응력/안전율 컬러바 — `surfaceAnalysis.ts` 공용화) + PDF 보고서 | `frontend/src/components/cad/CaePanel.tsx` | E2, `TASK-003` |

---

## 4. 보안 · 안전 · 성능 가드레일

1. **NC/G코드 실기계 안전 고지**: 모든 생성 G코드 상단에 `(AX-CAD SIMULATION/DRAFT ONLY - VERIFY ON DRY-RUN BEFORE MACHINING)` 주석 필수 삽입, 공구 번호·스핀들/이송 상한(`MAX_RPM`, `MAX_FEED`) 검증, 프로그램명/주석 내 제어문자·줄바꿈 주입 차단.
2. **워커 자원 상한**: CAM 경로 생성(`MAX_SEGMENTS = 500_000`, `TIMEOUT = 60s`), FEA 메시/풀이(`MAX_NODES = 100_000`, `MAX_ELEMENTS = 400_000`, `TIMEOUT = 120s`, `RLIMIT_AS` / Windows Job Object 메모리 상한).
3. **외부 솔버 호출 시(B안)**: `test_dwg_windows.py`·언더레이 워커와 동일하게 비밀 환경변수 제거, 고정 인자 리스트(`shell=False`), 임시 폴더 자동 정리.
4. **프론트 공용화**: `TASK-003`에서 만들 `frontend/src/lib/cad/surfaceAnalysis.ts`의 컬러바·버텍스/면 색 지도 유틸을 E3 응력/안전율 색 지도에 그대로 재사용해 중복 방지.

---

## 5. 완료 검증 기준 (`plan.md` §24 매핑)

| TC | 영역 | 검증 사례 | 통과 기준 |
|---|---|---|---|
| `CAM-T01` | C1 | 100×60 포켓(공구 Ø10, 스텝오버 50%, 깊이 10, 절입 2) | 총 절삭 길이·가공 시간 = 손계산 ±0.5%, 미절삭 잔여 섬 0 |
| `CAM-T02` | C2 | 3D 파트(구멍 4개 + 포켓 1개) 자동 인식 | 피처 좌표·깊이·지름 100% 일치(`1e-6`), 시뮬레이션 경로 렌더링 오류 0 |
| `CAM-T03` | C3 | Fanuc G코드 내보내기 → 파서 왕복 | 좌표·이송·안전 헤더 일치, 견적 라인에 `CAM_TOOLPATH` trace 기록 |
| `CAM-T04` | C4 | 판금 전개도(외곽 + 내부 구멍 4개) 레이저 경로 | 내부 구멍 먼저 절단 → 외곽 마지막 절단, 리드인 충돌 0 |
| `CAE-T01` | E1~E2 | 외팔보(강재 `E=210 GPa`, `L=200`, `b=20`, `h=10`, 끝단 `F=100 N`) | 최대 처짐 `PL³/(3EI)` 대비 오차 **≤ ±2%**, 최대 굽힘응력 `Mc/I` ±5% |
| `CAE-T02` | E2 | 단순 지지 평판 균일 압력 | 해석해 중앙 처짐 대비 오차 **≤ ±3%**, 안전율 `Sy / σ_vm` 일치 |
| `CAE-T03` | E3 | E2E 브라우저(경로 표시·G코드 다운로드·응력 색 지도·PDF 보고서) | 페이지 오류·CSP 위반 0, WebGL 메모리 누수 0 |
