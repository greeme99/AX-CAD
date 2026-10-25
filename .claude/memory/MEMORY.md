# MEMORY.md — AX-CAD 지속 메모리

확정된 결정과 함정만 둔다. 견적 공식·공정 매핑은 `.claude/rules/quote-engine.md`, 기하·렌더링 원칙은 `.claude/rules/cad-geometry.md`, 현재 상태·스키마는 `docs/HANDOFF.md`가 원본이다.

## ADR
- **01 기하 커널**: OCCT(B-Rep·Boolean·STEP AP242·IGES). **02 2D**: ezdxf. **03 UI**: 웹(shadcn/ui) + 독자 캔버스 렌더러. **04 추적성**: 엔티티 → 규칙 → 단가 → 견적 라인 `quote_trace` 영구 보존. **05 연동 격리**: CAD 본체와 ERP/MES/PLM API 분리.
- **06 OCCT 바인딩**: `cadquery-ocp-novtk==8.0.1.1.0` 고정, raw OCP 호출(pythonocc·VTK판 기각).
  - Feature 파라미터 JSON이 원천, BREP은 sha256 캐시. 프로세스 간엔 BREP 바이트만(`TopoDS_Shape` pickle 불가), 커널은 spawn 풀 + RLIMIT_AS.
  - 함정: `_s` 접미사 불일치, `OCP.collections`, Boolean에 `HasErrors()` 없음. IGES 정적 파라미터는 `IGESControl_Controller.Init_s()` 뒤에만 유효, IGES `ReadStream` 실패 → `ReadFile`. 빈 STEP은 `NbRootsForTransfer()==0`.
  - manylinux_2_28(Alpine 불가), OCCT LGPL(데스크톱 배포 시 고지).
- **07 견적 불변성**: 계산값(`calculated_*`)·원천·계보는 DB 트리거로 고정, 조정은 `override_*`로만. **Track 7에서 결재·확정·Revision·정식본 발행을 제거**(견적은 늘 DRAFT, 견적서는 현재 값으로 즉시 출력). 상태 전이 트리거·발행 대장은 기존 기록 보존용으로 남는다.
  - 증거 테이블이 있으면 downgrade 거부, 머지된 마이그레이션은 새 리비전으로만 변경.
- **08 ERP 연동 → 파일(Track 7)**: 실연동(HTTP 전송·재시도·멱등) 폐지. BOM "ERP 업로드 파일(Excel)"을 사람이 ERP에 올린다(현재 도면 BOM·미매핑 0). `integration_jobs`는 기록 보존용.
- **09 FreeCAD 활용**(2026-10-21 사용자 결정): 2D 제도·3D 모델링 보완은 greeme99/FreeCAD를 **알고리즘 이식**(재구현)으로만 한다. FreeCAD 엔진 내장·코드 복사 안 함(배포 크기, conda/Qt, ADR-06 OCP 충돌, LGPL 바이너리). 대응표와 단순화 목록은 `plan.md` §11.
  - 가공 Feature(FILLET·CHAMFER·HOLE·PATTERN·MIRROR·SHELL)는 BOOLEAN처럼 입력 형상을 소비, 모서리·면은 Z 기준 규칙으로 선택.
- **10 견적 유형 확장**(2026-10-21 사용자 결정 "둘 다"): 제조(부품) 견적에 더해 **공사·설비 견적**(`CONSTRUCTION`: 평면도 QTO → 공종 단가 → 공사원가계산서)을 둔다. 지역·시점 보정, 유효기한, 시나리오·민감도는 두 유형 공통. 구성·단순화는 `plan.md` §12.
  - 함정: 공사 이윤 기준은 재료비 제외(`summarize(construction=True)`) — 헤더 합계 검증·요약·승인함 합계 모두 같은 플래그로 계산해야 한다. 비율 라인(`식`)은 보정·시나리오를 다시 곱하지 않는다.

- **11 운영 = PC별 설치**(2026-10-21 사용자 결정): 서버 없이 PC마다 Windows 설치본(`installer/windows`, `%LOCALAPPDATA%\AX-CAD`: app/data), 데이터 공유 없음, **로그인·결재 없음**, 기능 개선은 Ed25519 서명 패치(`installer/patcher.py`, 설치된 공개키가 기준)로. 구성은 `plan.md` §13, 사용법은 `docs/PC_설치_가이드.md`.
  - PC 모드는 `AXCAD_LOCAL_USER` + `AXCAD_LOCAL_TOKEN`(둘 다 있어야 켜짐) + `_local_guard`(localhost Host/X-Forwarded-Host/Origin, **모든** 요청에 `X-AXCAD-Local: <설치 토큰>`). 토큰은 `data\axcad.env`(계정 SID만 읽기) → `start.ps1`이 `/#axcad=<토큰>`으로 브라우저에 전달 → `lib/api.ts`가 localStorage에 보관. 변수를 빼면 로그인 방식 그대로(테스트·CI·`deploy/`).
  - 함정: PowerShell `Set-Content -NoNewline (식) 값`은 경로·값이 뒤바뀐다 → 항상 `-Path`·`-Value`를 쓴다.
  - 함정: Windows 설치본의 Next standalone은 pnpm 심볼릭 링크가 깨진다 → `build.ps1`은 `--config.node-linker=hoisted`로 설치. 패치 컴포넌트 해시는 `__pycache__`·`.pyc`를 뺀다(실행할 때마다 생겨 런타임이 바뀐 것처럼 보임).
  - 릴리스 서명은 별도 잡(`release` 환경 시크릿, 러너 Python `-I -S`), 빌드 잡은 키를 보지 않는다.
  - 함정: PS 스크립트는 Windows PowerShell 5.1 호환 + UTF-8 BOM(한글). 패처는 표준 라이브러리만(교체 중인 런타임의 복사본에서 실행). 런타임 바뀌면 전체 패치. PG 주 버전 변경은 패치 불가.
