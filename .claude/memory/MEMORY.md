# MEMORY.md — AX-CAD 프로젝트 지속 메모리 (Curated Knowledge)

본 문서는 Research 분석을 통해 도출된 AX-CAD 시스템의 아키텍처 결정사항(ADR), 도메인 지식, CAD 기하 및 한국표준 견적 산출 규칙을 압축 보존한 지속 메모리다.

---

## 1. 아키텍처 결정 레코드 (ADR)
- **ADR-01 (기하 커널)**: 3D 모델링 및 교환 포맷의 코어로 OpenCASCADE(OCCT) 채택. B-Rep, Boolean, Extrude 및 STEP AP242, IGES 입출력 표준 지원.
- **ADR-02 (2D 도면 엔진)**: 2D 제도 및 파싱 라이브러리로 ezdxf 채택. Modelspace, Paperspace, Block layout 계층 구조 분리 접근.
- **ADR-03 (UI/UX 아키텍처)**: FreeCAD 워크벤치(Workbench) 구조와 shadcn/ui 기반 웹 클라이언트 결합. 캔버스는 독자 렌더러 분리. (대형 도면용 PyQt5 데스크톱 옵션 유지)
- **ADR-04 (견적 엔진 추적성)**: 도면 객체(Entity Handle) ↔ 제조 공정 ↔ 단가 매스터 ↔ 견적서 라인 간 1:1 역추적 데이터(`quote_trace`) 영구 보존.
- **ADR-05 (사내 연동 격리)**: CAD 클라이언트 본체와 ERP/MES/PLM 연계 API를 완전 분리하여 엔진 교체 시에도 기업 연계 영향도 최소화.
- **ADR-06 (OCCT 바인딩, 2026-10-09 승인)**: `cadquery-ocp-novtk==8.0.1.1.0`(OCCT 8.0.1) 정확 고정, raw OCP API 직접 호출(full `cadquery` 미도입). `pythonocc-core`(conda 전용, uv 비호환)·`cadquery-ocp`(VTK ~590MB, 서버 불필요) 기각. 근거: W3 스파이크 12/12, S5~S7 구현·테스트.
  - **데이터 원칙**: Feature 파라미터 JSON(IMPORT는 sha256 주소 업로드 원본)이 원천, BREP은 sha256(Feature 체인 + OCP 버전) 키 캐시, mesh/STEP은 파생물.
  - **프로세스 경계**: `TopoDS_Shape`는 pickle 불가 → BREP 바이트(`BinTools.Write_s(shape, buf, False, False, VERSION_4)`)만 전달. 커널은 상주 spawn 풀(워커당 1작업, `Interface_Static` 전역), 워커 사망 → 422 `GEOM_*`, 워커 RLIMIT_AS 상한.
  - **OCCT 8 함정**: `_s` 접미사 불일치(`TopoDS.Face`/`TopoDS.Shell`엔 없음), 컬렉션은 `OCP.collections`(`Sequence_TDF_Label` 등), Boolean API에 `HasErrors()` 없음, writer는 stdout 출력, 견적 BBox는 `AddOptimal_s`.
  - **STEP/IGES 함정**: STEP 스키마는 writer 생성 후 설정·반환값 확인. IGES 정적 파라미터(`write.iges.brep.mode`)는 `IGESControl_Controller.Init_s()` 전에는 조용히 무시(면만 기록). `IGESCAFControl_Reader.ReadStream`은 정상 파일도 실패 → 저장 파일 `ReadFile`. STEP `ReadStream`은 정상, 빈 STEP은 `NbRootsForTransfer() == 0`.
  - **배포·라이선스**: Linux manylinux_2_28(glibc ≥ 2.28, Alpine 불가). OCP Apache-2.0, OCCT LGPL-2.1+예외(사내 서버는 의무 없음, 데스크톱 배포 시 교체 가능한 공유 라이브러리 + 고지).

---

## 1.1 확정 정책 (2026-10-09 사용자 결정, 현재 동작 유지)
- **미승인 도면 열람**: 프로젝트 구성원이면 역할과 무관하게 미승인 도면의 render/diff 및 3D mesh를 열람 가능. 파일 **내보내기**(DXF/STEP/IGES)만 ADMIN·DESIGNER 외에는 APPROVED/RELEASED로 제한.
- **RELEASED 문서 수정**: 허용하며, 새 리비전 업로드·편집 시 상태가 DRAFT로 돌아가 재승인 필요.

---

## 2. 도면 메트릭 기반 한국표준 견적 산출 공식
- **원가 구성 체계**:
  $$\text{총원가} = (\text{직접재료비} + \text{직접노무비} + \text{제조간접비}) \times (1 + \text{일반관리비율}(5\sim8\%))$$
  $$\text{견적공급가액} = \text{총원가} \times (1 + \text{이윤율}(7\sim15\%))$$
  $$\text{최종견적금액} = \text{견적공급가액} \times 1.1 (\text{VAT } 10\%)$$
- **도면 형상 계측 매핑**:
  - 2D 외곽 절단 길이(Cutting Length) → 레이저/NCT 가공시간(M/H) 및 노무비 산출
  - 2D 절곡선 수 및 두께 → 프레스 브레이크(V-Bending) 공수 산출
  - 3D B-Box 체적 & 비중(Density) → 원자재 소요량 및 블록 중량 산출
  - 3D 표면적(Surface Area) → 표면처리(아노다이징, 분체도장, 아연도금) 비용 산출

---

## 3. 핵심 데이터 모델 및 ERD 엔티티
- **Source Layer**: `source_document`, `source_file`, `source_entity`(선/원/치수), `source_part`
- **Rule & Price Layer**: `process_rule`(절단/절곡/가공 기준공수), `price_master`(재질별 단가, 임률)
- **Quote Layer**: `quote_header`, `quote_line`(품번, 공정, 수량, 공급가), `quote_trace`(엔티티 역추적), `quote_revision`
- **Audit & Workflow**: `approval_workflow`, `audit_log`, `integration_job`(ERP 전송 이력)

---

## 4. CAD 기하 구현 가이드라인
- **공간 인덱스**: 마우스 스냅 탐색 시 전체 순회 금지, R-tree 또는 공간 격자 버킷 인덱싱 필수.
- **3D 사전 검증**: Extrude/Boolean 수행 전 open wire, self-intersection, degenerate edge 예외처리 필수.
- **수동 보정 격리**: 사용자가 견적 수치 변경 시 원본 자동 계산값(`calculated_value`)은 보존하고 `override_value`와 사유를 기록.
