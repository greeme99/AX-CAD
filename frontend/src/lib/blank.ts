// Start drafting in AX-CAD without an outside CAD file: the first revision is a blank drawing
// (standard KS line-type layers, mm, the sheet as drawing limits) — core/dxf/template.py
import { api } from "./api";

export const SHEETS = ["A4", "A3", "A2", "A1", "A0"] as const;
export type Sheet = (typeof SHEETS)[number];

/** Creates the blank first revision and returns its id (409 DOCUMENT_HAS_REVISION otherwise). */
export async function startBlank(documentId: number | string, sheet: Sheet = "A3", portrait = false): Promise<string> {
  const r = await api<{ revision_id: string }>(`/documents/${encodeURIComponent(documentId)}/blank`, { method: "POST", json: { sheet, portrait } });
  return r.revision_id;
}
