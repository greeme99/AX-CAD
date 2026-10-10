"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Loading, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Role = "CUT" | "HOLE" | "PUNCH" | "BEND";
type Item = { handle: string; layer: string; role: Role; length_mm?: number; dia_mm?: number };
export type Metrics2D = {
  rules_source: "MASTER" | "DEFAULT";
  master_version_id: number | null;
  cutting_length_mm: number;
  hole_count: number;
  holes_by_dia: Record<string, number>;
  punch_hole_count: number;
  bend_count: number;
  bend_length_mm: number;
  net_area_mm2: number | null;
  bbox: { size: [number, number] } | null;
  title_block: Record<string, string | number | null> & { missing: string[] };
  status: "OK" | "INPUT_REQUIRED";
  items: Item[];
  warnings: string[];
};

const ROLE: Record<Role, string> = { CUT: "절단 윤곽", HOLE: "구멍(레이저)", PUNCH: "구멍(펀칭)", BEND: "절곡선" };
const TITLE: Record<string, string> = { part_no: "품번", part_name: "품명", material: "재질", thickness_mm: "두께(mm)", qty: "수량" };
const WARN: Record<string, string> = {
  METRIC_NO_OUTER_CONTOUR: "최외곽 폐곡선이 없어 면적을 계산하지 못했습니다",
  METRIC_OPEN_CONTOUR: "닫히지 않은 윤곽",
  METRIC_DUPLICATE_GEOMETRY: "겹친 중복 선(1회만 계산)",
  METRIC_MULTIPLE_PARTS: "도면에 여러 부품 윤곽이 있습니다(가장 큰 윤곽 기준)",
  METRIC_ITEMS_TRUNCATED: "추적 항목이 많아 일부만 표시",
};
const n = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
const warnText = (w: string) => {
  const [code, count] = w.split(":");
  return (WARN[code] ?? code) + (count ? ` (${count})` : "");
};

const Row = ({ label, value }: { label: string; value: string }) => (
  <div className="flex justify-between gap-3 border-t border-line py-1">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="text-right font-mono text-foreground">{value}</dd>
  </div>
);

/** FN-14: quote metrics of a revision; "도면에서 보기" selects the source entities (traceability). */
// FN-17: start a quote from these metrics; title-block values are only defaults
function CreateQuote({ revisionId, m }: { revisionId: string; m: Metrics2D }) {
  const router = useRouter();
  const t = m.title_block;
  const [qty, setQty] = useState(t.qty == null ? "" : String(t.qty));
  const [material, setMaterial] = useState(t.material == null ? "" : String(t.material));
  const [thickness, setThickness] = useState(t.thickness_mm == null ? "" : String(t.thickness_mm));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const q = await api<{ quote_id: number }>("/quotes", {
        method: "POST",
        // only edited values are sent: untouched title-block values stay recorded as TITLE_BLOCK
        json: {
          revision_id: revisionId,
          qty: qty && qty !== String(t.qty ?? "") ? Number(qty) : undefined,
          material_code: material && material !== String(t.material ?? "") ? material : undefined,
          thickness_mm: thickness && thickness !== String(t.thickness_mm ?? "") ? thickness : undefined,
        },
      });
      router.push(`/quotes/${q.quote_id}`);
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} aria-label="견적 생성" className="mt-4 space-y-2 border-t border-line pt-3">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="수량">
          <input type="number" min="1" step="1" value={qty} onChange={(e) => setQty(e.target.value)} className={`${field} w-24 font-mono`} />
        </Field>
        <Field label="재질">
          <input value={material} onChange={(e) => setMaterial(e.target.value)} pattern="[A-Za-z0-9._\-]{1,40}" className={`${field} w-28 font-mono`} />
        </Field>
        <Field label="두께(mm)">
          <input type="number" min="0" step="any" value={thickness} onChange={(e) => setThickness(e.target.value)} className={`${field} w-24 font-mono`} />
        </Field>
        <button type="submit" disabled={busy} className={btnPrimary}>
          {busy ? "산출 중..." : "견적 생성"}
        </button>
      </div>
      <Err text={error} />
    </form>
  );
}

export default function MetricsDialog({ revisionId, onClose, onShow, canQuote = false }: { revisionId: string; onClose: () => void; onShow: (handles: string[]) => void; canQuote?: boolean }) {
  const { data: m, error } = useApi<Metrics2D>(`/revisions/${encodeURIComponent(revisionId)}/metrics`);
  const show = (role: Role) => onShow([...new Set(m?.items.filter((i) => i.role === role).map((i) => i.handle))]);
  return (
    <Modal title="견적 메트릭" onClose={onClose} wide>
      {error ? (
        <Err text={errText(error)} />
      ) : !m ? (
        <Loading />
      ) : (
        <div className="grid gap-6 text-sm md:grid-cols-2">
          <section aria-label="형상 메트릭">
            <dl>
              <Row label="절단길이" value={`${n(m.cutting_length_mm)} mm`} />
              <Row label="구멍" value={`${m.hole_count}개${m.punch_hole_count ? ` (펀칭 ${m.punch_hole_count})` : ""}`} />
              {Object.entries(m.holes_by_dia).map(([d, c]) => (
                <Row key={d} label={`  Ø${d}`} value={`${c}개`} />
              ))}
              <Row label="절곡" value={`${m.bend_count}개 · ${n(m.bend_length_mm)} mm`} />
              <Row label="순면적" value={m.net_area_mm2 == null ? "-" : `${n(m.net_area_mm2)} mm²`} />
              <Row label="외곽 크기" value={m.bbox ? `${m.bbox.size.map(n).join(" × ")} mm` : "-"} />
            </dl>
            <div className="mt-3 flex flex-wrap gap-1">
              {(Object.keys(ROLE) as Role[])
                .filter((r) => m.items.some((i) => i.role === r))
                .map((r) => (
                  <button key={r} type="button" onClick={() => show(r)} className={btn2}>
                    {ROLE[r]} 도면에서 보기
                  </button>
                ))}
            </div>
          </section>
          <section aria-label="표제란" className="space-y-3">
            <dl>
              {Object.entries(TITLE).map(([k, label]) => (
                <Row key={k} label={label} value={m.title_block[k] == null ? "미추출" : String(m.title_block[k])} />
              ))}
            </dl>
            {m.status === "INPUT_REQUIRED" && <p className="rounded-md bg-amber-50 px-2 py-1 text-amber-800">⚠ 재질·두께를 도면에서 찾지 못했습니다. 견적 시 직접 입력해야 합니다.</p>}
            {m.warnings.map((w) => (
              <p key={w} className="rounded-md bg-amber-50 px-2 py-1 text-amber-800">
                ⚠ {warnText(w)}
              </p>
            ))}
            <p className="text-xs text-muted-foreground">규칙: {m.rules_source === "MASTER" ? `기준정보 버전 #${m.master_version_id}` : "기본값(기준정보 미활성)"}</p>
          </section>
        </div>
      )}
      {m && canQuote && <CreateQuote revisionId={revisionId} m={m} />}
    </Modal>
  );
}
