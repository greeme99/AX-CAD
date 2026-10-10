"use client";

import { btn2, Err, Loading, Modal } from "@/components/ui";
import { errText } from "@/lib/api";
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
export default function MetricsDialog({ revisionId, onClose, onShow }: { revisionId: string; onClose: () => void; onShow: (handles: string[]) => void }) {
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
    </Modal>
  );
}
