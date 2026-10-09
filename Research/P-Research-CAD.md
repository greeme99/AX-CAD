<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# 아래 내용을 참조하여 CAD 시스템 구축 방법에 대해 집중 검색을 통해 근거가 명확하고, 신뢰할수 있는 방법론과 오픈소스를 보완해서 제안해줘.

## 중견제조기업의 사내 In-house CAD 시스템을 구축하기 위해 AI 기반의 바이브 코딩(Vibe Coding) 방식을 활용하는 것은 매우 혁신적이면서도 실현 가능한 전략입니다. 최근 들어 대형 언어 모델(LLM)과 AI 페어 프로그래밍 도구의 발전으로 실제 복잡한 CAD 소프트웨어까지 바이브 코딩으로 개발해 낸 성공 사례가 깃허브 오픈소스 진영에서 속속 등장하고 있습니다. [1] 성공적인 개발을 위해 상용 CAD(AutoCAD 등)의 필수 기능 분석부터 단계별 개발 절차, 그리고 바이브 코딩 시 AI에 입력할 구체적인 프롬프트와 신뢰할 수 있는 오픈소스 출처를 정리한 가이드를 제공합니다.

## 1. 상용 CAD 핵심 기능 분석 및 In-house 시스템 목표 정의

중견제조기업에서 실제 도면 설계 및 생산 공정에 대응하기 위해 상용 CAD(AutoCAD)에서 반드시 대체해야 하는 기능은 다음과 같이 요약할 수 있습니다. [2, 3]

- 
- 2D 제도 (Drafting): 선(Line), 원(Circle), 호(Arc), 다중선(Polyline), 텍스트, 치수 기입(Dimension), 스냅(Snap) 엔진, 레이어 관리. [4]
- 3D 모델링 (Modeling): 돌출(Extrude), 회전(Revolve), 로프트(Loft), 쉘(Shell), 불리언(Boolean) 연산 (합집합/차집합/교집합). [1, 5]
- 데이터 호환성: 현업 제조 파트너들과 도면을 주고받기 위한 DXF, DWG 포맷 읽기/쓰기 및 3D 표준 포맷인 STEP, IGES 지원. [5, 6]
- 사내 연동 (In-house 핵심): 설계 도면 데이터와 사내 ERP/MES/PLM 시스템을 직접 연동하여 자재명세서(BOM)를 자동으로 산출하는 기능. [7]
- 

______________________________________________________________________

## 2. 바이브 코딩(Vibe Coding)을 위한 개발 아키텍처 및 기술 스택 수립

AI와 함께 밑바닥부터 수학적 기하학 엔진을 만드는 것은 불가능에 가깝습니다. 전 세계에서 검증된 강력한 오픈소스 CAD 커널(Kernel)을 뼈대로 삼고, UI와 사내 특화 기능을 AI(바이브 코딩)로 붙여나가는 방식이 가장 실현 가능성이 높습니다. [1]

## 🛠️ 추천 기술 스택 조합

- 
- 기하학 커널 (Geometry Kernel): OpenCASCADE (OCCT). 전 세계 오픈소스 3D CAD의 표준 커널이며, 정밀한 B-Rep(부피 표현) 연산과 STEP/IGES 입출력을 완벽히 지원합니다. [1, 5, 6]
- 개발 언어 및 UI 프레임워크:
- C++ \& Qt (또는 Dear ImGui): 고성능 데스크톱 앱을 원할 때 사용합니다 (예: FreeCAD, Materializr 방식).
    - Python (PyQt5 + PythonOCC / CadQuery): 바이브 코딩 속도가 가장 빠르고 AI가 코드를 가장 잘 작성하는 조합입니다. 중견기업 사내용 시스템 개발에 강력 추천합니다. [1, 5, 6, 8]
- 도면 입출력 라이브러리: libdxfrw (C++ 기반 DXF/DWG 처리) 또는 ezdxf (Python 기반 DXF 처리). [9, 10]
- 

______________________________________________________________________

## 3. 단계별(Step-by-Step) 순차 개발 로드맵\#\# 1단계: MVP 개발 — 2D 뷰어 및 DXF 입출력 파이프라인 구축

- 
- 목표: 사내 도면(DXF/DWG)을 불러와 화면에 정확하게 띄우고, 마우스 휠로 확대/축소/이동(Pan)이 가능한 환경을 만듭니다.
- 핵심 기능: 파일 파싱, 2D 그래픽스 파이프라인 설정.
- 


## 2단계: 2D 제도 및 스냅 엔진 구현

