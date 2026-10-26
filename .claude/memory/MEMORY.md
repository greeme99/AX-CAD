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
  - 가공 Feature(FILLET·CHAMFER·HOLE·PATTERN·MIRROR·SHELL)는 BOOLEAN처럼 입력 형상을 소비, 모서리·면은 Z 기준 규칙으로 선택(Track 8 P2부터 3D 직접 선택도 가능, ADR-12).
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
  - P2 3D(2026-10-26 완료): 고른 모서리·면은 번호가 아니라 기하 특징(종류·중간점·방향·길이/넓이·반지름, `core/geometry/refs.py`)으로 저장한다. 재생성 때 같은 종류·방향·반지름 중 몸체 크기 10% 안의 가장 가까운 것을 쓰고, 없으면 `GEOM_REF_LOST`.
  - 스케치 윤곽은 FreeCAD 규칙: 포함 관계로 겹쳐 홀수 깊이는 구멍, 나머지는 외곽(섬 포함), 떨어진 외곽 여러 개도 한 번에 돌출·포켓. DXF 프로파일(스케치 아님)은 닫힌 윤곽 하나만.
  - 억제한 가공·Boolean·Pocket은 기준 몸체(`target_feature_id`)를 그대로 넘긴다(억제 여부는 BREP 캐시 키에 포함). 롤백 위치 삽입(`insert_after`)에서 새 피처가 뒤 피처가 쓰던 몸체를 받으면 뒤 피처를 새 피처로 다시 연결해 재생성한다. seq는 두 단계 UPDATE(음수 → 양수)로 민다(유일 제약).
  - 구멍 마법사 표 `core/geometry/holes_std.py`: KS B 0201/ISO 262 탭 드릴 = D − P, KS B 1007/ISO 273 3등급. 카운터보어·접시는 업계 참고값(사내 기준 오면 표만 교체).
  - 검증 도구 `uv run python -m tests.freecad_examples <.fcstd...>`: FreeCAD 없이 FCStd를 읽어 Pad/Pocket/Revolution을 AX-CAD 커널로 재구성해 부피 비교. FreeCAD-org Examples는 라이선스 표기가 없어 저장소 밖에 두고 숫자만 기록.
  - 함정(P2): OCP에 `OCP.GCPnt` 없음 → 길이는 `BRepGProp.LinearProperties_s`. 저장소에 prettier 설정 없음 → 기존 파일에 prettier를 돌리지 않는다.
  - P3 도면화(2026-10-26 완료): `POST /api/documents/{id}/drawing`이 보이는 3D 몸체를 OCCT HLR로 투상해 같은 문서의 DXF 리비전을 만든다. 생성 객체는 `AXGEN_*` 레이어에만, 설정은 DXF 헤더 `$CUSTOMPROPERTY` `AXCAD_DRAWING`. 다시 생성은 `AXGEN_*`만 지우고 다시 그려 사용자 객체를 남긴다. 직접 그린 도면이 최신이면 덮어쓰지 않는다(409 `DRAWING_NOT_GENERATED`).
  - 다시 생성은 DXF를 API 프로세스에서 다시 읽지 않고 업로드 워커가 저장한 렌더 데이터의 `drawing`을 `DrawingRequest`로 검증해 쓴다(파일에서 온 값 = 신뢰 경계 밖, BUG-50). 요청에서 보낸 필드만 바꾸고 null은 기본값(`model_fields_set`).
  - 뷰 좌표: x = p·xdir, y = p·(n×x). FRONT n(0,−1,0) x(1,0,0) · TOP n(0,0,1) x(1,0,0) · RIGHT n(1,0,0) x(0,1,0). 상세도 중심은 이 뷰 좌표. 기하는 모형 공간에 실척, 용지 한계 = 용지/축척, `$LTSCALE` = 2.5/축척.
  - C-1: 지시선 + 문자는 MULTILEADER 한 객체(`{type: MULTILEADER, vertices, value, height}`), 문자는 마지막 다리 쪽. 다른 CAD의 도그레그는 마지막 다리로 바뀐다. C-2: PDF `sheet`로 paper space 시트를 그 용지 1:1, 뷰포트로 모형 공간, 시트 표제란 그대로.
  - 함정(ezdxf MULTILEADER): 빌더 `build(insert=Vec2)`만, 빌더가 도그레그·문자 위치를 스타일로 정함 → 편집은 빌드 후 `context`를 직접 쓰고 `has_dogleg` 재정의 플래그. `dxf.char_height` 없음. R12 불가.
  - 함정(P3): 생성 치수 블록 삭제도 BUG-48 규칙(`*D` + 다른 치수 미사용)만. 렌더 데이터에 필드를 더하면 `RENDER_FORMAT`을 올린다(P3에서 7). paper space `limmin`은 −여백일 수 있다(PDF는 `paper.min`을 원점으로).
  - P4 견적 연계(2026-10-26 완료): 3D 견적은 몸체마다 계산(`cost.compute_quote`), 재료 기준이 소재 사각(BBOX)이면 3D는 소재 = 외곽 + 면당 여유(`stock_allowance_mm`, 기본 2), 360° 회전·원통 몸체는 환봉. 제거 체적 → 밀링/선반, 구멍 → 드릴·탭·카운터보어·접시. 공수 기본값은 `core/quote_engine/machining.py`(업계 참고값, 사내 공수표로 교체). 구멍 라인 원천 = HOLE Feature. 3D로 만든 도면 리비전 견적 = 원본 3D 계측 + 도면 구멍 지시 핸들. 규격품 = Feature `STANDARD`(형상 없음) → 구매 단가(`PURCHASE`) 라인·BOM.
  - 표준품셈(P4-6): 공종은 자기 단가 또는 `costbook_item_code` 중 하나(병행). 품셈 공종 라인은 자원마다 `공종/자원` 코드, 장비 = 경비. 품셈 실제 수치는 사용자가 G1 `12~14` 시트에 원문 입력(PDF 자동 파싱 안 함). 도면 객체(`qto_objects`) → 수량식 `obj_코드`(접두어 new_/demo_/keep_). 외부 수량(Revit/IFC JSON)은 공사 견적 `quantities`, 원천 종류 `EXTERNAL`, 내용 SHA-256 기록.
  - 기준 견적서: `tests/test_p4_parts.py`(합성 5종, 설계값 손계산과 원 단위 일치), `tests/test_costbook.py::test_floor_plan_reference_quote`(공급가액 1,960,870원).
  - 함정: 기준정보 새 하위 표를 더하면 `backend/cli.py` `APP_DELETABLE`에도 넣는다(배포 CI의 `axcad_app` 역할이 초안 저장 시 DELETE, 회귀 테스트 `test_app_role_may_delete_every_draft_table`). 아직 병합 안 된 마이그레이션을 고치면 테스트 DB를 이전 리비전으로 되돌려야 한다(conftest 다운그레이드가 없는 표를 지우다 실패).
  - 함정: 품셈 trace 입력에는 품셈 항목의 `source_sha256`이 들어가므로 다른 해시는 다른 이름(`quantities_sha256`). JSON 본문 상한 8MB(`MAX_JSON_BODY`, content-type 기준), 업로드는 50MB 그대로.

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
