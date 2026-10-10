// FN-17..20 quote view logic, kept free of React for tests. Money arrives as decimal strings.
export type Trace = {
  source_kind: "ENTITY" | "FEATURE" | "REVISION";
  sources: string[];
  source_count: number;
  revision_id: string | null;
  rule_code: string;
  price_item_code: string | null;
  unit_price: string | null;
  inputs: Record<string, unknown>;
  formula_text: string;
};
export type QuoteLine = {
  quote_line_id: number;
  line_no: number;
  cost_category: "MATERIAL" | "LABOR" | "OVERHEAD" | "OUTSOURCE";
  item_code: string;
  item_name: string;
  unit: string;
  calculated_qty: string;
  calculated_unit_price: string | null;
  calculated_amount: string | null;
  excluded: boolean;
  override_qty: string | null;
  override_unit_price: string | null;
  override_amount: string | null;
  override_reason: string | null;
  overridden_by: number | null;
  effective_amount: string | null;
  traces: Trace[];
};
export const TOTAL_KEYS = ["material_cost", "labor_cost", "overhead_cost", "outsource_cost", "manufacturing_cost", "admin_cost", "total_cost", "profit", "supply_amount", "vat_amount", "total_amount"] as const;
export type Totals = Record<(typeof TOTAL_KEYS)[number], string>;
export type Log = { severity: "INFO" | "WARN" | "ERROR"; code: string; message: string; line_no?: number | null };
export type Quote = Totals & {
  quote_id: number;
  quote_no: string;
  project_id: number;
  source_kind: "REVISION" | "DOCUMENT_3D";
  revision_id: string | null;
  document_id: number;
  master_version_id: number;
  created_by: number;
  status: "DRAFT" | "IN_REVIEW" | "CONFIRMED" | "SUPERSEDED" | "ABANDONED";
  revision_no: number;
  parent_quote_id: number | null;
  next_quote_id: number | null;
  change_note: string | null;
  has_errors: boolean;
  inputs: { qty: number; material_code: string | null; thickness_mm: string | null; input_source?: Record<string, string | null> };
  created_at: string;
  lines: QuoteLine[];
  effective: Totals | null;
  logs: Log[];
  approvals: QuoteApproval[];
  authors: number[]; // creator + everyone who ever adjusted a line: cannot approve (FN-22)
};
export type QuoteApproval = {
  approval_id: number;
  quote_id: number;
  requested_by: number;
  approver_id: number;
  status: "PENDING" | "APPROVED" | "REJECTED" | "CANCELLED";
  comment: string | null;
  decision_comment: string | null;
  decided_by: number | null;
  created_at: string;
  decided_at: string | null;
  quote_no: string;
  project_id: number;
  document_id: number;
  total_amount?: string | null; // inbox listing only: effective total, overrides included
  quote_status: Quote["status"];
  requested_by_name: string;
  approver_name: string;
};

export const CATEGORY: Record<QuoteLine["cost_category"], string> = { MATERIAL: "재료비", LABOR: "노무비", OVERHEAD: "제조간접비", OUTSOURCE: "외주가공비" };
export const TOTAL_LABEL: Record<(typeof TOTAL_KEYS)[number], string> = {
  material_cost: "직접재료비",
  labor_cost: "직접노무비",
  overhead_cost: "제조간접비",
  outsource_cost: "외주가공비",
  manufacturing_cost: "순제조원가",
  admin_cost: "일반관리비",
  total_cost: "총원가",
  profit: "이윤",
  supply_amount: "공급가액",
  vat_amount: "세액(VAT)",
  total_amount: "합계",
};

/** Line status badge (SCR-09): manual beats error, error = no usable amount. */
export function lineStatus(l: QuoteLine): "MANUAL" | "ERR" | "AUTO" {
  if (l.override_amount != null) return "MANUAL";
  if (l.effective_amount == null) return "ERR";
  return "AUTO";
}

/** "1234567.8" -> "1,234,568" (won), "-" for null. Decimal strings, no float math on the value itself. */
export function won(v: string | null | undefined, digits = 0): string {
  if (v == null || v === "") return "-";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: 0 }) : v;
}

/** FN-18 "도면에서 보기": the viewer (2D) or model workbench (3D) with the trace sources selected. */
export function sourceLink(t: Trace, documentId: number): string | null {
  if (!Number.isSafeInteger(documentId) || documentId < 1) return null;
  const sel = encodeURIComponent(t.sources.join(","));
  if (t.source_kind === "FEATURE") return `/model/${documentId}?select=${sel}`;
  if (!t.revision_id || !/^[0-9a-f]{32}$/.test(t.revision_id)) return null;
  const base = `/viewer/${t.revision_id}?doc=${documentId}`;
  return t.source_kind === "ENTITY" ? `${base}&select=${sel}` : base;
}

/** ?select=a,b -> handles (hex) or feature ids; anything else is dropped (URL is user-editable). */
export function parseSelect(raw: string | null, kind: "handle" | "feature"): string[] {
  const re = kind === "handle" ? /^[0-9A-Fa-f]{1,16}$/ : /^[1-9][0-9]{0,17}$/;
  return (raw ?? "").split(",").filter((s) => re.test(s)).slice(0, 5000);
}

/** FN-22: project reviewers who did not produce the numbers (server `authors` + the requester). */
export function approverCandidates<M extends { user_id: number; roles: string[] }>(members: M[], q: Pick<Quote, "authors">, meId: number): M[] {
  const authors = new Set([meId, ...q.authors]);
  return members.filter((m) => m.roles.includes("REVIEWER") && !authors.has(m.user_id));
}
