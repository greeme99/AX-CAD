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
