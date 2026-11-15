"use client";

import { useEffect, useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { createBody, featureName, rampColors, SURF_DEFAULT, SURF_LABEL, surfParams, type Feature, type SurfDraft, type SurfKind } from "@/lib/cad/features";

const WITH_SKETCH: SurfKind[] = ["SURF_EXTRUDE", "SURF_REVOLVE", "SURF_SWEEP"];
const ON_BODY: SurfKind[] = ["SURF_OFFSET", "SURF_EXTEND", "SURF_TRIM", "SURF_SPLIT", "SURF_THICKEN", "SURF_REPLACE"];
const DIST_LABEL: Partial<Record<SurfKind, string>> = { SURF_EXTRUDE: "거리 (mm)", SURF_OFFSET: "오프셋 (mm, 음수 = 반대쪽)", SURF_EXTEND: "연장 거리 (mm, 네 변 모두)", SURF_THICKEN: "두께 (mm, 음수 = 반대쪽)" };
const HELP: Partial<Record<SurfKind, string>> = {
  SURF_BOUNDARY: "변마다 한 줄, 점은 세미콜론으로 구분합니다. 변끼리 끝점이 이어져 닫힌 고리여야 합니다. 예: 0,0,0; 20,0,5; 40,0,0",
  SURF_FILL: "변마다 한 줄, 점은 세미콜론으로 구분합니다. 변끼리 끝점이 이어져 닫힌 고리여야 합니다. 인접 면과의 G1/G2 연속은 아직 지원하지 않습니다.",
  SURF_EXTEND: "면이 하나인 곡면만 연장합니다. 평면·원통·원뿔·돌출·회전 곡면은 그대로 이어지고, NURBS 패치와 잘린 면은 지원하지 않습니다.",
  SURF_TRIM: "곡면이나 스케치로 자르기는 아직 지원하지 않습니다 (평면만).",
  SURF_REPLACE: "면 번호는 몸체의 면 순서(0부터)입니다. 3D에서 면을 골라 지정하는 기능은 아직 없습니다.",
};

// plan §23 S6~S7: surface (sheet body) features
export default function SurfaceDialog({ docId, features, onClose, onDone }: { docId: string; features: Feature[]; onClose: () => void; onDone: (id: number) => void }) {
  const sketches = features.filter((f) => f.feature_type === "SKETCH" && f.visible);
  const bodies = features.filter((f) => f.feature_type !== "SKETCH" && f.feature_type !== "STANDARD" && f.visible && f.status === "OK");
  const last = (xs: Feature[]) => String(xs[xs.length - 1]?.feature_id ?? "");
  const [d, setD] = useState<SurfDraft>({ ...SURF_DEFAULT, sketch: last(sketches), target: last(bodies) });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const text = (k: "distance" | "edges" | "origin" | "normal" | "faceIndex" | "sketch" | "sketch2" | "target" | "tool") => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setD({ ...d, [k]: e.target.value });
  const flag = (k: "down" | "ruled" | "solid" | "keepNegative", label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={d[k]} onChange={(e) => setD({ ...d, [k]: e.target.checked })} />
      {label}
    </label>
  );
  const pick = (label: string, k: "sketch" | "sketch2" | "target" | "tool", from: Feature[]) => (
    <Field label={label}>
      <select className={field} value={d[k]} onChange={text(k)}>
        <option value="">선택</option>
        {from.map((f) => (
          <option key={f.feature_id} value={f.feature_id}>
            {featureName(f)}
          </option>
        ))}
      </select>
    </Field>
  );
  const many = d.kind === "SURF_LOFT" ? sketches : d.kind === "SURF_KNIT" ? bodies : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = surfParams(d);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      onDone((await api<Feature>(`/documents/${encodeURIComponent(docId)}/features`, { method: "POST", json: createBody(d.kind, p) })).feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="곡면" onClose={onClose} inline>
      <form onSubmit={submit} className="space-y-3">
        <Field label="작업">
          <select className={field} value={d.kind} onChange={(e) => (setError(null), setD({ ...d, kind: e.target.value as SurfKind, ids: [] }))}>
            {(Object.keys(SURF_LABEL) as SurfKind[]).map((k) => (
              <option key={k} value={k}>
                {SURF_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
        {HELP[d.kind] && <p className="text-sm text-muted-foreground">{HELP[d.kind]}</p>}
        {WITH_SKETCH.includes(d.kind) && pick(d.kind === "SURF_SWEEP" ? "단면 스케치" : "스케치 (열린 윤곽)", "sketch", sketches)}
        {d.kind === "SURF_SWEEP" && pick("경로 스케치", "sketch2", sketches)}
        {ON_BODY.includes(d.kind) && pick("몸체", "target", bodies)}
        {d.kind === "SURF_REPLACE" && pick("새 면 (곡면 몸체)", "tool", bodies)}
        {many && (
          <fieldset className="space-y-1">
            <legend className="text-sm font-medium text-foreground">{d.kind === "SURF_LOFT" ? "스케치 (고른 순서대로 잇습니다)" : "꿰맬 곡면"}</legend>
            {many.map((f) => (
              <label key={f.feature_id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={d.ids.includes(f.feature_id)} onChange={(e) => setD({ ...d, ids: e.target.checked ? [...d.ids, f.feature_id] : d.ids.filter((x) => x !== f.feature_id) })} />
                {featureName(f)}
                {d.ids.includes(f.feature_id) && <span className="text-xs text-muted-foreground">{d.ids.indexOf(f.feature_id) + 1}번째</span>}
              </label>
            ))}
            {!many.length && <p className="text-sm text-muted-foreground">고를 대상이 없습니다.</p>}
          </fieldset>
        )}
        {DIST_LABEL[d.kind] && (
          <Field label={DIST_LABEL[d.kind]!}>
            <input className={field} inputMode="decimal" value={d.distance} onChange={text("distance")} />
          </Field>
        )}
        {d.kind === "SURF_EXTRUDE" && flag("down", "반대 방향")}
        {d.kind === "SURF_REVOLVE" && (
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                ["px", "축 위의 점 X"],
                ["py", "축 위의 점 Y"],
                ["dx", "축 방향 X"],
                ["dy", "축 방향 Y"],
                ["angle", "각도 (°)"],
              ] as const
            ).map(([k, label]) => (
              <Field key={k} label={label}>
                <input className={field} inputMode="decimal" value={d.axis[k]} onChange={(e) => setD({ ...d, axis: { ...d.axis, [k]: e.target.value } })} />
              </Field>
            ))}
          </div>
        )}
        {d.kind === "SURF_LOFT" && (
          <>
            {flag("ruled", "직선으로 잇기 (ruled)")}
            {flag("solid", "닫힌 단면이면 솔리드로")}
          </>
        )}
        {(d.kind === "SURF_BOUNDARY" || d.kind === "SURF_FILL") && (
          <Field label="변의 점 (mm)">
            <textarea className={`${field} font-mono`} rows={5} value={d.edges} onChange={text("edges")} placeholder={"0,0,0; 40,0,0\n40,0,0; 40,40,0\n40,40,0; 0,40,0\n0,40,0; 0,0,0"} />
          </Field>
        )}
        {(d.kind === "SURF_TRIM" || d.kind === "SURF_SPLIT") && (
          <>
            <div className="grid grid-cols-2 gap-2">
              <Field label="평면 위의 점">
                <input className={field} value={d.origin} onChange={text("origin")} />
              </Field>
              <Field label="평면 법선">
                <input className={field} value={d.normal} onChange={text("normal")} />
              </Field>
            </div>
            {d.kind === "SURF_TRIM" && flag("keepNegative", "법선 반대쪽을 남김")}
          </>
        )}
        {d.kind === "SURF_REPLACE" && (
          <Field label="바꿀 면 번호">
            <input className={field} inputMode="numeric" value={d.faceIndex} onChange={text("faceIndex")} />
          </Field>
        )}
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="submit" disabled={busy} className={btnPrimary}>
            {busy ? "만드는 중..." : "만들기"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export type Cloud = { positions: Float32Array; colors: Float32Array };
type Sample = { x: number; y: number; z: number; gauss: number; mean: number; k_min: number; k_max: number };
type Curv = { samples: Sample[] };
type Cont = { edges: { edge_idx: number; g0_max_mm: number; g1_max_deg: number; g2_max_pct: number; pass_g0: boolean; pass_g1: boolean; pass_g2: boolean }[]; summary: { total_edges: number } };
type Draft = { faces: { face_idx: number; draft_min_deg: number; draft_max_deg: number }[] };
const CURV = { gauss: "가우스", mean: "평균", k_min: "최소", k_max: "최대" } as const;
const fmt = (n: number) => (Math.abs(n) < 1e-9 ? "0" : Math.abs(n) < 1e-3 ? n.toExponential(2) : n.toLocaleString("ko-KR", { maximumFractionDigits: 4 }));

/** plan §23 S8: quality analysis of the selected body. Curvature goes to the viewport as coloured
 * sample points, zebra stripes are drawn by the viewport from the mesh normals. */
export function SurfaceAnalysis({ featureId, zebra, onZebra, onCloud }: { featureId: number; zebra: boolean; onZebra: (on: boolean) => void; onCloud: (c: Cloud | null) => void }) {
  const [curv, setCurv] = useState<{ data: Curv; key: keyof typeof CURV } | null>(null);
  const [cont, setCont] = useState<Cont | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- on unmount only: the points belong to this body
  useEffect(() => () => onCloud(null), []);
  const run = <T,>(what: string, json: object, then: (r: T) => void) => {
    setError(null);
    api<T>(`/features/${featureId}/${what}`, { method: "POST", json }).then(then, (e) => setError(errText(e)));
  };
  const show = (data: Curv, key: keyof typeof CURV) => {
    setCurv({ data, key });
    onCloud({ positions: new Float32Array(data.samples.flatMap((s) => [s.x, s.y, s.z])), colors: rampColors(data.samples.map((s) => s[key])) });
  };
  const values = curv?.data.samples.map((s) => s[curv.key]).filter(Number.isFinite) ?? [];
  const ok = (pass: boolean) => (pass ? "text-[var(--color-success-text)]" : "text-red-600");
  return (
    <section aria-label="곡면 분석" className="space-y-2 border-t border-line pt-3 text-sm">
      <h3 className="font-semibold text-foreground">곡면 분석</h3>
      <div className="flex flex-wrap gap-1">
        <button type="button" className={btn2} onClick={() => run<Curv>("curvature-map", { n_u: 30, n_v: 30 }, (r) => show(r, curv?.key ?? "gauss"))}>
          곡률
        </button>
        <button type="button" className={btn2} onClick={() => run<Cont>("continuity-check", {}, setCont)}>
          연속성
        </button>
        <button type="button" className={btn2} onClick={() => run<Draft>("draft-analysis", { pull_direction: [0, 0, 1] }, setDraft)}>
          구배 (+Z)
        </button>
        <button type="button" aria-pressed={zebra} className={`${btn2} ${zebra ? "ring-2 ring-[var(--color-primary)]" : ""}`} onClick={() => onZebra(!zebra)}>
          제브라
        </button>
        {(curv || cont || draft) && (
          <button type="button" className={btn2} onClick={() => (setCurv(null), setCont(null), setDraft(null), onCloud(null))}>
            지우기
          </button>
        )}
      </div>
      <Err text={error} />
      {curv && (
        <div className="space-y-1">
          <select aria-label="곡률 종류" className={field} value={curv.key} onChange={(e) => show(curv.data, e.target.value as keyof typeof CURV)}>
            {(Object.keys(CURV) as (keyof typeof CURV)[]).map((k) => (
              <option key={k} value={k}>
                {CURV[k]} 곡률
              </option>
            ))}
          </select>
          <p className="text-body">
            <span className="text-blue-600">■</span> {values.length ? fmt(Math.min(...values)) : "-"} ~ <span className="text-red-600">■</span> {values.length ? fmt(Math.max(...values)) : "-"} (1/mm{curv.key === "gauss" ? "²" : ""})
          </p>
          <p className="text-xs text-muted-foreground">몸체의 첫 번째 면만 계산합니다.</p>
        </div>
      )}
      {cont &&
        (cont.summary.total_edges ? (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th>모서리</th>
                <th>G0 (mm)</th>
                <th>G1 (°)</th>
                <th>G2 (%)</th>
              </tr>
            </thead>
            <tbody>
              {cont.edges.map((e) => (
                <tr key={e.edge_idx}>
                  <td>{e.edge_idx + 1}</td>
                  <td className={ok(e.pass_g0)}>{fmt(e.g0_max_mm)}</td>
                  <td className={ok(e.pass_g1)}>{fmt(e.g1_max_deg)}</td>
                  <td className={ok(e.pass_g2)}>{fmt(e.g2_max_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-muted-foreground">면이 맞닿는 모서리가 없습니다.</p>
        ))}
      {draft && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th>면</th>
              <th>구배 최소 (°)</th>
              <th>최대 (°)</th>
            </tr>
          </thead>
          <tbody>
            {draft.faces.map((f) => (
              <tr key={f.face_idx}>
                <td>{f.face_idx + 1}</td>
                <td>{fmt(f.draft_min_deg)}</td>
                <td>{fmt(f.draft_max_deg)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
