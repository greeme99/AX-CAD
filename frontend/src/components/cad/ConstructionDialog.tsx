"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  btn2,
  btnPrimary,
  Err,
  field,
  Field,
  Loading,
  Modal,
} from "@/components/ui";
import { api, errText, qs, warnText, type List } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Room = {
  handles: string[];
  name: string | null;
  area_m2: number;
  perimeter_m: number;
  status: "NEW" | "KEEP" | "DEMO";
};
type Qto = {
  measures: Record<string, number>;
  rooms: Room[];
  items: { role: string; status: string; handles: string[] }[];
  has_base: boolean;
  rules_source: "MASTER" | "DEFAULT";
  warnings: string[];
};
type Rev = {
  revision_id: string;
  revision_no: string;
  created_at: string;
  is_current: boolean;
};
type Factor = {
  factor_type: "REGION" | "ESCALATION";
  code: string;
  factor: string;
  note: string | null;
};

// Track 6-2: what the plan measured, by role (length roles in m, counts in EA)
const MEASURES: [string, string, string][] = [
  ["room_area_m2", "실 면적", "m²"],
  ["wall_length_m", "벽체", "m"],
  ["partition_length_m", "칸막이", "m"],
  ["door_count", "문", "EA"],
  ["window_count", "창호", "EA"],
  ["fixture_count", "기구", "EA"],
  ["duct_length_m", "덕트", "m"],
  ["pipe_length_m", "배관", "m"],
  ["cable_length_m", "전선", "m"],
];
const STATUS = { NEW: "신설", KEEP: "유지", DEMO: "철거" } as const;
const n = (v: number | undefined) =>
  (v ?? 0).toLocaleString("ko-KR", { maximumFractionDigits: 2 });

/** Track 6: construction/MEP quote of a floor plan. A base revision (the existing building) splits
 * every quantity into 철거·신설·유지; "도면에서 보기" selects the measured entities. */
