# AX-CAD

중견 제조기업을 위한 **사내 CAD 및 한국 표준 자동 견적 시스템**. 2D 제도, 3D 모델링, 도면에서 한국 표준에 맞는 견적서·BOM까지 한 곳에서 만든다. 개별 PC에 설치해 쓰고, 기능 개선은 서명된 패치로 올린다.

| | |
|---|---|
| 사용자 | 설계·기술 엔지니어, 생산·품질 관리자, 영업·원가 견적 담당자 |
| 현재 단계 | **R1.0 릴리스 리허설 중**(Track 8 P5). 기능 개발은 완료, 사용자 PC 검증 대기 |
| 운영 방식 | 서버 없이 **PC마다 Windows 설치**, 로그인·결재 없음(localhost 전용), 데이터는 그 PC에만 저장 |

## 진행 현황 (2026-11-06)

| 구분 | 상태 |
|---|---|
| S1~S12 (DXF·3D·STEP/IGES·기준정보·견적·견적서·BOM) | 완료 |
| Track 2~7 (품질·배포·보안, 외부 기준 보완, FreeCAD 기능 이식, 공사 견적, 운영 전환) | 완료 |
| Track 8 P1 2D 완성 · P2 3D 모델링 · P3 도면화 · P4 견적 연계 | 완료 |
| Track 8 P5 패치·앱 안 업데이트·릴리스 | 기능 완료, **리허설(TC-158) 진행 중** |
| Track 9 R2.0 (AI Copilot·조립·판금·파라미터 블록·스크립트·SDK) | 완료 (릴리스 노트 `installer/release-notes/2.0.0.txt`) |
| 테스트 | pytest 644(수집) · vitest 204 · Playwright E2E 1 · CI 4개 잡 · 테스트 보고서 **Pass 136/143 (94.9%)** |
| 결함 | BUG-01~58 Fixed, 미해결 0 (BUG-54는 CI windows 잡으로 검증, BUG-53 패치 적용과 BUG-54 화면 업데이트는 사용자 PC 재검증 대기) |

남은 일과 알려진 문제는 [`docs/HANDOFF.md`](docs/HANDOFF.md)와 [`docs/7`](docs/7.AX-CAD_테스트계획_및_결과보고서.md)에 있다.

## 주요 기능

- **캐드 메뉴**: 홈·"캐드 메뉴"에서 프로젝트 없이 바로 새 도면을 그리거나 DXF·DWG를 열어 본다(공용 "빠른 도면" 프로젝트에 보관)
- **2D 제도**: DXF/DWG(변환기 선택) 읽기, 선·원·호·폴리라인·해치·치수·블록·지시선, 레이어·선종류, 스냅(끝점·중점·중심·교점·사분점·수직·근접), 편집 도구(이동·복사·회전·축척·대칭·배열·간격띄우기·필렛·자르기/연장), 측정, PDF 출력
- **3D 모델링**: 구속 스케치, 돌출·회전·포켓·스윕·로프트·구배·두께, 불리언, 필렛·챔퍼·구멍(KS/ISO 마법사), 모서리·면 직접 선택, 억제·순서·롤백, 3D 측정, STEP/IGES/STL/BREP 교환
- **제작도면**: 3D에서 투상도(제3각/제1각)·단면도·상세도·치수·구멍 지시·주석·표제란을 DXF로 생성, 다시 생성, 시트 그대로 PDF
- **견적**: 도면 형상(절단 길이·면적·부피)과 3D 가공 계측(소재·제거 체적·구멍/탭)으로 재료비·가공비·경비 산출, 부품별·규격품 견적, 수동 조정과 자동 산출값 분리, 견적서 PDF/Excel
- **공사 견적**: 철거/신설/유지 수량산출, 공종 단가·표준품셈 일위대가(고시·페이지·SHA-256 출처), 지역·시점 보정, 시나리오 비교, 공사원가계산서·내역서, Revit/IFC 수량 JSON 가져오기
- **추적성**: 모든 견적 라인이 원천 도면 객체(또는 3D Feature) → 공정/규칙 → 단가 → 견적 라인으로 이어진다
- **BOM**: 도면 블록·STEP 부품 집계, 품번 매핑, CSV/JSON, ERP 업로드 파일(Excel)
- **AI Copilot**: 모든 화면 우측 패널(헤더 버튼·`Ctrl+.`로 보이기/감추기) — 설계 점검(DFM)·견적 조언·자연어 명령("100x50 사각형", "두께 2 판금")·도움말, 규칙 기반이 기본이고 LLM은 관리자가 켤 때만 규칙 → Gemini(모델 3단계 대체) → 이 PC의 Ollama 순서(요약 문맥만 전송, 한 번에 1건)
- **AX-LISP·Text to Draw**: Copilot Drawing 탭에서 AutoLISP 문법(안전한 부분집합, 대화형 get*·entsel·ssget)으로 2D·3D를 그리고, 문장('100x50 사각형과 가운데 지름 20 원', '100x50x20 상자 그리고 위에 원통')을 LISP로 바꿔 실행, 자주 쓰는 `c:` 명령은 PC 라이브러리에 저장해 명령행에서 이름으로 실행
- **조립**: 조립 문서에 부품 배치, 메이트 5종(일치·거리·평행·각도·동심) 솔버, 간섭 검사, 조립 BOM·STEP
- **판금**: 베이스·모서리 플랜지, K-factor 해석 전개, 전개도 DXF, 레이저 절단·절곡 견적
- **자동화**: 2D 파라미터 블록(내장 6종 + 회사 템플릿), 명령 스크립트(.axs), 앱 밖 Python SDK(`sdk/axcad.py`)
- **패치 업데이트**: Ed25519 서명 패치, 앱 화면 "업데이트"(검사 → 백업 → 교체 → 재시작, 실패 시 자동 원복)