- **12 표준 CAD 완결 방식**(Track 8, 2026-10-25 사용자 결정·grill-me): **D′ 하이브리드** — OCCT 직접 호출 + FreeCAD 고유 로직만 클린룸 재구현(LGPL, 코드 복사 금지), 스케치 솔버는 numpy(새 의존성 없음). REA 미사용, FreeCAD는 개발용 검증 오라클로만(배포 안 함). 순서 P1 2D → P2 3D → P3 도면화 → P4 견적 연계 → P5 R1.0 릴리스·리허설. 근거 `plan.md` §14.
  - 참조: 원본 `freecad/freecad` + 포크 `greeme99/FreeCAD` + FreeCAD 조직 레포(대응표 §14.5). **FreeCAD-documentation(위키, CC0)은 명령 동작 명세로 직접 사용**, OndselSolver(LGPL)는 알고리즘만, Examples·symbols·library(CC-BY)는 조건 확인 후. 로컬 사본은 저장소 밖.
  - DWG: 사용자가 설치한 변환기만(ODA File Converter 또는 LibreDWG `dwg2dxf` — GPLv3라 별도 프로그램 호출만), 설치본 미포함. 비밀정보 없는 환경·고정 인자·시간 제한·프로세스 트리 종료.
  - 검증: 합성 샘플 자동 검증 + 실도면은 사용자 PC UAT(숫자만 공유). 릴리스: P1~P5 완료 설치본 = R1.0 기준선, 이후 업데이트(R1.1)·업그레이드(R2.0) 등급별 패치 확장.
  - 함정(ezdxf): 대칭(음수 축척) 블록 참조의 속성 위치를 `Insert.transform()`·`add_auto_attribs()`가 잘못 놓음 → 블록 좌표로 되돌려 새 `matrix44()`로 직접 배치(`writer._place`). `Matrix44.inverse()`는 제자리 변경(None 반환). LEADER는 `extrusion` 대신 `normal_vector`. `add_cad_spline_control_frame()`은 통과점을 저장하지 않음 → `fit_points_to_cad_cv` + `apply_construction_tool` 후 `fit_points` 직접 지정. 새 문서의 `EZDXF` 치수 스타일은 m 기준 → mm 도면은 `Standard` + 재정의.
  - 함정(렌더 데이터): 블록 확장 엔티티는 조각마다 부모 핸들 공유 → 편집 정의(`geom`)는 첫 조각에만 붙이고 조각 `type`은 그대로(형상 유형은 `geom.type`).

## 확정 정책 (사용자 결정)
- (Track 7) 결재 없음: 구성원이면 열람·내보내기 모두 가능, 견적은 언제나 수정·출력.
- 검증 오류는 422 `REQUEST_INVALID` + `details[{field,type}]`(입력 미반사).
- 감사 로그: REVIEWER는 구성원 프로젝트만, 기준정보 로그는 ESTIMATOR·ADMIN, 계정·ERP 로그는 ADMIN.
- PR은 GitHub Actions CI 녹색 후 머지.
- (보류, Track 7로 대체) 사내 서버: Linux x86_64 + Docker(rootful), 사내 CA 없음, 인터넷 가능(폐쇄망 절차 불필요).
  - HTTPS는 자체 서명 leaf(`deploy/tls-selfsigned.sh`, CA:FALSE, 825일)를 PC가 신뢰. 사설 CA는 만들지 않는다(키 유출 시 모든 사이트 사칭).
  - 함정: proxy는 권한 없는 root라 TLS 키는 root 소유·600이어야 읽힌다(BUG-40).
- 개발 단계 서버(2026-10 사용자 결정, Track 7 이후엔 `deploy/`가 개발·CI용): 회사 도입 전까지 **집의 개인 PC**(Windows 10 Home + Docker Desktop + WSL2 Ubuntu, RAM 8GB, Wi-Fi `192.168.45.110`)에서 개인 개발·테스트용으로 운영하고, 회사용 전환 시 위 사내 서버(Linux 전용 장비)로 백업·복구 이전한다.
  - 테스트 용도라 용량·성능 부족, 상시 가동·NAS 백업·디스크 암호화·Docker Desktop 상용 라이선스는 요구하지 않는다. 이 PC 성능값은 NFR-02 목표치 근거로 쓰지 않는다.
  - 설계·문서는 회사 전환을 전제로 유지(Linux 서버 절차가 원본), 개인 PC 전용 설정은 `.env`·Windows 쪽에만 둔다.
- 실도면(기밀)은 클라우드 세션으로 옮기지 않는다. 사용자가 PC에서 `test_perf.py -k real`을 돌려 숫자만 전달.
