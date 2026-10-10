# MEMORY.md — AX-CAD 지속 메모리

확정된 결정과 함정만 둔다. 견적 공식·공정 매핑은 `.claude/rules/quote-engine.md`, 기하·렌더링 원칙은 `.claude/rules/cad-geometry.md`, 현재 상태·스키마는 `docs/HANDOFF.md`가 원본이다.

## ADR
- **01 기하 커널**: OCCT(B-Rep·Boolean·STEP AP242·IGES). **02 2D**: ezdxf. **03 UI**: 웹(shadcn/ui) + 독자 캔버스 렌더러. **04 추적성**: 엔티티 → 규칙 → 단가 → 견적 라인 `quote_trace` 영구 보존. **05 연동 격리**: CAD 본체와 ERP/MES/PLM API 분리.
- **06 OCCT 바인딩**: `cadquery-ocp-novtk==8.0.1.1.0` 고정, raw OCP 호출(pythonocc·VTK판 기각).
  - Feature 파라미터 JSON이 원천, BREP은 sha256 캐시. 프로세스 간엔 BREP 바이트만(`TopoDS_Shape` pickle 불가), 커널은 spawn 풀 + RLIMIT_AS.
  - 함정: `_s` 접미사 불일치, `OCP.collections`, Boolean에 `HasErrors()` 없음. IGES 정적 파라미터는 `IGESControl_Controller.Init_s()` 뒤에만 유효, IGES `ReadStream` 실패 → `ReadFile`. 빈 STEP은 `NbRootsForTransfer()==0`.
  - manylinux_2_28(Alpine 불가), OCCT LGPL(데스크톱 배포 시 고지).
- **07 견적 불변성**: 금액·원천·계보는 DB 트리거로 고정. 헤더 가드는 상태 전이 DRAFT↔IN_REVIEW→CONFIRMED→SUPERSEDED, DRAFT→ABANDONED만 허용.
  - 확정 견적은 Revision(COPY/RECALC, `<root>-R<n>`, 폐기 번호 미재사용)으로만 바꾼다. 승인자는 체인 전체 작성자·조정자 제외.
  - 정식 견적서는 첫 발행 bytes 저장, 산출근거는 초안 전용.
  - 증거 테이블이 있으면 downgrade 거부, 머지된 마이그레이션은 새 리비전으로만 변경.
- **08 ERP 연동**: 멱등 키 `BOM:{doc}:{revision|MODEL:hash16}`, 백그라운드 + `run_id` 펜싱, 최대 3회(5xx/408/425/429), 리다이렉트 불허.
  - https만(localhost 예외), 토큰은 환경변수만, 응답 본문은 ADMIN만.
  - `request_payload`는 불변이고 감사 로그엔 INSERT 1회(0014).

## 확정 정책 (사용자 결정)
- 미승인 도면도 구성원이면 열람 가능. 내보내기(DXF/STEP/IGES)만 ADMIN·DESIGNER 외엔 APPROVED/RELEASED로 제한.
- RELEASED 문서 수정 허용 → 새 리비전은 DRAFT로 재승인.
- 검증 오류는 422 `REQUEST_INVALID` + `details[{field,type}]`(입력 미반사).
- 감사 로그: REVIEWER는 구성원 프로젝트만, 기준정보 로그는 ESTIMATOR·ADMIN, 계정·ERP 로그는 ADMIN.
- PR은 GitHub Actions CI 녹색 후 머지.
- 사내 서버: Linux x86_64 + Docker(rootful), 사내 CA 없음, 인터넷 가능(폐쇄망 절차 불필요).
  - HTTPS는 자체 서명 leaf(`deploy/tls-selfsigned.sh`, CA:FALSE, 825일)를 PC가 신뢰. 사설 CA는 만들지 않는다(키 유출 시 모든 사이트 사칭).
  - 함정: proxy는 권한 없는 root라 TLS 키는 root 소유·600이어야 읽힌다(BUG-40).
- 실도면(기밀)은 클라우드 세션으로 옮기지 않는다. 사용자가 PC에서 `test_perf.py -k real`을 돌려 숫자만 전달.
