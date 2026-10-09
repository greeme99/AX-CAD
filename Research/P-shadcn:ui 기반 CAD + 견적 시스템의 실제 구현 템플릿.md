<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# shadcn/ui 기반 컴포넌트별 구현 코드 작성하고, CAD와 견적 화면 와이어프레임 상세안 그리고, Figma용 Design System 명세서 작성하고, Tailwind CSS 토큰과 테마 설정 코드 생성하고, React 컴포넌트 구조와 상태 관리 설계한다. 중요한 것은 반드시 근거가 있는 소스를 찾아서 신뢰할수 있고 바로 코딩에 사용할 수 있도록 개발 가이드 만든다

아래는 **shadcn/ui 기반 CAD + 견적 시스템의 실제 구현 템플릿**입니다. 컴포넌트 코드, 와이어프레임 상세안, Figma용 Design System 명세, Tailwind 테마 설정, React 상태관리 구조를 한 번에 연결해두었습니다. shadcn/ui는 공식적으로 CSS 변수 기반 테마와 복사-소유 방식 컴포넌트를 권장하고, Sidebar/Table/Form/Dialog 등의 base component를 조합해서 앱 쉘과 데이터 UI를 구성하도록 안내합니다.[^1][^2][^3][^4]

## 1. 폴더 구조

```text
frontend/
├─ src/
│  ├─ app/
│  │  ├─ layout.tsx
│  │  ├─ page.tsx
│  │  ├─ projects/
│  │  │  ├─ page.tsx
│  │  │  └─ [projectId]/
│  │  │     ├─ draft/page.tsx
│  │  │     ├─ model/page.tsx
│  │  │     ├─ quote/page.tsx
│  │  │     └─ report/page.tsx
│  ├─ components/
│  │  ├─ shell/
│  │  │  ├─ AppSidebar.tsx
│  │  │  ├─ AppHeader.tsx
│  │  │  └─ AppStatusBar.tsx
│  │  ├─ cad/
│  │  │  ├─ CanvasViewport.tsx
│  │  │  ├─ ToolPalette.tsx
│  │  │  ├─ LayerTree.tsx
│  │  │  ├─ PropertyInspector.tsx
│  │  │  ├─ SnapOverlay.tsx
│  │  │  └─ CommandPrompt.tsx
│  │  ├─ quote/
│  │  │  ├─ QuoteBuilder.tsx
│  │  │  ├─ SourceFilePanel.tsx
│  │  │  ├─ QuoteLineTable.tsx
│  │  │  ├─ ValidationPanel.tsx
│  │  │  ├─ QuoteSummaryCard.tsx
│  │  │  └─ TraceabilityDrawer.tsx
│  │  └─ ui/
│  │     ├─ button.tsx
│  │     ├─ card.tsx
│  │     ├─ dialog.tsx
│  │     ├─ input.tsx
│  │     ├─ table.tsx
│  │     ├─ textarea.tsx
│  │     ├─ badge.tsx
│  │     ├─ sidebar.tsx
│  │     └─ form.tsx
│  ├─ lib/
│  │  ├─ api.ts
│  │  ├─ query-client.ts
│  │  ├─ types.ts
│  │  ├─ theme.ts
│  │  └─ utils.ts
│  ├─ store/
│  │  ├─ useAppStore.ts
│  │  ├─ useQuoteStore.ts
│  │  └─ useCadStore.ts
│  └─ styles/
│     └─ globals.css
└─ tailwind.config.ts
```

shadcn/ui의 Sidebar는 `SidebarProvider`, `SidebarTrigger`, `SidebarHeader`, `SidebarContent`, `SidebarFooter`를 중심으로 조합하며, Table은 단순 테이블부터 TanStack Table과의 결합까지 지원합니다.[^2][^4][^5][^6]

## 2. Tailwind 토큰과 테마 설정

shadcn/ui는 CSS 변수 기반 테마를 사용하며, Tailwind의 theme variables(`@theme`) 또는 CSS 변수로 semantic token을 관리하는 방식을 권장합니다.[^7][^8][^1]

### `src/styles/globals.css`

