// Start drafting in AX-CAD without an outside CAD file: blank KS sheet or domain/company template
// (standard layers, border, title block, BOM/schedules, reusable symbol blocks) — core/dxf/template.py
import { api } from "./api";

export const SHEETS = ["A4", "A3", "A2", "A1", "A0"] as const;
export type Sheet = (typeof SHEETS)[number];

export const TEMPLATES = [
  "COMPANY_INTERNAL",
  "BLANK_KS",
  "MECHANICAL_KS",
  "ARCHITECTURAL_KS",
  "INTERIOR_RENO",
  "MEP_FACILITY",
] as const;
export type TemplateCode = (typeof TEMPLATES)[number];

export const TEMPLATE_LABELS: Record<TemplateCode, string> = {
  COMPANY_INTERNAL: "회사 업무용 (비공식 · 2D/3D 공통)",
  BLANK_KS: "표준 빈 도면 (KS 기본 레이어)",
  MECHANICAL_KS: "기계 · 제조 표준 (제3각법 + BOM + 거칠기)",
  ARCHITECTURAL_KS: "건축 평면도 표준 (방위표 + 창호 + 마감표)",
  INTERIOR_RENO: "인테리어 리노베이션 (철거/마감 + 가구/조명)",
  MEP_FACILITY: "전기 · 배관 · 공조 설비 (범례 + 설비 기호)",
};

/** Creates the first revision from a standard sheet / template and returns its id. */
export async function startBlank(
  documentId: number | string,
  sheet: Sheet = "A3",
  portrait = false,
  template: TemplateCode = "BLANK_KS",
): Promise<string> {
  const r = await api<{ revision_id: string }>(`/documents/${encodeURIComponent(documentId)}/blank`, {
    method: "POST",
    json: { sheet, portrait, template },
  });
  return r.revision_id;
}
