// FN-23 BOM types (S12)
export type BomItem = {
  bom_item_id: number;
  item_no: number;
  source_name: string;
  part_no: string | null;
  part_name: string;
  qty: number;
  unit: string;
  level: number;
  mapping_status: "AUTO" | "MANUAL" | "UNMAPPED";
  source_refs: string[];
};
export type Bom = {
  bom_id: number;
  bom_no: string;
  document_id: number;
  revision_id: string | null;
  source_type: "DXF_BLOCK" | "STEP_ASSEMBLY";
  warnings: string[];
  created_at: string;
  items: BomItem[];
  unmapped: number;
};
export type BomSummary = Pick<Bom, "bom_id" | "bom_no" | "revision_id" | "source_type" | "created_at">;

/** Same rule as the server (core/bom/dxf.PART_NO, applied after trim): starts with a letter or
 * digit (Korean included), then letters/digits . - / _ space, 64 chars max. */
export const PART_NO_RE = /^[\p{L}\p{N}_][\p{L}\p{N}_.\-/ ]{0,63}$/u;

// FN-24 ERP transfer job (S12-b)
export type Job = {
  job_id: number;
  idempotency_key: string;
  project_id: number;
  bom_id: number;
  status: "PENDING" | "RUNNING" | "SUCCESS" | "FAILED";
  attempt_count: number;
  response_payload: { status: number; body?: string } | null; // body: ADMIN only
  last_error: string | null;
  created_at: string;
  updated_at: string;
  duplicate: boolean;
};