```css
@import "tailwindcss";

@theme {
  --color-background: 0 0% 100%;
  --color-foreground: 222.2 84% 4.9%;
  --color-card: 0 0% 100%;
  --color-card-foreground: 222.2 84% 4.9%;
  --color-popover: 0 0% 100%;
  --color-popover-foreground: 222.2 84% 4.9%;
  --color-primary: 222.2 47.4% 11.2%;
  --color-primary-foreground: 210 40% 98%;
  --color-secondary: 210 40% 96.1%;
  --color-secondary-foreground: 222.2 47.4% 11.2%;
  --color-muted: 210 40% 96.1%;
  --color-muted-foreground: 215.4 16.3% 46.9%;
  --color-accent: 210 40% 96.1%;
  --color-accent-foreground: 222.2 47.4% 11.2%;
  --color-destructive: 0 84.2% 60.2%;
  --color-destructive-foreground: 210 40% 98%;
  --color-border: 214.3 31.8% 91.4%;
  --color-input: 214.3 31.8% 91.4%;
  --color-ring: 222.2 84% 4.9%;
  --radius: 0.75rem;

  --color-canvas: 210 20% 98%;
  --color-panel: 0 0% 100%;
  --color-panel-strong: 210 20% 96%;
  --color-snap: 142 76% 36%;
  --color-warning: 38 92% 50%;
  --color-quote: 222.2 84% 4.9%;
}

.dark {
  --color-background: 222.2 84% 4.9%;
  --color-foreground: 210 40% 98%;
  --color-card: 222.2 84% 4.9%;
  --color-card-foreground: 210 40% 98%;
  --color-popover: 222.2 84% 4.9%;
  --color-popover-foreground: 210 40% 98%;
  --color-primary: 210 40% 98%;
  --color-primary-foreground: 222.2 47.4% 11.2%;
  --color-secondary: 217.2 32.6% 17.5%;
  --color-secondary-foreground: 210 40% 98%;
  --color-muted: 217.2 32.6% 17.5%;
  --color-muted-foreground: 215 20.2% 65.1%;
  --color-accent: 217.2 32.6% 17.5%;
  --color-accent-foreground: 210 40% 98%;
  --color-destructive: 0 62.8% 30.6%;
  --color-destructive-foreground: 210 40% 98%;
  --color-border: 217.2 32.6% 17.5%;
  --color-input: 217.2 32.6% 17.5%;
  --color-ring: 212.7 26.8% 83.9%;
}
```

Tailwind 공식 문서는 `@theme`로 색상, 폰트, breakpoints, spacing 등을 선언하고, 색상 namespace를 `--color-*`로 관리하는 방식을 설명합니다. shadcn/ui 테마 문서도 CSS 변수를 `background`, `foreground`, `primary`, `border` 같은 semantic token으로 쓰라고 안내합니다.[^9][^8][^1][^7]

## 3. shadcn/ui 컴포넌트 구현 예시

### 3.1 App Sidebar

```tsx
"use client";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Home, DraftingCompass, Calculator, FileText, Settings } from "lucide-react";

const items = [
  { title: "Projects", url: "/projects", icon: Home },
  { title: "Draft", url: "/draft", icon: DraftingCompass },
  { title: "Quote", url: "/quote", icon: Calculator },
  { title: "Report", url: "/report", icon: FileText },
  { title: "Admin", url: "/admin", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon" variant="inset">
        <SidebarHeader>
          <div className="px-3 py-2 text-sm font-semibold">CAD Quote System</div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Workspace</SidebarGroupLabel>
            <SidebarMenu>
              {items.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild>
                    <a href={item.url} className="flex items-center gap-2">
                      <item.icon className="h-4 w-4" />
                      <span>{item.title}</span>
                    </a>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <div className="px-3 py-2 text-xs text-muted-foreground">v1.0.0</div>
        </SidebarFooter>
      </Sidebar>

      <div className="flex min-h-screen flex-1 flex-col">
        <header className="flex h-14 items-center gap-2 border-b px-4">
          <SidebarTrigger />
          <h1 className="text-sm font-medium">In-house CAD</h1>
        </header>
        <main className="flex-1">{children}</main>
      </div>
    </SidebarProvider>
  );
}
```

Sidebar는 `SidebarProvider`로 감싸고 `SidebarTrigger`를 루트 레이아웃에 두는 것이 공식 권장 패턴입니다.[^10][^11][^2]

### 3.2 Quote Line Table

