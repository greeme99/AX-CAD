"use client";

import type { BBox } from "@/lib/cad/mesh";

export type Direction = "+Z" | "-Z";
export type Feature = {
  feature_id: number;
  seq: number;
  feature_type: "EXTRUDE";
  params: { source_revision_id: number; handles: string[]; distance: number; direction: Direction };
  status: "OK" | "ERROR";
  error_code: string | null;
  metrics: { volume_mm3: number; surface_area_mm2: number; bbox: BBox & { size: [number, number, number] } } | null;
  created_at: string;
  updated_at: string;
};

export const featureName = (f: Feature) => `Extrude${String(f.seq).padStart(3, "0")}`;

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
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring ${f.feature_id === selectedId ? "bg-[var(--color-primary-light)] text-[var(--color-primary)]" : "text-body hover:bg-hover"}`}
            >
              <span className="font-medium">{featureName(f)}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {f.params.direction}
                {f.params.distance}
              </span>
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
