# DESIGN.md — AX-CAD Design System & UI Specification

본 문서는 **Light SaaS 대시보드(Edaca Style)**와 **Dark/Glassmorphism 시스템**, 그리고 **FreeCAD/QCAD 워크벤치 UX & shadcn/ui 기반 컴포넌트 아키텍처**를 통합한 AX-CAD의 전역 디자인 소스이자 UI/UX 단일 진실 공급원(Single Source of Truth)이다.

---

## 0. Source of Truth & Fallback Rules (디자인 소스 탐색 수칙)

- **1순위 (프로젝트 지침)**: 프로젝트 루트의 `DESIGN.md` (본 문서)
- **2순위 (하네스 전역 지침)**: 사용자 홈/글로벌 디렉터리의 `.claude/DESIGN.md`
- **적용 수칙**:
  - UI 컴포넌트, 색상, 레이아웃, 스타일 작업 전 반드시 본 문서를 읽고 정의된 토큰과 패턴을 준수한다.
  - 임의의 임시 색상(예: 단순 `#ff0000`, `#0000ff`)이나 비표준 여백 사용을 엄격히 금지한다.
  - CAD 뷰포트와 견적 데이터 테이블의 대비(Contrast) 및 시인성을 최우선으로 확보한다.

---

## 1. Design Principles (디자인 원칙)

1. **Workbench 기반 작업 공간 분리**:
   - FreeCAD 스타일의 작업 맥락 분리: 2D Drafting, 3D Modeling, Quote Builder, Validation 모드를 전환 가능한 워크벤치 형태로 제공.
2. **Dual-Theme & High-Precision Canvas**:
   - UI 쉘은 맑은 **Clean Light SaaS Theme** (기본)와 **Dark/Glassmorphic Theme**를 지원.
   - CAD 뷰포트(Canvas)는 도면 정밀도와 눈 피로도 감소를 위해 전용 캔버스 배경 테마(Dark Slate / Deep Navy / Blueprint Grid)를 독립 적용.
3. **Pill Controls & Micro-interactions**:
   - 툴 팔레트 및 스냅 토글, 뱃지는 부드러운 Pill/Rounded 스타일을 적용하고 호버(`translateY(-1px)`), 클릭 반응 보장.
4. **추적 가능한 견적 UI (Traceable Quote UI)**:
   - 견적 테이블의 각 행은 원천 도면 요소(DXF 엔티티, STEP 부품)와 시각적으로 1:1 하이라이트 연동(Trace Link).

---

## 2. Color System & Semantic Tokens

### 2.1 UI Shell Color Tokens (Light / Dark)
```css
:root, [data-theme="light"] {
  /* Canvas & Surface */
  --color-bg-base: #f8fafc;         /* Slate 50 (앱 전체 배경) */
  --color-bg-surface: #ffffff;      /* 카드, 사이드바, 패널 배경 */
  --color-bg-subtle: #f1f5f9;       /* Slate 100 (헤더, 보조 컨테이너) */
  --color-bg-hover: #e2e8f0;        /* 호버 배경 */

  /* Primary Accent */
  --color-primary: #0066ff;         /* Electric Royal Blue */
  --color-primary-hover: #0052cc;
  --color-primary-light: #e0edff;

  /* CAD Distinct Accent (Drafting & Selection) */
  --color-cad-selection: #2563eb;   /* 선택된 기하 객체 하이라이트 */
  --color-cad-snap: #10b981;        /* 스냅 마커 (Emerald Green) */
  --color-cad-warning: #f59e0b;     /* 기하 오류/경고 (Amber) */
  --color-cad-dimension: #8b5cf6;   /* 치수 보조선 (Purple) */

  /* Borders & Shadows */
  --color-border: #e2e8f0;
  --color-border-strong: #cbd5e1;
  --shadow-card: 0 4px 20px -2px rgba(15, 23, 42, 0.05);

  /* Typography */
  --color-text-main: #0f172a;       /* Slate 900 */
  --color-text-body: #334155;       /* Slate 700 */
  --color-text-muted: #64748b;      /* Slate 500 */
}

[data-theme="dark"] {
  --color-bg-base: #090d16;
  --color-bg-surface: #111827;
  --color-bg-subtle: #1f2937;
  --color-bg-hover: #374151;

  --color-primary: #3b82f6;
  --color-primary-hover: #60a5fa;
  --color-primary-light: rgba(59, 130, 246, 0.15);

  --color-cad-selection: #60a5fa;
  --color-cad-snap: #34d399;
  --color-cad-warning: #fbbf24;
  --color-cad-dimension: #a78bfa;

  --color-border: #1f2937;
  --color-border-strong: #374151;
  --shadow-card: 0 4px 20px -2px rgba(0, 0, 0, 0.5);

  --color-text-main: #f9fafb;
  --color-text-body: #e5e7eb;
  --color-text-muted: #9ca3af;
}
```

