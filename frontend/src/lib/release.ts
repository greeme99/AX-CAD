/** Track 8 P5: installed release and update outcome (GET /api/system/release). */
export type Grade = "UPDATE" | "UPGRADE";
export type UpdateResult = { status: "RUNNING" | "OK" | "REFUSED" | "ROLLED_BACK"; from?: string; to?: string; message?: string };
export type Release = {
  installed: boolean;
  version: string | null;
  label: string | null;
  grade?: Grade | null;
  notes: string[];
  released_at?: string | null;
  last_update?: UpdateResult | null;
  can_update?: boolean;
};

export const GRADE: Record<Grade, string> = { UPDATE: "업데이트", UPGRADE: "업그레이드" };

/** The last update's outcome as one line (nothing while none ran or it is still running). */
export function outcome(r: UpdateResult | null | undefined): { ok: boolean; text: string } | null {
  if (!r || r.status === "RUNNING") return null;
  if (r.status === "OK") return { ok: true, text: `업데이트 완료: ${r.from} → ${r.to}` };
  return { ok: false, text: r.message ?? "업데이트 실패" };
}
