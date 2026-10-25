"use client";

import {
  booleanCandidates,
  featureName,
  featureSummary,
  type BooleanOp,
  type BooleanParams,
  type Direction,
  type ExtrudeParams,
  type Feature,
  type RevolveParams,
} from "@/lib/cad/mesh";

export { booleanCandidates, featureName, featureSummary, type BooleanOp, type BooleanParams, type Direction, type ExtrudeParams, type Feature, type RevolveParams };

export default function FeatureTree({ features, selectedId, onSelect }: { features: Feature[]; selectedId: number | null; onSelect: (id: number) => void }) {
  if (!features.length) return <p className="text-sm text-muted-foreground">Feature가 없습니다.</p>;
  return (
    <ul aria-label="Feature 목록" className="space-y-1">
      {features.map((f) => {
        const err = f.status === "ERROR";
        return (
          <li key={f.feature_id}>
            <button
              type="button"
              aria-pressed={f.feature_id === selectedId}
              onClick={() => onSelect(f.feature_id)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring ${f.feature_id === selectedId ? "bg-[var(--color-primary-light)] text-[var(--color-primary)]" : "text-body hover:bg-hover"} ${!f.visible ? "opacity-60" : ""}`}
            >
              <span className="font-medium">{featureName(f)}</span>
              <span className="font-mono text-xs text-muted-foreground">{featureSummary(f)}</span>
              {!f.visible && <span className="text-xs text-muted-foreground">(소비됨)</span>}
              <span className={`ml-auto rounded-full px-2 py-0.5 text-xs ${err ? "bg-red-100 text-red-700" : "bg-snap/15 text-emerald-700"}`}>
                <span aria-hidden>{err ? "⚠" : "✓"}</span> {err ? "오류" : "정상"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
