"use client";

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
  onDownload: () => void; // authenticated fetch, a plain link cannot send the Bearer token
};

const TOOLS: { tool: Tool; label: string; key: string }[] = [
  { tool: "SELECT", label: "선택", key: "V" },
  { tool: "LINE", label: "선", key: "L" },
  { tool: "CIRCLE", label: "원", key: "C" },
  { tool: "ARC", label: "호", key: "A" },
  { tool: "PLINE", label: "폴리라인", key: "PL" },
  { tool: "RECT", label: "사각형", key: "REC" },
  { tool: "POLYGON", label: "다각형", key: "POL" },
  { tool: "TEXT", label: "문자", key: "T" },
  { tool: "MOVE", label: "이동", key: "M" },
  { tool: "COPY", label: "복사", key: "CO" },
  { tool: "ROTATE", label: "회전", key: "RO" },
  { tool: "SCALE", label: "축척", key: "SC" },
  { tool: "MIRROR", label: "대칭", key: "MI" },
  { tool: "ARRAY", label: "배열", key: "AR" },
  { tool: "ARRAYPOLAR", label: "원형배열", key: "ARP" },
  { tool: "OFFSET", label: "간격띄우기", key: "O" },
  { tool: "FILLET", label: "모깎기", key: "F" },
  { tool: "TRIM", label: "자르기", key: "TR" },
  { tool: "EXTEND", label: "연장", key: "EX" },
  { tool: "CHAMFER", label: "모따기", key: "CHA" },
  { tool: "BREAK", label: "끊기", key: "BR" },
  { tool: "JOIN", label: "결합", key: "J" },
  { tool: "EXPLODE", label: "분해", key: "X" },
  { tool: "STRETCH", label: "늘리기", key: "S" },
  { tool: "HATCH", label: "해치", key: "H" },
  { tool: "BLOCK", label: "블록", key: "B" },
  { tool: "INSERT", label: "삽입", key: "I" },
  { tool: "DIMLINEAR", label: "선형치수", key: "D" },
  { tool: "DIMALIGNED", label: "정렬치수", key: "DAL" },
  { tool: "DIMANGULAR", label: "각도치수", key: "DAN" },
  { tool: "DIMRADIUS", label: "반지름치수", key: "DRA" },
  { tool: "DIMDIAMETER", label: "지름치수", key: "DDI" },
  { tool: "DIMBASELINE", label: "기준선치수", key: "DBA" },
  { tool: "DIMCONTINUE", label: "연속치수", key: "DCO" },
];

const btn =
  "rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-40 disabled:hover:bg-transparent";

export default function ToolPalette(p: Props) {
  return (
    <div role="toolbar" aria-label="도구" className="flex flex-wrap items-center gap-1 border-b border-line bg-card px-2 py-1">
      {TOOLS.map((t) => (
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
      <span className="mx-1 h-5 w-px bg-line" aria-hidden />
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
    </div>
  );
}
