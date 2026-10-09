<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# 지금까지 조사한 CAD(2D, 3D) 기능과 표준견적생성 기능에 가장 적합한 UI/UX 디자인 설계 가이드를 상용 CAD 시스템과 오픈소스를 참조해서 조사해서 Design System 으로 제안해줘. 중요한 것은 반드시 근거가 있는 소스를 찾아서 신뢰할수 있고 바로 코딩에 사용할 수 있도록 개발 가이드 만든다

아래 제안은 **2D/3D CAD 편집 + 표준 견적 생성**에 맞는 UI/UX Design System 초안입니다. FreeCAD의 workbench 기반 인터페이스, QCAD의 단순하고 일관된 2D CAD UX, shadcn/ui의 “코드를 복사해 소유하는” 디자인 시스템 철학을 결합하는 방식이 가장 실무적입니다.[^1][^2][^3][^4]

## 1. 디자인 원칙

- **작업 중심(workbench) 구조**를 채택합니다. FreeCAD는 3D View, Tree View, Property View, Report View, Python Console, Status Bar, Workbench Selector를 분리해 작업 맥락을 명확히 합니다.[^3]
- **2D는 가볍고 예측 가능하게**, 3D는 **상태와 히스토리 중심**으로 설계합니다. QCAD는 모듈성, 확장성, 이식성을 강조하면서도 직관적인 UI를 핵심 장점으로 내세웁니다.[^5][^2]
- **견적은 CAD 화면에서 독립된 업무 패널**로 분리합니다. 설계자는 도면/형상을 보면서 견적 근거를 확인하고, 견적 담당자는 규칙/단가/오류를 별도 탭에서 검토해야 합니다.
- **shadcn/ui는 UI의 토대**로 사용하되, CAD 특유의 캔버스/툴바/속성창은 별도 컴포넌트로 설계합니다.[^1][^6][^4]


## 2. 화면 구조 표준

### 메인 레이아웃

- 상단: Global Header
- 좌측: Workbench / Project / Layer / Library
- 중앙: 2D/3D Canvas
- 우측: Properties / Rules / Quote Inspector
- 하단: Command Bar / Status / Messages


### 탭 구조

- **Drafting**
- **Sketch**
- **3D Model**
- **Quote**
- **Validation**
- **Report**

FreeCAD는 작업 성격에 따라 Draft, Sketcher, PartDesign, BIM 등으로 workbench를 바꾸는 개념을 제공하므로, 우리 시스템도 탭 전환이 아니라 작업 모드 전환으로 설계하는 게 좋습니다.[^3]

## 3. CAD UI/UX 패턴

### 3.1 2D Drafting 패턴

- 툴바는 선/원/호/치수/트림/오프셋처럼 자주 쓰는 도구만 노출합니다.
- 스냅은 토글형 토글 버튼 + 상태바 표시를 둡니다.
- 레이어는 tree로 보되, 색상/선종류/잠금/가시성만 단순하게 제어합니다.
- 명령은 “선택 → 명령 → 대상 지정 → 완료”의 4단계로 일관되게 유지합니다.


### 3.2 3D Modeling 패턴

- 좌측 Tree View에 feature history를 표시합니다.
- 우측 Property View에서 수치와 제약을 수정합니다.
- 중앙 뷰포트는 마우스 제스처와 휠을 우선합니다.
- 하단에는 선택된 edge/face의 맥락 정보만 보여줍니다.

FreeCAD는 Tree View가 construction history를 나타내고 Property View가 선택 객체 속성 수정에 쓰인다고 설명합니다.[^3]

### 3.3 견적 패턴

- 견적은 **도면에서 추출된 근거가 보이는 테이블 UI**가 핵심입니다.
- 각 라인은 source entity, rule code, qty, unit price, amount, trace를 표시합니다.
- “자동 계산”과 “수동 조정”을 분리합니다.
- 승인 전에는 외부 export 버튼을 비활성화합니다.


## 4. Design System 토큰

shadcn/ui는 Tailwind CSS, Radix primitives, CSS 변수 기반 theming을 사용하므로, 디자인 토큰을 먼저 정의하는 것이 좋습니다.[^1][^4]

### 컬러 토큰

- `bg-canvas`
- `bg-panel`
- `bg-panel-strong`
- `border-subtle`
- `text-primary`
- `text-muted`
- `accent-drawing`
- `accent-snap`
- `accent-warning`
- `accent-danger`
- `accent-quote`


### 상태 토큰

- `active`
- `selected`
- `hover`
- `disabled`
- `locked`
- `warning`
- `error`
- `approved`
- `draft`


### 타이포그래피

- `title-xl`: 메인 문서 제목
- `title-md`: 패널 제목
- `body`: 일반 설명
- `mono`: 코드/좌표/단가
- `caption`: 상태/메타데이터


### 간격/레이아웃

- 8px grid system
- 패널 간 12~16px gap
- 좌우 패널 너비 고정 + 중앙 유동
- 도구 아이콘은 24px 기준


## 5. 핵심 컴포넌트 설계

### 공통 UI

- Button
- Input
- Select
- Tabs
- Dialog
- Drawer
- Tooltip
- Badge
- Table
- Card
- Accordion
- Toast
- Dropdown Menu


### CAD 특화 UI

