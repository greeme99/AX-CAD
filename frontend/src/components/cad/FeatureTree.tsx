"use client";

import { featureName, OP_SYMBOL, type Feature } from "@/lib/cad/features";

function summary(f: Feature, byId: Map<number, Feature>) {
  if (f.feature_type === "EXTRUDE") return `${f.params.direction}${f.params.distance}`;
  if (f.feature_type === "REVOLVE") return `${f.params.angle_deg}°`;
  const n = (id: number) => (byId.has(id) ? featureName(byId.get(id)!) : `#${id}`);
  return `${n(f.params.target_feature_id)} ${OP_SYMBOL[f.params.op]} ${n(f.params.tool_feature_id)}`;
}

export default function FeatureTree({ features, selectedId, onSelect }: { features: Feature[]; selectedId: number | null; onSelect: (id: number) => void }) {
  if (!features.length) return <p className="text-sm text-muted-foreground">Feature가 없습니다.</p>;
  const byId = new Map(features.map((f) => [f.feature_id, f]));
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
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring ${f.feature_id === selectedId ? "bg-[var(--color-primary-light)] text-[var(--color-primary)]" : "text-body hover:bg-hover"} ${f.visible ? "" : "opacity-60"}`}
            >
              <span className="font-medium">{featureName(f)}</span>
              <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">{summary(f, byId)}</span>
              <span className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-xs ${err ? "bg-red-100 text-red-700" : f.visible ? "bg-snap/15 text-emerald-700" : "bg-muted text-muted-foreground"}`}>
                <span aria-hidden>{err ? "⚠" : f.visible ? "✓" : "↳"}</span> {err ? "오류" : f.visible ? "정상" : "사용됨"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
