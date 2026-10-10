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

/** Same rule as the server (routes_bom.PART_NO): letters/digits incl. Korean, . - / _ space. */
export const PART_NO_RE = /^[\p{L}\p{N}_.\-/ ]{1,64}$/u;
