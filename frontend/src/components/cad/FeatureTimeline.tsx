"use client";

import { useRef } from "react";
import { featureName, type Feature } from "@/lib/cad/features";

const ICON: Record<string, string> = {
  SKETCH: "✏", EXTRUDE: "▣", POCKET: "▢", REVOLVE: "⟳", SWEEP: "〰", LOFT: "△",
  BOOLEAN: "⊕", PRIMITIVE: "◆", IMPORT: "↓", FILLET: "◠", CHAMFER: "◢",
  SHELL: "☐", HOLE: "⊙", STANDARD: "🔩", PATTERN_LINEAR: "⋮⋮", PATTERN_POLAR: "⟲",
  MIRROR: "⇿", THICKNESS: "≡", DRAFT: "∠",
};

type Props = {
  features: Feature[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  bar: number | null;
  onBar?: (seq: number | null) => void;
};

export default function FeatureTimeline({ features, selectedId, onSelect, bar, onBar }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  if (!features.length) return null;

  return (
    <div className="flex h-10 shrink-0 items-center gap-0 border-t border-line bg-card px-2 overflow-hidden">
      <span className="mr-2 shrink-0 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Timeline
      </span>
      <div ref={scrollRef} className="flex flex-1 items-center gap-0.5 overflow-x-auto scrollbar-none">
        {features.map((f, i) => {
          const err = f.status === "ERROR";
          const after = bar != null && f.seq > bar;
          const selected = f.feature_id === selectedId;
          const icon = ICON[f.feature_type] ?? "●";
          return (
            <div key={f.feature_id} className="flex items-center">
              <button
                type="button"
                title={`${featureName(f)}${f.suppressed ? " (억제)" : ""}${err ? " (오류)" : ""}`}
                onClick={() => onSelect(f.feature_id)}
                className={[
                  "flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs transition-colors",
                  selected ? "bg-[var(--color-mode-accent,var(--color-primary))]/15 text-[var(--color-mode-accent,var(--color-primary))] font-semibold" : "",
                  after ? "opacity-30" : "",
                  err ? "text-red-500" : !selected ? "text-body hover:bg-hover" : "",
                  f.suppressed ? "line-through opacity-50" : "",
                ].join(" ")}
              >
                <span className="text-sm">{icon}</span>
                <span className="max-w-20 truncate">{featureName(f)}</span>
              </button>
              {f.seq === bar && (
                <div className="mx-0.5 flex h-6 w-0.5 shrink-0 items-center">
                  <button
                    type="button"
                    title="롤백 위치 (클릭: 끝으로 이동)"
                    onClick={() => onBar?.(null)}
                    className="h-full w-1 rounded-full bg-[var(--color-mode-accent,var(--color-primary))] hover:w-1.5 transition-all"
                  />
                </div>
              )}
              {i < features.length - 1 && f.seq !== bar && (
                <span className="mx-0.5 h-px w-2 shrink-0 bg-line" />
              )}
            </div>
          );
        })}
      </div>
      {bar != null && onBar && (
        <button
          type="button"
          onClick={() => onBar(null)}
          className="ml-2 shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-hover"
        >
          끝으로
        </button>
      )}
    </div>
  );
}
