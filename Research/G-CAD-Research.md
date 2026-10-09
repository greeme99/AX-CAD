중견제조기업의 사내 In-house CAD 시스템을 구축하기 위해 AI 기반의 바이브 코딩(Vibe Coding) 방식을 활용하는 것은 매우 혁신적이면서도 실현 가능한 전략입니다. 최근 들어 대형 언어 모델(LLM)과 AI 페어 프로그래밍 도구의 발전으로 실제 복잡한 CAD 소프트웨어까지 바이브 코딩으로 개발해 낸 성공 사례가 깃허브 오픈소스 진영에서 속속 등장하고 있습니다. [1] 
성공적인 개발을 위해 상용 CAD(AutoCAD 등)의 필수 기능 분석부터 단계별 개발 절차, 그리고 바이브 코딩 시 AI에 입력할 구체적인 프롬프트와 신뢰할 수 있는 오픈소스 출처를 정리한 가이드를 제공합니다.
------------------------------
## 1. 상용 CAD 핵심 기능 분석 및 In-house 시스템 목표 정의
중견제조기업에서 실제 도면 설계 및 생산 공정에 대응하기 위해 상용 CAD(AutoCAD)에서 반드시 대체해야 하는 기능은 다음과 같이 요약할 수 있습니다. [2, 3] 

* 
* 2D 제도 (Drafting): 선(Line), 원(Circle), 호(Arc), 다중선(Polyline), 텍스트, 치수 기입(Dimension), 스냅(Snap) 엔진, 레이어 관리. [4] 
* 3D 모델링 (Modeling): 돌출(Extrude), 회전(Revolve), 로프트(Loft), 쉘(Shell), 불리언(Boolean) 연산 (합집합/차집합/교집합). [1, 5] 
* 데이터 호환성: 현업 제조 파트너들과 도면을 주고받기 위한 DXF, DWG 포맷 읽기/쓰기 및 3D 표준 포맷인 STEP, IGES 지원. [5, 6] 
* 사내 연동 (In-house 핵심): 설계 도면 데이터와 사내 ERP/MES/PLM 시스템을 직접 연동하여 자재명세서(BOM)를 자동으로 산출하는 기능. [7] 
* 

------------------------------
## 2. 바이브 코딩(Vibe Coding)을 위한 개발 아키텍처 및 기술 스택 수립
AI와 함께 밑바닥부터 수학적 기하학 엔진을 만드는 것은 불가능에 가깝습니다. 전 세계에서 검증된 강력한 오픈소스 CAD 커널(Kernel)을 뼈대로 삼고, UI와 사내 특화 기능을 AI(바이브 코딩)로 붙여나가는 방식이 가장 실현 가능성이 높습니다. [1] 
## 🛠️ 추천 기술 스택 조합

* 
* 기하학 커널 (Geometry Kernel): OpenCASCADE (OCCT). 전 세계 오픈소스 3D CAD의 표준 커널이며, 정밀한 B-Rep(부피 표현) 연산과 STEP/IGES 입출력을 완벽히 지원합니다. [1, 5, 6] 
* 개발 언어 및 UI 프레임워크:
* C++ & Qt (또는 Dear ImGui): 고성능 데스크톱 앱을 원할 때 사용합니다 (예: FreeCAD, Materializr 방식).
   * Python (PyQt5 + PythonOCC / CadQuery): 바이브 코딩 속도가 가장 빠르고 AI가 코드를 가장 잘 작성하는 조합입니다. 중견기업 사내용 시스템 개발에 강력 추천합니다. [1, 5, 6, 8] 
* 도면 입출력 라이브러리: libdxfrw (C++ 기반 DXF/DWG 처리) 또는 ezdxf (Python 기반 DXF 처리). [9, 10] 
* 

------------------------------
## 3. 단계별(Step-by-Step) 순차 개발 로드맵## 1단계: MVP 개발 — 2D 뷰어 및 DXF 입출력 파이프라인 구축

* 
* 목표: 사내 도면(DXF/DWG)을 불러와 화면에 정확하게 띄우고, 마우스 휠로 확대/축소/이동(Pan)이 가능한 환경을 만듭니다.
* 핵심 기능: 파일 파싱, 2D 그래픽스 파이프라인 설정.
* 

## 2단계: 2D 제도 및 스냅 엔진 구현

* 
* 목표: 사용자가 마우스로 선, 원을 그리고, AutoCAD처럼 끝점(Endpoint), 중간점(Midpoint)을 자석처럼 잡아주는 스냅(Snap) 기능을 구현합니다.
* 핵심 기능: 실시간 마우스 좌표 연산, 드로잉 상태 머신 구현. [4] 
* 

## 3단계: 3D 모델링 확장 및 커널 연동

* 
* 목표: 2D 스케치를 기반으로 3D 형상을 만들고 3D 표준 파일(STEP)로 내보내는 기능을 구현합니다.
* 핵심 기능: OpenCASCADE 커널과 UI 연동, Extrude 및 Boolean 함수 호출. [1, 5] 
* 

## 4단계: 사내 시스템(ERP/MES/PLM) 연동 및 자동화

* 
* 목표: 도면에 포함된 부품 정보를 인식하여 사내 ERP와 연동하고, BOM(자재명세서)을 원클릭으로 추출합니다.
* 핵심 기능: 도면 텍스트/속성(Attribute) 데이터베이스 파싱, REST API 연동. [7] 
* 

------------------------------
## 4. 바이브 코딩 성공을 위한 프롬프트 엔지니어링 가이드
바이브 코딩 시 대형 모델에게 단순히 "CAD 프로그램 만들어줘"라고 하면 실패합니다. AI는 시각적 결과를 직접 보지 못하므로 가이드라인을 매우 구체적으로 쪼개서 제공해야 합니다. 다음은 개발 단계별 검증된 프롬프트 예시입니다. [11] 
## 💡 1단계(뷰어) 작성을 위한 AI 프롬프트 예시