```tsx
"use client";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

export type QuoteLine = {
  line_no: number;
  item_code: string;
  item_name: string;
  qty: number;
  unit: string;
  unit_price: number;
  amount: number;
  status?: "AUTO" | "MANUAL" | "WARN";
};

export function QuoteLineTable({ lines }: { lines: QuoteLine[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>No</TableHead>
          <TableHead>Code</TableHead>
          <TableHead>Name</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead>Unit</TableHead>
          <TableHead className="text-right">Unit Price</TableHead>
          <TableHead className="text-right">Amount</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((l) => (
          <TableRow key={l.line_no}>
            <TableCell>{l.line_no}</TableCell>
            <TableCell>{l.item_code}</TableCell>
            <TableCell>{l.item_name}</TableCell>
            <TableCell className="text-right">{l.qty}</TableCell>
            <TableCell>{l.unit}</TableCell>
            <TableCell className="text-right">{l.unit_price.toLocaleString()}</TableCell>
            <TableCell className="text-right">{l.amount.toLocaleString()}</TableCell>
            <TableCell>
              <Badge variant={l.status === "WARN" ? "destructive" : "outline"}>
                {l.status ?? "AUTO"}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

Table 컴포넌트는 공식 문서에서 간단한 composition 방식과 TanStack Table 확장 방식을 모두 안내합니다.[^4][^5]

### 3.3 CAD Canvas Skeleton

```tsx
"use client";

export function CanvasViewport() {
  return (
    <div className="relative h-[calc(100vh-8rem)] rounded-lg border bg-[hsl(var(--color-canvas))]">
      <div className="absolute left-4 top-4 rounded-md bg-background/90 px-2 py-1 text-xs shadow">
        2D/3D Viewport
      </div>
      <div className="absolute bottom-4 left-4 rounded-md bg-background/90 px-2 py-1 text-xs shadow">
        Snap: ON · Grid: 10mm · Scale: 1:1
      </div>
    </div>
  );
}
```


## 4. CAD + 견적 와이어프레임 상세안

FreeCAD는 3D View, Tree View, Property View, Report View, Python Console을 분리하고, QCAD는 2D CAD 작업의 단순성과 일관성을 강조합니다.[^12][^13][^14]

### 4.1 Draft 화면

- 좌측: `LayerTree`, `ToolPalette`, `Library`
- 중앙: `CanvasViewport`
- 우측: `PropertyInspector`, `SnapPanel`, `ValidationPanel`
- 하단: `CommandPrompt`, `StatusBar`


### 4.2 Quote 화면

- 좌측: `SourceFilePanel`
- 중앙: `QuoteLineTable`
- 우측: `QuoteSummaryCard`, `ValidationPanel`, `TraceabilityDrawer`
- 하단: `Approve`, `Reject`, `Export PDF`, `Export Excel`


### 4.3 Model 화면

- 좌측: `FeatureTree`
- 중앙: 3D viewport
- 우측: `PropertyInspector`, `PMI Panel`
- 하단: `SelectionInfo`, `MeasureInfo`


### 4.4 Report 화면

- 좌측: `Report List`
- 중앙: PDF preview
- 우측: `Version`, `Audit`, `Trace`
- 하단: `Download`, `Share`, `Regenerate`


## 5. Figma Design System 명세서

shadcn/ui Figma 문서는 공식 컴포넌트와 동일한 구조를 Figma에서 재현하는 방향을 제시합니다. 따라서 Figma에는 아래 네 계층을 두는 게 좋습니다.[^15][^16]

### 5.1 Foundations

- Colors
- Type scale
- Spacing
- Radius
- Shadows
- Icons
- Motion


### 5.2 Semantic Tokens

- `background`
- `foreground`
- `card`
- `primary`
- `secondary`
- `muted`
- `accent`
- `destructive`
- `border`
- `input`
- `ring`
- `canvas`
- `snap`
- `quote`
- `warning`


### 5.3 Components

- Button
- Input
- Select
- Table
- Card
- Sidebar
- Tabs
- Dialog
- Badge
- Toast
- Drawer
- Form


### 5.4 CAD-specific patterns

- Layer row
- Tool button
- Object property row
- Quote row
- Trace row
- Validation item
- Status chip


## 6. 상태 관리 설계

### 권장 구조

- **Server State**: TanStack Query
- **UI State**: Zustand
- **Form State**: React Hook Form
- **Table State**: TanStack Table


### Zustand 예시

```tsx
import { create } from "zustand";

