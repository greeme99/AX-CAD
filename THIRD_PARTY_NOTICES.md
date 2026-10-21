# Third-party notices (오픈소스 고지)

AX-CAD는 아래 오픈소스를 **수정 없이** 사용한다. 사내 배포(이미지·서버 설치) 시 이 파일을 함께 유지한다.
라이선스 전문은 각 패키지 배포본(Python `*.dist-info/`, `frontend/node_modules/<패키지>/LICENSE`)과 아래 링크에 있다.
의존성을 추가하거나 버전을 크게 바꾸면 이 표를 갱신한다.

## 의무가 있는 라이선스 (LGPL)

| 구성요소 | 라이선스 | 쓰는 곳 | 지키는 방법 |
|---|---|---|---|
| Open CASCADE Technology 8.0 (`cadquery-ocp-novtk`에 동봉, 바인딩 자체는 Apache-2.0) | LGPL-2.1 + Open CASCADE 예외 | `core/geometry` 3D 커널 | 동적 라이브러리를 수정 없이 사용, pip 패키지로 교체 가능한 형태 유지, 본 고지 유지. 커널을 수정·정적 링크하면 소스 공개 의무 검토 |
| psycopg 3 (`psycopg[binary]`) | LGPL-3.0 | `backend` PostgreSQL 드라이버 | 위와 같음 (수정 없이 동적 사용, 교체 가능) |

## 참고만 한 소스 (코드 미포함)

| 프로젝트 | 라이선스 | 참고한 부분 |
|---|---|---|
| FreeCAD ([greeme99/FreeCAD](https://github.com/greeme99/FreeCAD) 포크) | LGPL-2.1+ | Draft 스냅·수정 도구, PartDesign 가공 Feature, Import 색상 처리의 기능 구성. AX-CAD 코드는 다시 작성했고 FreeCAD 소스·바이너리를 포함하지 않는다 (`plan.md` §11) |

## 고지만 필요한 라이선스

| 구성요소 | 라이선스 | 쓰는 곳 |
|---|---|---|
| ezdxf | MIT | DXF 읽기·쓰기 |
| FastAPI, Pydantic, SQLAlchemy, Alembic, PyJWT, argon2-cffi, openpyxl | MIT | API·DB·인증·엑셀 |
| uvicorn, httpx | BSD-3-Clause | API 서버·ERP 연동 |
| python-multipart | Apache-2.0 | 업로드 |
| ReportLab | BSD | 견적서 PDF |
| defusedxml | PSF | 엑셀 XML 방어 |
| Next.js, React, three.js | MIT | 웹 화면·3D 뷰어 (브라우저로 전달되는 번들) |
| 나눔고딕 (`backend/assets/fonts`) | SIL OFL 1.1 (`OFL.txt` 동봉) | 견적서 PDF 한글 |
| PostgreSQL 16, nginx (배포 이미지) | PostgreSQL License, BSD-2-Clause | `deploy/` |

라이선스 원문: [LGPL-2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) · [OCCT 예외](https://dev.opencascade.org/resources/licensing) · [LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html) · [MIT](https://opensource.org/license/mit) · [OFL 1.1](https://openfontlicense.org)
