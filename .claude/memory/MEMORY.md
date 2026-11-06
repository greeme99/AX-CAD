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
- P5 패치 확장(2026-10-26, 기능 완료·사용자 PC 리허설 대기): 화면 R1.1 = 내부 1.1.0, 수정 1.1.1 = "R1.1 (수정 1)". `app/RELEASE.json`(등급·변경 내역·릴리스일·업그레이드 from_version)은 build.ps1이 `installer/release-notes/<v>.txt`로 만들고 서명 manifest에 실린다. 업데이트 = 같은 메이저 누적·런타임 변경 불가, 업그레이드 = 다음 메이저·from_version에서만, 설치된 DB 마이그레이션이 모두 패치에 있어야 함(`patcher.check_grade`).
  - 앱 안 업데이트: `POST /api/system/updates`가 설치본의 `scripts/patcher.py`(importlib)로 검사 → `update.ps1 -Result`를 WMI(Win32_Process Create, `-EncodedCommand`)로 API 프로세스 트리 밖에서 실행(Stop-App이 taskkill /T로 API 트리를 죽이므로). 런처가 `AXCAD_APP_DIR`·`AXCAD_RUN_DIR`를 준다.
  - 함정: patcher.py는 표준 라이브러리만(업데이트 중 site-packages가 바뀜). 메시지는 한국어(화면·update.ps1에 그대로 보임). 진행 중 판단은 `update-result.json`의 RUNNING(2시간 넘으면 UNKNOWN).