type AppState = {
  activeProjectId: string | null;
  activeMode: "draft" | "model" | "quote" | "report";
  setActiveProjectId: (id: string | null) => void;
  setActiveMode: (mode: AppState["activeMode"]) => void;
};

export const useAppStore = create<AppState>((set) => ({
  activeProjectId: null,
  activeMode: "draft",
  setActiveProjectId: (id) => set({ activeProjectId: id }),
  setActiveMode: (mode) => set({ activeMode: mode }),
}));
```


### Quote 상태 예시

```tsx
import { create } from "zustand";

type QuoteStore = {
  lines: any[];
  selectedLineId: number | null;
  setLines: (lines: any[]) => void;
  setSelectedLineId: (id: number | null) => void;
};

export const useQuoteStore = create<QuoteStore>((set) => ({
  lines: [],
  selectedLineId: null,
  setLines: (lines) => set({ lines }),
  setSelectedLineId: (id) => set({ selectedLineId: id }),
}));
```


## 7. 실제 구현 우선순위

1. `globals.css` 테마 토큰.
2. `Sidebar`, `Table`, `Card`, `Dialog`, `Form` 설치.
3. `AppShell` 구성.
4. `CanvasViewport`, `QuoteLineTable`, `QuoteSummaryCard` 구현.
5. `Zustand`로 프로젝트/모드/선택 상태 관리.
6. `Figma`에 토큰과 컴포넌트 매핑.
7. `Draft`, `Quote`, `Report` 화면 연결.

## 8. 바로 코딩 시 체크 포인트

- shadcn/ui는 복사-소유형이므로 `components/ui`를 직접 관리합니다.[^17][^18]
- Sidebar는 Provider와 Trigger를 루트에서 구성해야 합니다.[^11][^2][^10]
- Table은 견적의 핵심이므로 filter/sort가 필요하면 TanStack Table과 결합합니다.[^5][^4]
- 테마는 CSS 변수와 `@theme`를 사용해 semantic token 중심으로 유지합니다.[^8][^1][^7]
- CAD 화면은 캔버스 우선, 우측은 속성/근거, 하단은 상태/커맨드로 고정합니다.[^13][^12]

원하시면 다음 단계로 이어서\
**“이 Design System을 반영한 Figma 페이지 구조와 컴포넌트 네이밍 규칙”**,\
**“각 UI 컴포넌트의 props 타입 정의서”**,\
\*\*“Quote/Draft/Model 화면별 실제 페이지 코드”\*\*까지 바로 작성해드리겠습니다.

<span style="display:none">[^19][^20][^21][^22][^23][^24][^25][^26][^27]</span>

<div align="center">⁂</div>

[^1]: https://ui.shadcn.com/docs/theming

[^2]: https://ui.shadcn.com/docs/components/base/sidebar

[^3]: https://ui.shadcn.com/docs/forms

[^4]: https://ui.shadcn.com/docs/components/radix/table

[^5]: https://ui.shadcn.com/docs/components/base/table

[^6]: https://ui.shadcn.com/blocks/sidebar

[^7]: https://tailwindcss.com/docs/theme

[^8]: https://tailwindcss.com/docs/functions-and-directives

[^9]: https://tailwindcss.com/docs/colors

[^10]: https://ui.shadcn.com/docs/components/radix/sidebar

[^11]: https://ui-v4.shadcn.com/docs/components/sidebar

[^12]: https://wiki.freecad.org/Getting_started

[^13]: https://www.qcad.org/en/78-qcad

[^14]: https://www.qcad.org/en/documentation

[^15]: https://ui.shadcn.com/docs/figma

[^16]: https://www.shadcndesign.com/docs/components

[^17]: https://ui.shadcn.com/docs

[^18]: https://ui.shadcn.com/docs/official

[^19]: https://v1.tailwindcss.com/docs/theme

[^20]: https://tailwindcss.com/docs/adding-custom-styles

[^21]: https://v3.tailwindcss.com/docs/configuration

[^22]: https://www.shadcn-ui.cn/docs/components

[^23]: https://www.shadcn-ui.cn/docs/components/dialog

[^24]: https://ds.shadcn.com/docs/guides/theming

[^25]: https://ui.shadcn.com/docs/components/base/sidebar.md

[^26]: https://www.shadcn.io/ui/sidebar

[^27]: https://shadcnstudio.com/docs/components/form