- 
- 목표: 사용자가 마우스로 선, 원을 그리고, AutoCAD처럼 끝점(Endpoint), 중간점(Midpoint)을 자석처럼 잡아주는 스냅(Snap) 기능을 구현합니다.
- 핵심 기능: 실시간 마우스 좌표 연산, 드로잉 상태 머신 구현. [4]
- 


## 3단계: 3D 모델링 확장 및 커널 연동

- 
- 목표: 2D 스케치를 기반으로 3D 형상을 만들고 3D 표준 파일(STEP)로 내보내는 기능을 구현합니다.
- 핵심 기능: OpenCASCADE 커널과 UI 연동, Extrude 및 Boolean 함수 호출. [1, 5]
- 


## 4단계: 사내 시스템(ERP/MES/PLM) 연동 및 자동화

- 
- 목표: 도면에 포함된 부품 정보를 인식하여 사내 ERP와 연동하고, BOM(자재명세서)을 원클릭으로 추출합니다.
- 핵심 기능: 도면 텍스트/속성(Attribute) 데이터베이스 파싱, REST API 연동. [7]
- 

______________________________________________________________________

## 4. 바이브 코딩 성공을 위한 프롬프트 엔지니어링 가이드

바이브 코딩 시 대형 모델에게 단순히 "CAD 프로그램 만들어줘"라고 하면 실패합니다. AI는 시각적 결과를 직접 보지 못하므로 가이드라인을 매우 구체적으로 쪼개서 제공해야 합니다. 다음은 개발 단계별 검증된 프롬프트 예시입니다. [11]

## 💡 1단계(뷰어) 작성을 위한 AI 프롬프트 예시

"Python과 PyQt5, 그리고 ezdxf 라이브러리를 사용하여 DXF 파일을 읽어와 화면에 렌더링하는 2D CAD 뷰어 클래스를 작성해줘. 마우스 휠을 돌리면 마우스 포인터 위치를 중심으로 확대/축소(Zoom)가 되어야 하고, 마우스 휠을 클릭하고 드래그하면 화면이 이동(Panning)되어야 해. 코드는 모듈화하여 유지보수가 가능하도록 설계해줘."

## 💡 2단계(스냅 엔진) 작성을 위한 AI 프롬프트 예시

"PyQt5 QGraphicsScene 환경에서 실시간 2D 객체 스냅(Snap) 엔진을 구현하고 싶어. 마우스 커서의 현재 위치에서 일정 픽셀 반경 내에 존재하는 선(Line)의 끝점(Endpoint)과 중간점(Midpoint)을 찾아서, 마우스 좌표를 해당 점으로 강제 고정(Snapping)하고 화면에 작은 녹색 사각형 마커를 표시하는 Python 메서드를 작성해줘."

______________________________________________________________________

## 5. 신뢰할 수 있는 오픈소스 및 커뮤니티 출처 (Deep Researched)

개발 시 뼈대로 삼거나 코드를 참조할 수 있는 완벽히 검증된 공식 출처 리스트입니다.

## 🐙 깃허브(GitHub) 핵심 레포지토리