## 구조

```text
클라이언트(웹 UI) ↔ CAD 엔진(기하·DXF·견적) ↔ PostgreSQL
core/        geometry(OCCT) · dxf(ezdxf) · quote_engine · bom · copilot
backend/     FastAPI api(routes_*) · db(Alembic 마이그레이션 0001~0026)
frontend/    Next.js 15 · React 19 · TypeScript · Tailwind · shadcn/ui
sdk/         Python SDK(표준 라이브러리)·예제
installer/   Windows 설치본·서명 패치(patcher.py)·릴리스 노트
deploy/      docker compose(개발·CI용 배포 패키지)
tests/       pytest(도면 샘플 기반 기하·견적 검증)
docs/        7단계 개발 문서 + 설치·UAT·인계 가이드
```

스택: Python 3.11+, ezdxf, OpenCASCADE(`cadquery-ocp`), FastAPI, SQLAlchemy 2.0, PostgreSQL 16+, Next.js 15. 렌더링과 기하 연산은 분리하고, 모든 금액은 `Decimal`로 계산한다.

## 개발 환경

```bash
uv sync && pnpm --dir frontend install
uv run alembic upgrade head                        # DATABASE_URL 필요(.env, 커밋 금지)
uv run uvicorn backend.api.main:app --port 8000
pnpm --dir frontend dev                            # http://localhost:3000

# 품질 게이트 (CI와 동일)
uv run ruff check . && uv run ruff format --check .
uv run mypy core backend
uv run pytest -v tests/                            # DB 테스트는 TEST_DATABASE_URL 필요
pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test && pnpm --dir frontend build
```

환경변수·테스트 DB·작업 규칙·알려진 함정은 [`docs/HANDOFF.md`](docs/HANDOFF.md)를 따른다. dev 서버가 떠 있으면 `pnpm build`를 하지 않는다.

## 설치와 릴리스

- **사용자**: 릴리스의 `AX-CAD-<버전>-win-x64.zip`을 풀고 `install.cmd` 실행(관리자 권한 불필요). 백업·복구·업데이트는 시작 메뉴 또는 앱 화면에서 한다. 자세한 절차는 [`docs/PC_설치_가이드.md`](docs/PC_설치_가이드.md).
- **개발자**: GitHub Actions **Windows release**(수동 실행, `main` 브랜치, `release` 환경 승인)가 설치본과 서명 패치를 만든다. 변경 내역은 `installer/release-notes/<버전>.txt`.
- 작은 패치는 설치본과 Python 런타임이 같아야 한다(빌드 작업 폴더를 `dist\stage`로 고정한 이유, BUG-53).
- 서명 개인키는 저장소에 두지 않는다(GitHub `release` 환경 시크릿 `AXCAD_PATCH_KEY`). 공개키는 `installer/patch_pub.hex`.

## 문서

| 문서 | 내용 |
|---|---|
| [`plan.md`](plan.md) | 일정·방법론·게이트·스프린트, Track별 결과 |
| [`docs/README.md`](docs/README.md) | 7단계 문서 지도와 관리 규칙 |
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | 세션 인계: 현재 상태, 환경 기동, 남은 작업 |
| [`docs/PC_설치_가이드.md`](docs/PC_설치_가이드.md) | 설치·백업·복구·패치·릴리스·리허설 체크리스트 |
| [`docs/UAT_가이드.md`](docs/UAT_가이드.md) · [`docs/사내_검증_가이드.md`](docs/사내_검증_가이드.md) | UAT·사내 검증 절차 |
| [`CLAUDE.md`](CLAUDE.md) · [`DESIGN.md`](DESIGN.md) | 프로젝트 규칙·디자인 토큰 |
| [`manual.html`](manual.html) | 사용자 매뉴얼 |

## 보안과 기밀

- 모든 서비스는 `127.0.0.1`에만 열리고, 설치별 무작위 토큰이 없는 API 요청과 localhost가 아닌 Host·Origin은 거부한다.
- 업로드(DXF·STEP·DWG)는 형식·크기·시간 제한을 두고 격리 워커에서 처리한다. 패치는 서명·파일 해시·경로를 검증한다.
- 실도면, 기준정보 작성본(G1), 실제 공급자 정보, 비밀번호·토큰·서명 개인키는 저장소에 두지 않는다.

## 라이선스와 고지

저장소에 `LICENSE` 파일은 없다. 외부 오픈소스 의존성 고지는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). FreeCAD(LGPL) 관련 기능은 알고리즘만 참고해 새로 구현했고 코드를 복사하지 않았다.