- `CanvasViewport`
- `ToolPalette`
- `LayerTree`
- `PropertyInspector`
- `SelectionInfoBar`
- `SnapOverlay`
- `CommandPrompt`
- `HistoryTree`
- `QuoteInspector`
- `ValidationPanel`
- `TraceTable`

shadcn/ui는 복사해서 코드로 소유하는 구조이므로, CAD 특화 컴포넌트는 기존 primitives를 조합해서 만드는 것이 맞습니다.[^1][^7][^8]

## 6. UI 정보 밀도 가이드

### 설계자용 화면

- 도구 수는 적게, 상태 정보는 충분히.
- 선택/스냅/좌표/레이어를 즉시 확인 가능하게.
- 수치 입력은 인라인 편집 지원.


### 견적 담당자용 화면

- 항목 테이블 밀도를 높게.
- source trace를 한 클릭으로 열람.
- 경고는 색상뿐 아니라 텍스트와 아이콘을 같이 사용.


### 관리자용 화면

- 기준정보와 템플릿 관리 중심.
- 불필요한 캔버스는 최소화.
- 작업 로그와 승인 흐름을 우선.


## 7. 화면별 구성안

### 7.1 프로젝트 홈

- 최근 프로젝트 카드
- 검색/필터
- 신규 프로젝트 버튼
- 작업 상태 배지


### 7.2 2D Draft 화면

- 좌측: Layer / Tool / Library
- 중앙: Canvas
- 우측: Property / Dimension / Quote hint
- 하단: Command bar / snap / coordinate


### 7.3 3D Model 화면

- 좌측: Feature tree
- 중앙: 3D viewport
- 우측: Property / Material / PMI
- 하단: selection info / navigation mode


### 7.4 Quote 화면

- 좌측: Source files
- 중앙: Quote lines
- 우측: Rule selector / validation / summary
- 하단: total amount / approval / export


### 7.5 Report 화면

- PDF preview
- export controls
- traceability panel
- version history


## 8. 상용 CAD와 오픈소스에서 가져올 포인트

- **FreeCAD**: workbench 기반 구조, Tree/Property/Report/Python console 분리.[^3]
- **QCAD**: 2D CAD의 직관성, 모듈화, 스크립트 기반 확장, 문서 체계.[^5][^2]
- **shadcn/ui**: 코드 소유, 접근성, 디자인 토큰, component composition.[^1][^6][^4]
- **FreeCAD UI 스크린샷**는 상단 툴바, 좌측 트리, 우측 속성, 하단 상태바 조합이 표준적인 CAD UX임을 보여줍니다.


## 9. 개발용 디자인 규칙

- 캔버스는 절대 다른 UI보다 우선한다.
- 좌측은 구조, 우측은 속성, 하단은 상태.
- 명령은 항상 텍스트로도 표시한다.
- 도면 작업은 키보드 단축키를 우선 제공한다.
- 견적 작업은 근거와 결과를 한 화면에서 추적 가능하게 한다.
- 모든 색상은 semantic token으로만 사용한다.


## 10. 바로 코딩할 때의 구현 우선순위

### 1단계

- design tokens 정의
- shadcn/ui 기본 컴포넌트 설치
- App shell 구성
- CAD viewport skeleton 생성


### 2단계

- LayerTree, PropertyInspector, ToolPalette 구현
- QuoteInspector, ValidationPanel 구현
- 상태바와 command bar 추가


### 3단계

- 다크모드 / 라이트모드
- selection highlight
- snap overlay
- rule-based validation badge


### 4단계

- report preview
- traceability drawer
- PDF export dialog
- approval workflow UI


## 11. 권장 네비게이션 구조

```text
/home
/projects
/projects/[id]/draft
/projects/[id]/model
/projects/[id]/quote
/projects/[id]/report
/admin/settings
/admin/master-data
/admin/audit
```


## 12. 실전 설계 결론

이 시스템의 UI/UX는 **“CAD 작업 공간”과 “견적 업무 공간”을 분리하되 같은 프로젝트 컨텍스트에서 연결**하는 설계가 가장 좋습니다. FreeCAD처럼 작업 모드 중심 구조를 가져오고, QCAD처럼 단순하고 예측 가능한 2D 흐름을 유지하며, shadcn/ui처럼 코드 기반 Design System으로 구현하면 유지보수와 확장성이 높습니다.[^1][^2][^3]

원하시면 다음 단계로 바로 이어서\
**“Figma용 Design System 명세서”**,\
**“shadcn/ui 기반 컴포넌트별 구현 코드”**,\
\*\*“CAD/견적 화면 와이어프레임 상세안”\*\*까지 이어서 작성해드리겠습니다.

<span style="display:none">[^10][^11][^9]</span>

<div align="center">⁂</div>

[^1]: https://ui.shadcn.com/docs

[^2]: https://www.qcad.org/en/78-qcad

[^3]: https://wiki.freecad.org/Getting_started

[^4]: https://ui.shadcn.com/llms.txt

[^5]: https://www.qcad.org/en/documentation

[^6]: https://www.shadcndesign.com/docs/components

[^7]: https://www.designsystems.one/design-systems/shadcn-ui

[^8]: https://ui.shadcn.com/docs/official

[^9]: https://www.qcad.org/doc/qcad/latest/reference/en/qcad_reference_manual_en.html

[^10]: https://ui.shadcn.com/docs/figma

[^11]: https://ui.shadcn.com/docs/changelog/2026-03-cli-v4