- 
- [materializr](https://github.com/materializr-cad/materializr): 실제 LLM과 바이브 코딩(Vibe coded)으로 개발된 최신 오픈소스 3D CAD 프로그램입니다. OpenCASCADE 커널과 Dear ImGui UI를 사용하여 밑바닥부터 빌드된 모범 사례이므로 반드시 소스코드를 분석해야 합니다.
- [FreeCAD 공식 저장소](https://github.com/FreeCAD/FreeCAD): 오픈소스 CAD의 끝판왕입니다. C++과 Python 기반으로 OpenCASCADE를 어떻게 상용 수준으로 활용했는지 아키텍처를 참고하기 좋습니다.
- [CadQuery](https://github.com/cadquery/cadquery): Python 코드로 3D 기하학을 제어하는 프레임워크로, GUI 없이 기하학 연산 백엔드를 구축할 때 유용합니다.
- [libdxfrw](https://github.com/LibreCAD/libdxfrw): LibreCAD 진영에서 관리하는 DWG/DXF 읽기/쓰기 핵심 라이브러리입니다.
- [webcad](https://github.com/elhakimz/webcad): 웹 브라우저 기반으로 OpenCASCADE Wasm과 스냅 엔진을 구현한 프로젝트로, 경량화된 스냅 로직을 참고하기 좋습니다. [1, 4, 6, 10, 12, 13, 14]
- 


## 🤗 허깅페이스(Hugging Face) 및 AI 모델 리포지토리

- 
- [Hugging Face CAD Models Search](https://huggingface.co/models?search=CAD): Text-to-CAD나 DeepCAD 같이 자연어나 AI 스크립트로 3D 형상을 복원하는 최신 인공지능 모델 및 데이터셋(ABC Dataset 등) 동향을 파악할 수 있습니다. [15]
- 


## 🌐 공식 개발자 및 사용자 커뮤니티 포럼

- 
- [AutoCAD Developer Center](https://www.autodesk.com/developer-network/platform-technologies/autocad): AutoCAD의 공식 ObjectARX 및 DXF 사양서(Specification)를 제공합니다. 도면 호환성을 완벽하게 맞추기 위해 파서(Parser)를 개발할 때 공식 문서 규격을 반드시 대조해야 합니다.
- [Open CASCADE 기술 포럼](https://dev.opencascade.org/): 커널 사용 중 발생하는 수학적 오류나 3D 연산 버그를 해결할 수 있는 가장 신뢰도 높은 개발자 포럼입니다.
- 

______________________________________________________________________

## 6. 바이브 코딩 시 주의점 및 리스크 관리

1. 시각적 피드백의 한계: AI 코딩 도구는 CLI(텍스트) 출력은 잘 분석하지만, 화면에 선이 비뚤어지게 그려지거나 3D 면이 깨지는 현상은 스스로 인지하지 못합니다. 인간 개발자가 화면을 보고 기하학적 수치나 렌더링 상태를 논리적 글로 풀어서 AI에게 다시 피드백을 주어야 합니다.
2. 스파게티 코드 방지: 프롬프트를 계속 추가하다 보면 코드가 뒤엉키게 됩니다. 처음부터 객체지향 설계(UI 디자인 패턴, 데이터 처리 레이어 분리)를 명확히 요구하고, 주기적으로 AI에게 "지금까지 작성된 코드를 클린 코드로 리팩토링해줘"라고 요청해야 합니다. [11]

In-house CAD 개발을 위한 기술 스택을 확정하기 위해 아래 내용을 알려주시면 더 맞춤형 소스코드를 제안해 드릴 수 있습니다.

- 사내 개발팀이 가장 선호하는 주력 프로그래밍 언어 (C++ / Python / TypeScript 등)
- 최종 결과물이 작동해야 하는 환경이 데스크톱 앱 형태인지, 혹은 사내 웹브라우저에서 접속하는 웹 앱 형태인지 여부
- 가장 먼저 연동해야 하는 사내 시스템 (예: ERP 제품명 또는 도면 기반 BOM 자동 산출 필요 여부)

[1] [https://github.com](https://github.com/materializr-cad/materializr)
[2] [https://github.com](https://github.com/topics/cad-design)
[3] [https://blog.naver.com](https://blog.naver.com/doctor_soft/222983358211)
[4] [https://github.com](https://github.com/elhakimz/webcad)
[5] [https://daddynkidsmakers.blogspot.com](http://daddynkidsmakers.blogspot.com/2023/04/opencascade_26.html)
[6] [https://svrforum.com](https://svrforum.com/itnews/3083708)
[7] [https://ingetrazo.com](https://ingetrazo.com/en/)
[8] [https://github.com](https://github.com/qcad/qcad)
[9] [https://github.com](https://github.com/codelibs/libdxfrw)
[10] [https://github.com](https://github.com/LibreCAD/libdxfrw)
[11] [https://brunch.co.kr](https://brunch.co.kr/@michaelsergei/146)
[12] [https://github.com](https://github.com/topics/3d?l=c%2B%2B)
[13] https://www.freecad.org
[14] [https://github.com](https://github.com/cadquery/cadquery)
[15] [https://github.com](https://github.com/mlightcad/awesome-cad)

아래 자료를 기준으로 보면, **중견제조기업의 In-house CAD는 “OCCT(기하 커널) + DXF/DWG 파서 + 2D 편집 UI + 사내 ERP/MES/PLM 연동” 구조로 가는 것이 가장 현실적**입니다. FreeCAD와 OCCT는 CAD 커널과 아키텍처의 검증 사례로 적합하고, 2D DXF 쪽은 ezdxf·libdxfrw·QCAD·LibreCAD가 실전 참고용으로 유용합니다.[^1][^2][^3][^4][^5][^6]

## 권장 구축 원칙

- **커널은 직접 만들지 말고 OCCT를 사용**하는 것이 맞습니다. OCCT는 3D surface/solid modeling, CAD data exchange, visualization을 제공하는 전문 CAD 커널이며 STEP, IGES, STL, VRML 등을 표준적으로 다룹니다.[^7][^8]
- **2D는 DXF 중심으로 시작**하는 것이 좋습니다. Autodesk는 DXF를 AutoCAD 도면 정보의 tagged data 표현으로 설명하고 있으며, ezdxf는 DXF 읽기/수정/생성용 Python 인터페이스입니다.[^3][^9]
- **DWG는 초기에 완전 대체보다 “읽기 중심/변환 중심”으로 접근**하는 편이 현실적입니다. libdxfrw는 DXF 읽기/쓰기와 제한적 DWG 읽기를 제공하고, QCAD는 DWG를 상용 플러그인으로 처리하는 구조를 보여줍니다.[^4][^10][^11]


## 추천 기술 스택

| 계층 | 추천 | 근거 |
| :-- | :-- | :-- |
| 기하 커널 | OCCT | FreeCAD의 핵심 커널이며 STEP/IGES/3D 연산에 강함 [^1][^2][^7] |
| 2D DXF 처리 | ezdxf, libdxfrw | Python 빠른 개발과 C++ 기반 안정성 모두 확보 가능 [^3][^4] |
| 데스크톱 UI | Qt | QCAD와 FreeCAD 계열이 검증한 선택지 [^12][^6] |
| 빠른 프로토타입 | Python + PyQt | AI 보조 개발과 MVP 속도가 빠름; ezdxf와 결합이 쉬움 [^3][^12] |
| 고성능/장기 확장 | C++ + Qt + OCCT | 커널 성능과 상용화 안정성을 얻기 좋음 [^2][^7] |
| 브라우저형 확장 | Web front-end + 서버 CAD 서비스 | 초기엔 뷰어/주석 중심, 핵심 연산은 서버에서 처리하는 구조가 안전함 [^7][^13] |

## 단계별 구축 방법

1. **1단계: DXF 뷰어/MVP**
    - DXF 파일 로딩, 레이어 표시, 줌/팬, 기본 선택 기능부터 만듭니다.
    - 이 단계에서는 ezdxf로 파싱하고 Qt로 렌더링하는 구성이 빠릅니다.[^12][^3]
2. **2단계: 2D 편집과 스냅**
    - 선, 원, 호, 폴리라인, 텍스트, 치수, 스냅(Endpoint, Midpoint 등)을 추가합니다.
    - QCAD와 LibreCAD가 보여주듯 2D CAD의 본질은 정밀한 편집/스냅/UI 반응성입니다.[^14][^6]
3. **3단계: 3D 모델링**
    - 스케치 기반으로 Extrude, Revolve, Boolean, Shell을 연결합니다.
    - 이 구간부터는 OCCT를 직접 붙여야 하며 FreeCAD의 구조를 참고하는 것이 좋습니다.[^2][^1][^7]
4. **4단계: BOM/PLM 연동**
    - 도면 속성, 부품명, 규격, 수량을 추출해 ERP/MES/PLM API로 넘깁니다.
    - CAD 자체보다 “사내 데이터와 연결되는 구조”가 제조기업에서는 더 큰 가치가 있습니다.

## 바이브 코딩에 적합한 방식

바이브 코딩은 “CAD 전체를 한 번에 만들어 달라”가 아니라, **작은 단위로 명세를 쪼개서 AI에게 구현시키는 방식**이 적합합니다. 특히 CAD는 화면 피드백이 중요하므로, AI에게는 기능보다도 “입력, 상태, 렌더링, 검증 조건”을 분리해서 지시해야 합니다. Materializr는 OpenCASCADE와 Dear ImGui 기반으로, constraint sketch와 solid modeling, STEP/STL/DXF/OBJ/3MF exchange를 제공하는 오픈소스 예시로 참고 가치가 높습니다.[^13][^15]

### 프롬프트 예시 1: DXF 뷰어

“Python, PyQt, ezdxf를 사용해 DXF 파일을 읽고 QGraphicsView에 렌더링하는 2D CAD 뷰어를 작성해줘. 줌은 마우스 포인터 위치를 중심으로 동작해야 하고, 팬은 마우스 드래그로 동작해야 한다. 코드 구조는 파일 로더, 렌더러, 입력 핸들러를 분리해줘.”

### 프롬프트 예시 2: 스냅 엔진

“QGraphicsScene에서 Endpoint와 Midpoint 스냅을 구현해줘. 커서 반경 내 객체를 탐색하고, 가장 가까운 스냅 포인트로 좌표를 보정하며, 스냅 대상은 작은 마커로 시각화해줘. 성능을 위해 모든 엔티티를 매번 전수 탐색하지 말고 공간 검색 구조를 사용해줘.”

### 프롬프트 예시 3: 3D 확장

“OCCT를 사용해 2D 스케치를 Extrude로 3D 솔리드로 변환하는 모듈을 작성해줘. 입력은 폐곡선 wire, 출력은 STEP 파일이며, 실패 케이스로 self-intersection과 open wire를 검출해 예외 처리해줘.”

## 오픈소스 우선순위

| 목적 | 우선 참고 | 이유 |
| :-- | :-- | :-- |
| 3D 커널 기반 CAD | FreeCAD, OCCT | 제조용 CAD 아키텍처와 파이프라인이 검증됨 [^1][^2][^7] |
| 빠른 3D 프로토타입 | Materializr, CadQuery | AI 보조 개발과 파라메트릭 모델링 참고에 좋음 [^13] |
| 2D DXF 중심 CAD | QCAD, LibreCAD | 2D 제도, DXF 처리, 스냅, 편집 UX가 성숙함 [^14][^5][^6] |
| DXF/DWG 파싱 | libdxfrw, ezdxf | 실무에서 직접 쓰기 쉬운 처리 레이어 [^3][^4] |

## 리스크와 주의점

- **DWG 완전 호환을 초기에 목표로 잡으면 일정이 급격히 늘어납니다.** 먼저 DXF 중심으로 내부 표준을 만들고, DWG는 변환/호환 계층으로 두는 편이 좋습니다.[^11][^16][^4]
- **AI는 기하학적 정확성을 자동 보장하지 못합니다.** 따라서 인간이 시각 검증, 단위 테스트, 도면 샘플 비교를 함께 수행해야 합니다.
- **라이선스를 반드시 검토해야 합니다.** OCCT, FreeCAD, QCAD, libdxfrw, ezdxf는 각기 라이선스 특성이 다르므로, 사내 배포 범위와 플러그인 방식에 맞춰 확인이 필요합니다.[^17][^3][^4][^11]

원하시면 다음 단계로 바로 이어서, **“중견제조기업용 CAD 시스템 아키텍처도(모듈/ERD/API 포함)”** 또는 **“바이브 코딩용 세부 프롬프트 20개”** 형태로 구체화해드릴 수 있습니다.

<span style="display:none">[^18][^19][^20][^21][^22][^23][^24][^25][^26][^27][^28][^29][^30]</span>

<div align="center">⁂</div>

[^1]: https://github.com/freecad/freecad

[^2]: https://freecad.github.io/DevelopersHandbook/gettingstarted/dependencies.html

[^3]: https://ezdxf.readthedocs.io/en/stable/introduction.html

[^4]: https://github.com/LibreCAD/libdxfrw

[^5]: https://www.qcad.org/en/78-qcad

[^6]: https://github.com/qcad/qcad

[^7]: https://dev.opencascade.org/doc/occt-7.9.0/overview/html/index.html

[^8]: https://dev.opencascade.org/doc/occt-7.6.0/overview/html/index.html

[^9]: https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-DXF/files/GUID-235B22E0-A567-4CF6-92D3-38A2306D73F3.htm

[^10]: https://github.com/LibreCAD/libdxfrw/blob/master/README.md

[^11]: https://www.qcad.org/en/license

[^12]: https://github.com/qcad

[^13]: https://github.com/materializr-cad/materializr

[^14]: https://github.com/librecad/librecad

[^15]: https://github.com/camillanapoles/materializr-3d-cad/blob/main/README.md

[^16]: https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/AutoCAD-DXF-file-format-documentation.html

[^17]: https://www.qcad.org/en/documentation/license

[^18]: https://www.opencascade.com/products/cad-processor/

[^19]: https://wiki.freecad.org/OpenCASCADE/cs

[^20]: https://www.opencascade.com/products/cad-assistant/

[^21]: https://github.com/FreeCAD

[^22]: https://freecad.github.io/Website/dev/setup/dependencies/

[^23]: https://github.com/librecad

[^24]: https://github.com/LibreCAD/libdxfrw/blob/master/src/main_doc.h

[^25]: https://github.com/LibreCAD/libdxfrw/actions

[^26]: https://en.wikipedia.org/wiki/LibreCAD

[^27]: https://www.mankier.com/package/libdxfrw

[^28]: https://www.qcad.org/en/download

[^29]: https://www.qcad.org/en/contribute

[^30]: https://help.autodesk.com/cloudhelp/2018/ENU/AutoCAD-DXF/files/index.htm