### 2.2 CAD Viewport Canvas Theme (캔버스 전용)
- **배경색**: `#181b20` (Dark Canvas) / `#ffffff` (White Plotting Canvas) 전환 가능
- **그리드 색상**: `rgba(255, 255, 255, 0.06)` (Dark) / `rgba(0, 0, 0, 0.06)` (Light)
- **스냅 마커**:
  - Endpoint: 녹색 사각형 (`#10b981`, 8px)
  - Midpoint: 시안 삼각형 (`#06b6d4`, 8px)
  - Center: 오렌지 원형 (`#f97316`, 8px)
  - Intersection: 마젠타 X (`#ec4899`, 8px)

---

## 3. UI Layout & Component Architecture

### 3.1 5분할 워크스페이스 레이아웃 (Layout Specification)
```text
+-----------------------------------------------------------------------------------+
| Top Global Header: 로고, 프로젝트명, 워크벤치 모드 탭, 테마 전환, 저장/내보내기    |
+-------------------+-------------------------------------------+-------------------+
| Left Panel        | Center Viewport                           | Right Panel       |
| - 모드 셀렉터     | - 2D Canvas (ezdxf 렌더러)                 | - 속성 검사기     |
| - 레이어 트리     | - 3D Viewport (OCCT / Three.js 뷰어)      |   (선택 객체 치수)|
| - 부품 목록       | - 오버레이 플로팅 툴 팔레트               | - 견적 인스펙터   |
| - 스케치 히스토리 | - 스냅 상태 및 좌표 인디케이터            |   (가공비/재료비) |
+-------------------+-------------------------------------------+-------------------+
| Bottom Command & Status Bar: CLI 명령창, 좌표(X,Y,Z), 스냅 토글, 단위(mm), 로그     |
+-----------------------------------------------------------------------------------+
```

### 3.2 핵심 컴포넌트 목록 (shadcn/ui 기반 구조)
1. **App Shell**:
   - `AppHeader`: 파일 I/O(DXF/STEP 열기, 저장), 실행 모드(Drafting/Modeling/Quote) 전환기
   - `AppSidebar`: 프로젝트 탐색기, 레이어 트리(`LayerTree.tsx`), 히스토리 뷰
   - `AppStatusBar`: 현재 좌표, 절대/상대 모드, 그리드 On/Off, 스냅 모드 버튼 그룹
2. **CAD Viewport**:
   - `CanvasViewport`: 2D HTML5 Canvas 기반 정밀 렌더러 (줌/팬/스냅)
   - `ThreeViewport`: 3D WebGL / OCCT B-Rep 형상 뷰포트
   - `ToolPalette`: 선, 원, 호, 사각형, 치수기입, 트림 플로팅 툴바
   - `SnapOverlay`: Endpoint, Midpoint 마커 및 가이드라인 오버레이
3. **Quote & ERP Integration**:
   - `QuoteBuilder`: 도면 분석 결과 및 공정 선택 대시보드
   - `QuoteLineTable`: 품번, 도면 객체, 가공 공정, 절단길이/면적, 단가, 공급가액, 추적 링크 테이블
   - `QuoteSummaryCard`: 재료비, 직접노무비, 제조간접비, 일반관리비, 이윤, VAT 계산 카드
   - `TraceabilityDrawer`: 견적 항목 클릭 시 도면 내 해당 엔티티를 하이라이트하는 드로어

---

## 4. Typography & Spacing System

- **글꼴**: `Pretendard`, `-apple-system`, `BlinkMacSystemFont`, `Inter`, `sans-serif`
- **모노스페이스 (치수/수치/명령창)**: `JetBrains Mono`, `Fira Code`, `monospace`
- **단위 규격**: CAD 치수 표시는 항상 `mm` (소수점 2자리 기본, 공차 지원 시 3자리)
- **여백 체계**: 4px 베이스라인 그리드 (4px, 8px, 12px, 16px, 24px, 32px)