"Python과 PyQt5, 그리고 ezdxf 라이브러리를 사용하여 DXF 파일을 읽어와 화면에 렌더링하는 2D CAD 뷰어 클래스를 작성해줘. 마우스 휠을 돌리면 마우스 포인터 위치를 중심으로 확대/축소(Zoom)가 되어야 하고, 마우스 휠을 클릭하고 드래그하면 화면이 이동(Panning)되어야 해. 코드는 모듈화하여 유지보수가 가능하도록 설계해줘."

## 💡 2단계(스냅 엔진) 작성을 위한 AI 프롬프트 예시

"PyQt5 QGraphicsScene 환경에서 실시간 2D 객체 스냅(Snap) 엔진을 구현하고 싶어. 마우스 커서의 현재 위치에서 일정 픽셀 반경 내에 존재하는 선(Line)의 끝점(Endpoint)과 중간점(Midpoint)을 찾아서, 마우스 좌표를 해당 점으로 강제 고정(Snapping)하고 화면에 작은 녹색 사각형 마커를 표시하는 Python 메서드를 작성해줘."

------------------------------
## 5. 신뢰할 수 있는 오픈소스 및 커뮤니티 출처 (Deep Researched)
개발 시 뼈대로 삼거나 코드를 참조할 수 있는 완벽히 검증된 공식 출처 리스트입니다.
## 🐙 깃허브(GitHub) 핵심 레포지토리

* 
* [materializr](https://github.com/materializr-cad/materializr): 실제 LLM과 바이브 코딩(Vibe coded)으로 개발된 최신 오픈소스 3D CAD 프로그램입니다. OpenCASCADE 커널과 Dear ImGui UI를 사용하여 밑바닥부터 빌드된 모범 사례이므로 반드시 소스코드를 분석해야 합니다.
* [FreeCAD 공식 저장소](https://github.com/FreeCAD/FreeCAD): 오픈소스 CAD의 끝판왕입니다. C++과 Python 기반으로 OpenCASCADE를 어떻게 상용 수준으로 활용했는지 아키텍처를 참고하기 좋습니다.
* [CadQuery](https://github.com/cadquery/cadquery): Python 코드로 3D 기하학을 제어하는 프레임워크로, GUI 없이 기하학 연산 백엔드를 구축할 때 유용합니다.
* [libdxfrw](https://github.com/LibreCAD/libdxfrw): LibreCAD 진영에서 관리하는 DWG/DXF 읽기/쓰기 핵심 라이브러리입니다.
* [webcad](https://github.com/elhakimz/webcad): 웹 브라우저 기반으로 OpenCASCADE Wasm과 스냅 엔진을 구현한 프로젝트로, 경량화된 스냅 로직을 참고하기 좋습니다. [1, 4, 6, 10, 12, 13, 14] 
* 

## 🤗 허깅페이스(Hugging Face) 및 AI 모델 리포지토리

* 
* [Hugging Face CAD Models Search](https://huggingface.co/models?search=CAD): Text-to-CAD나 DeepCAD 같이 자연어나 AI 스크립트로 3D 형상을 복원하는 최신 인공지능 모델 및 데이터셋(ABC Dataset 등) 동향을 파악할 수 있습니다. [15] 
* 

## 🌐 공식 개발자 및 사용자 커뮤니티 포럼

* 
* [AutoCAD Developer Center](https://www.autodesk.com/developer-network/platform-technologies/autocad): AutoCAD의 공식 ObjectARX 및 DXF 사양서(Specification)를 제공합니다. 도면 호환성을 완벽하게 맞추기 위해 파서(Parser)를 개발할 때 공식 문서 규격을 반드시 대조해야 합니다.
* [Open CASCADE 기술 포럼](https://dev.opencascade.org/): 커널 사용 중 발생하는 수학적 오류나 3D 연산 버그를 해결할 수 있는 가장 신뢰도 높은 개발자 포럼입니다.
* 

------------------------------
## 6. 바이브 코딩 시 주의점 및 리스크 관리

   1. 시각적 피드백의 한계: AI 코딩 도구는 CLI(텍스트) 출력은 잘 분석하지만, 화면에 선이 비뚤어지게 그려지거나 3D 면이 깨지는 현상은 스스로 인지하지 못합니다. 인간 개발자가 화면을 보고 기하학적 수치나 렌더링 상태를 논리적 글로 풀어서 AI에게 다시 피드백을 주어야 합니다.
   2. 스파게티 코드 방지: 프롬프트를 계속 추가하다 보면 코드가 뒤엉키게 됩니다. 처음부터 객체지향 설계(UI 디자인 패턴, 데이터 처리 레이어 분리)를 명확히 요구하고, 주기적으로 AI에게 "지금까지 작성된 코드를 클린 코드로 리팩토링해줘"라고 요청해야 합니다. [11] 

In-house CAD 개발을 위한 기술 스택을 확정하기 위해 아래 내용을 알려주시면 더 맞춤형 소스코드를 제안해 드릴 수 있습니다.

* 사내 개발팀이 가장 선호하는 주력 프로그래밍 언어 (C++ / Python / TypeScript 등)
* 최종 결과물이 작동해야 하는 환경이 데스크톱 앱 형태인지, 혹은 사내 웹브라우저에서 접속하는 웹 앱 형태인지 여부
* 가장 먼저 연동해야 하는 사내 시스템 (예: ERP 제품명 또는 도면 기반 BOM 자동 산출 필요 여부)


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
