"use client";

import { useState } from "react";
import type { Tool } from "@/lib/cad/tools";

type Props = {
  tool: Tool;
  onTool: (t: Tool) => void;
  layers: { name: string; locked: boolean }[];
  layer: string;
  onLayer: (name: string) => void;
  canDelete: boolean;
  onDelete: () => void;
  canUndo: boolean;
  onUndo: () => void;
  canRedo: boolean;
  onRedo: () => void;
  canSave: boolean;
  saving: boolean;
  onSave: () => void;
  onDownload: () => void;
  onPlot: () => void;
  onScript?: () => void; // Track 9 Q4
  onParamBlock?: () => void;
};

type ToolEntry = { tool: Tool; label: string; key: string };
type Group = { name: string; tools: ToolEntry[] };

const GROUPS: Group[] = [
  {
    name: "그리기",
    tools: [
      { tool: "LINE", label: "선", key: "L" },
      { tool: "CIRCLE", label: "원", key: "C" },
      { tool: "ARC", label: "호", key: "A" },
      { tool: "PLINE", label: "폴리라인", key: "PL" },
      { tool: "RECT", label: "사각형", key: "REC" },
      { tool: "POLYGON", label: "다각형", key: "POL" },
      { tool: "ELLIPSE", label: "타원", key: "EL" },
      { tool: "SPLINE", label: "스플라인", key: "SPL" },
      { tool: "POINT", label: "점", key: "PO" },
      { tool: "TEXT", label: "문자", key: "T" },
      { tool: "HATCH", label: "해치", key: "H" },
      { tool: "LEADER", label: "지시선", key: "LE" },
    ],
  },
  {
    name: "수정",
    tools: [
      { tool: "SELECT", label: "선택", key: "V" },
      { tool: "MOVE", label: "이동", key: "M" },
      { tool: "COPY", label: "복사", key: "CO" },
      { tool: "ROTATE", label: "회전", key: "RO" },
      { tool: "SCALE", label: "축척", key: "SC" },
      { tool: "MIRROR", label: "대칭", key: "MI" },
      { tool: "ARRAY", label: "배열", key: "AR" },
      { tool: "ARRAYPOLAR", label: "원형배열", key: "ARP" },
      { tool: "OFFSET", label: "간격띄우기", key: "O" },
      { tool: "STRETCH", label: "늘리기", key: "S" },
    ],
  },
  {
    name: "편집",
    tools: [
      { tool: "FILLET", label: "모깎기", key: "F" },
      { tool: "CHAMFER", label: "모따기", key: "CHA" },
      { tool: "TRIM", label: "자르기", key: "TR" },
      { tool: "EXTEND", label: "연장", key: "EX" },
      { tool: "BREAK", label: "끊기", key: "BR" },
      { tool: "JOIN", label: "결합", key: "J" },
      { tool: "EXPLODE", label: "분해", key: "X" },
      { tool: "SPLIT", label: "분할", key: "SP" },
      { tool: "BLOCK", label: "블록", key: "B" },
      { tool: "INSERT", label: "삽입", key: "I" },
    ],
  },
  {
    name: "치수",
    tools: [
      { tool: "DIMLINEAR", label: "선형", key: "D" },
      { tool: "DIMALIGNED", label: "정렬", key: "DAL" },
      { tool: "DIMANGULAR", label: "각도", key: "DAN" },
      { tool: "DIMRADIUS", label: "반지름", key: "DRA" },
      { tool: "DIMDIAMETER", label: "지름", key: "DDI" },
      { tool: "DIMBASELINE", label: "기준선", key: "DBA" },
      { tool: "DIMCONTINUE", label: "연속", key: "DCO" },
    ],
  },
  {
    name: "조회",
    tools: [
      { tool: "DIST", label: "거리", key: "DI" },
      { tool: "AREA", label: "면적", key: "AA" },
      { tool: "LIST", label: "목록", key: "LI" },
    ],
  },
];

const btn =
  "rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-40 disabled:hover:bg-transparent";

export default function ToolPalette(p: Props) {
  // ponytail: collapsed state per group, all expanded by default; localStorage if users want persistence
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const toggle = (name: string) => setCollapsed((c) => ({ ...c, [name]: !c[name] }));

  return (
    <div role="toolbar" aria-label="도구" className="flex flex-wrap items-center gap-1 border-b border-line bg-card px-2 py-1">
      {GROUPS.map((g) => (
        <div key={g.name} className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => toggle(g.name)}
            className="rounded px-1 py-0.5 text-xs font-semibold text-muted hover:text-foreground"
            title={`${g.name} ${collapsed[g.name] ? "펼치기" : "접기"}`}
          >
            {g.name}{collapsed[g.name] ? "▸" : "▾"}
          </button>
          {!collapsed[g.name] &&
            g.tools.map((t) => (
              <button
                key={t.tool}
                type="button"
                aria-label={`${t.label} (${t.key})`}
                title={`${t.label} (${t.key})`}
                aria-pressed={p.tool === t.tool}
                onClick={() => p.onTool(t.tool)}
                className={`${btn} ${p.tool === t.tool ? "bg-[var(--color-primary-light)] border-[var(--color-primary)]" : ""}`}
              >
                {t.label}
              </button>
            ))}
          <span className="mx-0.5 h-5 w-px bg-line" aria-hidden />
        </div>
      ))}
      <button type="button" aria-label="삭제 (Del)" title="삭제 (Del)" disabled={!p.canDelete} onClick={p.onDelete} className={btn}>
        삭제
      </button>
      <button type="button" aria-label="실행 취소 (Ctrl+Z)" title="실행 취소 (Ctrl+Z)" disabled={!p.canUndo} onClick={p.onUndo} className={btn}>
        Undo
      </button>
      <button type="button" aria-label="다시 실행 (Ctrl+Y)" title="다시 실행 (Ctrl+Y)" disabled={!p.canRedo} onClick={p.onRedo} className={btn}>
        Redo
      </button>
      <span className="mx-1 h-5 w-px bg-line" aria-hidden />
      <label className="flex items-center gap-1 text-sm text-body">
        작도 레이어
        <select
          value={p.layer}
          onChange={(e) => p.onLayer(e.target.value)}
          className="rounded-md border border-line bg-card px-1 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          {p.layers.map((l) => (
            <option key={l.name} value={l.name} disabled={l.locked}>
              {l.name}
              {l.locked ? " (잠금)" : ""}
            </option>
          ))}
        </select>
      </label>
      <span className="mx-1 h-5 w-px bg-line" aria-hidden />
      <button type="button" aria-label="저장 (Ctrl+S)" title="저장 (Ctrl+S)" disabled={!p.canSave || p.saving} onClick={p.onSave} className={btn}>
        {p.saving ? "저장 중..." : "저장"}
      </button>
      <button type="button" onClick={p.onDownload} title="DXF 다운로드" aria-label="DXF 다운로드" className={btn}>
        DXF 다운로드
      </button>
      <button type="button" onClick={p.onPlot} title="도면 PDF 출력" className={btn}>
        PDF 출력
      </button>
      {p.onParamBlock && (
        <button type="button" onClick={p.onParamBlock} title="치수를 바꿔 다시 만드는 파라미터 블록 삽입 (블록 하나를 선택하고 누르면 값 수정)" className={btn}>
          파라미터 블록
        </button>
      )}
      {p.onScript && (
        <button type="button" onClick={p.onScript} title="명령 스크립트(.axs) 실행 — 한 줄에 명령 입력 하나" className={btn}>
          스크립트
        </button>
      )}
    </div>
  );
}
