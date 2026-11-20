import { api } from "@/lib/api";
import type { OutputMode, PhotoMode, ViewLabel } from "@/lib/cad/photo3d";

export type Photo3dStatus = {
  sdk_installed: boolean;
  package: string;
  provider: string;
  supported_modes: PhotoMode[];
  supported_outputs: OutputMode[];
  max_images: number;
  max_image_mb: number;
};

export type Photo3dReconstructResponse = {
  feature_id: number | null;
  mode: PhotoMode;
  output_mode: OutputMode;
  resolved_output: "PARAMETRIC" | "VISUAL_HULL";
  bbox_mm: [number, number, number];
  volume_mm3: number;
  surface_area_mm2: number;
  mm_per_px: number;
  feature_plan: Record<string, unknown>;
  ai_used: boolean;
  ai_provider: string;
  view_summaries: {
    view_label: string;
    angle_deg: number;
    width_px: number;
    height_px: number;
    fill_ratio: number;
    circularity: number;
    hole_count: number;
  }[];
  warnings: string[];
};

export const getPhoto3dStatus = () => api<Photo3dStatus>("/photo3d/status");

export function reconstructPhoto3d(
  docId: string,
  files: File[],
  meta: {
    mode: PhotoMode;
    output_mode: OutputMode;
    reference_mm: number;
    reference_axis: "X" | "Y" | "Z";
    ref_p1_px?: [number, number];
    ref_p2_px?: [number, number];
    view_labels: ViewLabel[];
    angles_deg?: number[];
    use_ai: boolean;
    create_feature?: boolean;
  },
): Promise<Photo3dReconstructResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  form.set("meta", JSON.stringify({ create_feature: true, ...meta }));
  return api<Photo3dReconstructResponse>(`/documents/${encodeURIComponent(docId)}/photo3d/reconstruct`, {
    method: "POST",
    body: form,
  });
}