export default function ConstructionDialog({
  revisionId,
  docId,
  onClose,
  onShow,
}: {
  revisionId: string;
  docId: string | null;
  onClose: () => void;
  onShow: (handles: string[]) => void;
}) {
  const router = useRouter();
  const [base, setBase] = useState("");
  const [height, setHeight] = useState("");
  const [region, setRegion] = useState("");
  const [escalation, setEscalation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const revs = useApi<List<Rev>>(
    docId ? `/documents/${encodeURIComponent(docId)}/revisions` : null,
  ).data;
  const factors =
    useApi<{ adjust_factors: Factor[] }>("/master-versions/current").data
      ?.adjust_factors ?? [];
  const { data: q, error: qErr } = useApi<Qto>(
    `/revisions/${encodeURIComponent(revisionId)}/qto${qs({ base_revision_id: base || undefined })}`,
  );
  const show = (role: string) =>
    onShow([
      ...new Set(
        q?.items
          .filter((i) => i.role === role && i.status !== "DEMO")
          .flatMap((i) => i.handles),
      ),
    ]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ quote_id: number }>("/quotes", {
        method: "POST",
        json: {
          kind: "CONSTRUCTION",
          revision_id: revisionId,
          base_revision_id: base || undefined,
          height_mm: height || undefined,
          region: region || undefined,
          escalation: escalation || undefined,
        },
      });
      router.push(`/quotes/${r.quote_id}`);
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }
  const pick = (type: Factor["factor_type"]) =>
    factors.filter((f) => f.factor_type === type);
  return (
    <Modal title="공사 견적 (수량산출)" onClose={onClose} wide>
      <form
        onSubmit={submit}
        aria-label="공사 견적 생성"
        className="space-y-4 text-sm"
      >
        <div className="flex flex-wrap items-end gap-2">
          <Field label="기존 도면(철거·신설 구분)">
            <select
              value={base}
              onChange={(e) => setBase(e.target.value)}
              className={field}
            >
              <option value="">없음 — 전부 신설</option>
              {revs?.items
                .filter((r) => r.revision_id !== revisionId)
                .map((r) => (
                  <option key={r.revision_id} value={r.revision_id}>
                    Rev {r.revision_no} ({r.created_at.slice(0, 10)})
                  </option>
                ))}
            </select>
          </Field>
          <Field label="층고(mm)">
            <input
              type="number"
              min="1"
              max="100000"
              step="any"
              value={height}
              onChange={(e) => setHeight(e.target.value)}
              placeholder="예: 2700"
              className={`${field} w-28 font-mono`}
            />
          </Field>
          <Field label="지역 보정">
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className={field}
            >
              <option value="">없음</option>
              {pick("REGION").map((f) => (
                <option key={f.code} value={f.code}>
                  {f.code} ×{Number(f.factor)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="시점 보정">
            <select
              value={escalation}
              onChange={(e) => setEscalation(e.target.value)}
              className={field}
            >
              <option value="">없음</option>
              {pick("ESCALATION").map((f) => (
                <option key={f.code} value={f.code}>
                  {f.code} ×{Number(f.factor)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {qErr ? (
          <Err text={errText(qErr)} />
        ) : !q ? (
          <Loading />
        ) : (
          <div className="grid gap-6 md:grid-cols-2">
            <section aria-label="수량산출">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <th className="py-1 font-normal">항목</th>
                    <th className="py-1 text-right font-normal">현재</th>
                    {q.has_base &&
                      (["new", "keep", "demo"] as const).map((p) => (
                        <th key={p} className="py-1 text-right font-normal">
                          {STATUS[p.toUpperCase() as keyof typeof STATUS]}
                        </th>
                      ))}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {MEASURES.map(([k, label, unit]) => {
                    const role =
                      k === "room_area_m2"
                        ? "ROOM"
                        : k.replace(/_(length_m|count)$/, "").toUpperCase();
                    const any = q.measures[k] || q.measures[`demo_${k}`];
                    if (!any) return null;
                    return (
                      <tr key={k} className="border-t border-line">
                        <td className="py-1">{label}</td>
                        <td className="py-1 text-right font-mono">
                          {n(q.measures[k])} {unit}
                        </td>
                        {q.has_base &&
                          ["new", "keep", "demo"].map((p) => (
                            <td key={p} className="py-1 text-right font-mono">
                              {n(q.measures[`${p}_${k}`])}
                            </td>
                          ))}
                        <td className="py-1 pl-2">
                          <button
                            type="button"
                            onClick={() => show(role)}
                            className={btn2}
                          >
                            보기
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!MEASURES.some(
                ([k]) => q.measures[k] || q.measures[`demo_${k}`],
              ) && (
                <p className="text-muted-foreground">
                  공사 수량을 찾지 못했습니다. 레이어 이름(ROOM*, WALL*, DOOR*
                  등)이나 기준정보의 공사 수량 매핑을 확인하세요.
                </p>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                레이어 규칙:{" "}
                {q.rules_source === "MASTER" ? "기준정보" : "기본값"}
              </p>
            </section>
            <section aria-label="실별 면적" className="space-y-2">
              <h3 className="font-semibold text-foreground">
                실별 면적 {q.rooms.length}개
              </h3>
              <ul className="max-h-48 overflow-y-auto">
                {q.rooms.map((r, i) => (
                  <li
                    key={i}
                    className="flex justify-between gap-2 border-t border-line py-1"
                  >
                    <span>
                      {r.name ?? "(이름 없음)"}{" "}
                      {q.has_base && (
                        <span className="text-xs text-muted-foreground">
                          {STATUS[r.status]}
                        </span>
                      )}
                    </span>
                    <span className="font-mono">{n(r.area_m2)} m²</span>
                  </li>
                ))}
              </ul>
              {q.warnings.map((w) => (
                <p
                  key={w}
                  className="rounded-md bg-amber-50 px-2 py-1 text-amber-800"
                >
                  ⚠ {warnText(w)}
                </p>
              ))}
            </section>
          </div>
        )}
        <div className="flex items-center gap-2">
          <button type="submit" disabled={busy || !q} className={btnPrimary}>
            {busy ? "산출 중..." : "공사 견적 생성"}
          </button>
          <span className="text-xs text-muted-foreground">
            벽 면적 등 높이가 필요한 공종은 층고를 입력해야 산출됩니다.
          </span>
        </div>
        <Err text={error} />
      </form>
    </Modal>
  );
}