- 설치본과 작은 패치의 짝(BUG-53, PR #31): 패치는 설치본의 Python·PostgreSQL·Node 컴포넌트 id(파일 해시 묶음)와 같을 때만 적용된다(`patcher._admissible`, UPDATE 등급은 런타임이 바뀌는 패치를 거부). `build.ps1`의 작업 폴더가 `dist\AX-CAD-<버전>\`이면 pip가 `Scripts\*.exe`·`*.dist-info/RECORD`에 `python.exe` 절대 경로를 새겨 릴리스마다 python id가 달라진다 → 작업 폴더는 `dist\stage`로 **고정**한다. 이 이전에 만든 설치본은 패치를 받을 수 없어 재설치. Python 버전·`uv.lock`을 바꾸는 릴리스는 `UPGRADE`.
  - 런타임 프로브(BUG-55, PR #44): CI 빌드마다 pip 메타데이터 때문에 python id가 달라져 작은 패치는 거부되는 게 정상이다 → **full patch**를 쓴다. UPDATE의 런타임 검사(`check_grade`)는 컴포넌트 id가 아니라 `RUNTIME_PROBES`(python.exe·bin/postgres.exe·node.exe) 해시를 비교하므로, 핵심 바이너리가 같으면 full patch가 통과한다. CI `windows` 잡은 서로 다른 빌드 간 비교를 하지 않는다(후속).
  - 함정: PC는 **설치본의** `scripts/patcher.py`로 검사한다(`update.ps1`, `routes_system.py`). patcher 규칙을 고친 릴리스는 구버전 PC에서 옛 규칙으로 거부될 수 있다 → 그 1회는 새 번들의 `app\scripts\patcher.py`를 설치본에 덮어쓴 뒤 full patch 적용(2026-11-06 사용자 PC 1.0.0 → 1.0.2 성공).
  - 진단: 설치본 `app\components.json`의 `components.python.id`와 패치 `manifest.json`의 `requires.python` 비교, 다르면 두 파일 목록의 해시 차이를 비교(이번엔 24개 = exe 14 + RECORD 10).
- 릴리스 실행(PR #45, 2026-11-06): Windows release 입력은 version + from_version뿐. **from_version 비움 = UPDATE**, 채움 = UPGRADE(직전 major 마지막 버전). build 잡 첫 단계 `Check inputs`가 형식·major 규칙·`installer/release-notes/<v>.txt` 존재를 검사한다(노트 없으면 실패 → 1.0.2/1.2.0 오타 방지). 예전 grade 선택 입력은 같은 major 릴리스를 UPGRADE로 골라 sign 단계에서 반복 실패해 없앴다. 워크플로 dispatch는 세션 GitHub 권한(403)으로 불가 → 사용자가 Actions에서 실행. Rerun은 원래 커밋으로 돌므로 머지 후엔 새로 실행.
- Next.js 15.5 `/api` 재작성 프록시는 본문 기본 10MB 제한(BUG-54, Fixed·재검증 대기). 한도 옵션 `middlewareClientMaxBodySize`는 **`experimental` 아래**에 둔다(최상위는 조용히 무시, 현재 1gb). CI의 패치 업로드는 화면 경로(`:13000`)로 보낸다 — 우회로(`:18000`)를 쓰면 이 경로가 검증되지 않는다. 로컬 확인은 `NEXT_OUTPUT=standalone` 빌드 + 본문 바이트를 세는 스텁 API. 시험 서버(`next-server`)는 PID로 종료.
- 운영: 클라우드 세션의 GitHub 연결은 Actions 실행 권한이 없다(403) → 릴리스는 사용자가 Actions 탭에서. `Windows release` 서명 잡은 `main`에서만 돌고 `release` 환경(승인자)을 쓴다.
- **13 R2.0 보완 + AI Copilot**(Track 9, 2026-11-06 사용자 결정·grill-me, PR #47~#51 완료): 조립은 ASSEMBLY 문서가 PART 결과 몸체를 인스턴스로 참조(부품 200개 이내), 판금은 베이스·모서리 플랜지 + K-factor 해석 전개 + 레이저/절곡 견적, 자동화는 수식 파라미터 블록(일반 블록 + BLOCK 설명 JSON) + 명령 스크립트(.axs) + 앱 밖 Python SDK(앱 안 임의 코드 실행 금지). Copilot은 모든 화면 우측 패널(헤더 버튼·`Ctrl+.`)·규칙 기반(DFM·견적 조언·자연어 명령·도움말), LLM은 Gemini 2.5 Flash 접점만 두고 기본 꺼짐·관리자 키 설정 시에만·요약 문맥(화면·문서 유형·피처 개수·코드)만 전송. 근거 `plan.md` §15, 릴리스 노트 `installer/release-notes/2.0.0.txt`.
  - 함정(Q1): 2D 명령을 여러 개 이어서 `submit`하면 React 상태가 갱신되기 전 값을 참조한다 → 입력열은 `lib/cad/script.ts runInputs`로 순수하게 이어 돌리고 결과를 한 번에 적용한다(실행 취소 1단위, `.axs`도 같은 경로).
  - 함정(Q1): DRAWING 문서에도 3D 피처가 생긴다(DXF → 3D). 문서 단위 점검은 doc_type으로 갈라 한쪽만 보지 말고 도면·모델 둘 다 본다. LLM 호출은 표준 `urllib`(리디렉션 차단) — `httpx`는 개발 전용이라 설치본에 없다.
  - 함정(Q2, 조립 솔버): 면 마주봄 잔차를 `na+nb`로 두면 해 근처에서 2차라 가우스-뉴턴이 거대한 스텝을 요구한다 → 올바른 쪽에서는 `na×nb`. 최소 노름 스텝은 mm와 rad를 같은 무게로 봐 부품을 기울인다 → 회전 변수를 부품 크기로 스케일. 같은 방향 평면끼리 '일치'는 안장점 → 풀기 전에 자유 부품을 뒤집는다. 체인 20개 이상으로 시험해야 드러난다. 작업자 풀(`run_kernel`)은 별도 프로세스라 테스트의 monkeypatch 값은 인자로 넘겨야 반영된다.
  - 결정(Q3, 판금): 전개는 임의 B-Rep 펼치기가 아니라 판금 피처 파라미터(외곽 + 변별 플랜지)에서 해석식(BA = θ·(R+K·T)) → 정확·빠름, 대신 1단 플랜지·직선 외곽만. 플랜지 길이 L은 굽힘 뒤 직선부. 판금 몸체 견적 소재는 판재(전개 BBox + 여유 × T), 기본 규칙 `S-LASER`·`S-BEND`.
  - 결정(Q4): 파라미터 블록의 템플릿·값은 XDATA가 아니라 BLOCK 설명(description, DXF 그룹 4)에 JSON 한 줄(기존 `apply_edits` 블록 정의 경로 재사용). 파일에 그대로 쓰이므로 서버는 파라미터 블록 JSON만·제어문자 없이 받는다(BUG-58: 개행이면 DXF 그룹 주입). SDK는 표준 라이브러리, 리디렉션 미추종, 설치 토큰은 루프백만.
  - 함정(Q4): API가 내장 템플릿을 검증 전 원본으로 내보내 선택 키(`states`)가 빠졌고 화면이 깨졌다 → 내보내는 값은 항상 검증·정규화된 것. 브라우저 확인에서만 드러났다.
  - 진행 방식: 단계별 PR → CI 녹색 → squash 머지. 뒤 단계 커밋은 로컬에 단계별로 나눠 두고, 머지 후 main 위에 cherry-pick + 원격 브랜치는 `git merge -X ours origin/<branch>`(내용 변화 0 확인)로 강제 푸시 없이 올린다(강제 푸시는 훅이 차단). 앞 단계가 바꾼 기준 데이터(예: G1 공정 예시 행)는 다른 테스트의 하드코딩 값을 깨므로 단계마다 전체 pytest.

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
