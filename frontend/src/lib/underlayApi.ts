import { api, apiBlob } from "@/lib/api";
import type { PageVec } from "@/lib/cad/underlayVec";

// 2D underlay U3: the project's PDF/DWFx files (see backend/api/routes_underlay.py)
export type UnderlayFile = {
  underlay_id: string;
  project_id: number;
  kind: "pdf" | "dwfx";
  name: string;
  sha256: string;
  size_bytes: number;
  page_count: number;
  pages: { width: number; height: number }[];
  created_at: string;
};

export const underlayFormat = (f: Pick<UnderlayFile, "kind">): "pdf" | "dwf" => (f.kind === "pdf" ? "pdf" : "dwf");
export const MAX_UNDERLAY_MB = 100;

export const listUnderlays = (projectId: number) => api<{ items: UnderlayFile[] }>(`/projects/${projectId}/underlays`).then((r) => r.items);

export function uploadUnderlay(projectId: number, file: File): Promise<UnderlayFile> {
  if (!/\.(pdf|dwfx)$/i.test(file.name)) return Promise.reject(new Error(/\.dwf$/i.test(file.name) ? "고전 DWF(.dwf)는 지원하지 않습니다. AutoCAD에서 PDF 또는 DWFx로 내보내세요" : ".pdf 또는 .dwfx 파일만 올릴 수 있습니다"));
  if (file.size > MAX_UNDERLAY_MB * 1024 * 1024) return Promise.reject(new Error(`참조 파일은 ${MAX_UNDERLAY_MB} MB까지입니다`));
  const body = new FormData();
  body.set("file", file);
  return api<UnderlayFile>(`/projects/${projectId}/underlays`, { method: "POST", body });
}

export const pageVectors = (id: string, page: number) => api<PageVec>(`/underlays/${id}/pages/${page}/vectors`);

export type ImageWindow = { x0: number; y0: number; x1: number; y1: number };
/** A PNG of the page (or of a window of it, page mm) as a bitmap; `hide` = PDF layers switched off. */
export async function pageImage(id: string, page: number, width: number, hide: string[], win?: ImageWindow): Promise<ImageBitmap> {
  const q = new URLSearchParams({ width: String(Math.round(width)) });
  if (hide.length) q.set("hide", hide.join("|"));
  if (win) for (const k of ["x0", "y0", "x1", "y1"] as const) q.set(k, String(win[k]));
  return createImageBitmap(await apiBlob(`/underlays/${id}/pages/${page}/image?${q}`));
}
