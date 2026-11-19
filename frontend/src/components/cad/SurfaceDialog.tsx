"use client";

import { useEffect, useRef, useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { createBody, featureName, rampColors, SURF_DEFAULT, SURF_LABEL, surfParams, type Feature, type Sig, type SurfDraft, type SurfKind } from "@/lib/cad/features";

const WITH_SKETCH: SurfKind[] = ["SURF_EXTRUDE", "SURF_REVOLVE", "SURF_SWEEP"];
const ON_BODY: SurfKind[] = ["SURF_OFFSET", "SURF_EXTEND", "SURF_TRIM", "SURF_SPLIT", "SURF_THICKEN", "SURF_REPLACE", "SURF_DELETE_FACE"];
type PickRefs = (bodyId: number, mode: "EDGE" | "FACE", initial: Sig[], done: (refs: Sig[]) => void, cancel: () => void) => void;
const DIST_LABEL: Partial<Record<SurfKind, string>> = { SURF_EXTRUDE: "거리 (mm)", SURF_OFFSET: "오프셋 (mm, 음수 = 반대쪽)", SURF_EXTEND: "연장 거리 (mm, 네 변 모두)", SURF_THICKEN: "두께 (mm, 음수 = 반대쪽)" };
const HELP: Partial<Record<SurfKind, string>> = {
  SURF_BOUNDARY: "변마다 한 줄, 점은 세미콜론으로 구분합니다. 변끼리 끝점이 이어져 닫힌 고리여야 합니다. 예: 0,0,0; 20,0,5; 40,0,0",
  SURF_FILL: "점 입력: 변마다 한 줄, 점은 세미콜론으로 구분하고 변끼리 끝점이 이어져 닫힌 고리여야 합니다. 몸체의 구멍 메우기: 구멍 둘레의 모서리를 3D에서 고르면 둘레 면과 G0/G1/G2로 이어지는 면을 만들어 몸체에 꿰맵니다 (닫히면 솔리드).",
  SURF_DELETE_FACE: "고른 면을 뺀 나머지를 곡면 몸체로 남깁니다. 솔리드는 열린 곡면이 됩니다.",
  SURF_EXTEND: "면이 하나인 곡면만 연장합니다. 평면·원통·원뿔·돌출·회전 곡면은 그대로 이어지고, NURBS 패치와 잘린 면은 지원하지 않습니다.",
  SURF_TRIM: "평면, 스케치(닫힌 윤곽을 스케치 법선 방향으로 투영), 다른 곡면으로 자릅니다. 곡면이 몸체를 완전히 가로질러야 합니다.",
  SURF_SPLIT: "자르기와 같지만 양쪽을 한 몸체의 따로 떨어진 조각으로 남깁니다.",
  SURF_REPLACE: "면 번호는 몸체의 면 순서(0부터)입니다. 3D에서 면을 골라 지정하는 기능은 아직 없습니다.",
};

// plan §23 S6~S7: surface (sheet body) features
export default function SurfaceDialog({ docId, features, onPickRefs, onClose, onDone }: { docId: string; features: Feature[]; onPickRefs: PickRefs; onClose: () => void; onDone: (id: number) => void }) {
  const sketches = features.filter((f) => f.feature_type === "SKETCH" && f.visible);
  const bodies = features.filter((f) => f.feature_type !== "SKETCH" && f.feature_type !== "STANDARD" && f.visible && f.status === "OK");
  const last = (xs: Feature[]) => String(xs[xs.length - 1]?.feature_id ?? "");
  const [d, setD] = useState<SurfDraft>({ ...SURF_DEFAULT, sketch: last(sketches), target: last(bodies) });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false); // the 3D view takes the clicks; the dialog comes back after
  const onBody = d.kind === "SURF_DELETE_FACE" || (d.kind === "SURF_FILL" && d.fillFrom === "body");
  const pickRefs = () => {
    setPicking(true);
    onPickRefs(
      Number(d.target),
      d.kind === "SURF_FILL" ? "EDGE" : "FACE",
      d.refs,
      (refs) => (setPicking(false), setD({ ...d, refs })),
      () => setPicking(false),
    );
  };
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

  if (picking) return null;
  return (
    <Modal title="곡면" onClose={onClose} inline>
      <form onSubmit={submit} className="space-y-3">
        <Field label="작업">
          <select className={field} value={d.kind} onChange={(e) => (setError(null), setD({ ...d, kind: e.target.value as SurfKind, ids: [], refs: [] }))}>
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
        {d.kind === "SURF_FILL" && (
          <Field label="채울 곳">
            <select className={field} value={d.fillFrom} onChange={(e) => setD({ ...d, fillFrom: e.target.value as SurfDraft["fillFrom"], refs: [] })}>
              <option value="points">변의 점 입력 (새 면)</option>
              <option value="body">몸체의 구멍 (모서리 선택)</option>
            </select>
          </Field>
        )}
        {(ON_BODY.includes(d.kind) || onBody) && (
          <Field label="몸체">
            <select className={field} value={d.target} onChange={(e) => setD({ ...d, target: e.target.value, refs: [] })}>
              <option value="">선택</option>
              {bodies.map((f) => (
                <option key={f.feature_id} value={f.feature_id}>
                  {featureName(f)}
                </option>
              ))}
            </select>
          </Field>
        )}
        {onBody && (
          <div className="flex items-center gap-2 text-sm">
            <button type="button" className={btn2} disabled={!d.target} onClick={pickRefs}>
              {d.kind === "SURF_FILL" ? "3D에서 모서리 고르기" : "3D에서 면 고르기"}
            </button>
            <span className="text-muted-foreground">{d.refs.length}개 선택됨</span>
          </div>
        )}
        {d.kind === "SURF_FILL" && d.fillFrom === "body" && (
          <Field label="둘레 면과의 연속성">
            <select className={field} value={d.continuity} onChange={(e) => setD({ ...d, continuity: e.target.value as SurfDraft["continuity"] })}>
              <option value="G0">G0 (닿기만)</option>
              <option value="G1">G1 (접선 연속)</option>
              <option value="G2">G2 (곡률 연속)</option>
            </select>
          </Field>
        )}
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
        {(d.kind === "SURF_BOUNDARY" || (d.kind === "SURF_FILL" && d.fillFrom === "points")) && (
          <Field label="변의 점 (mm)">
            <textarea className={`${field} font-mono`} rows={5} value={d.edges} onChange={text("edges")} placeholder={"0,0,0; 40,0,0\n40,0,0; 40,40,0\n40,40,0; 0,40,0\n0,40,0; 0,0,0"} />
          </Field>
        )}
        {(d.kind === "SURF_TRIM" || d.kind === "SURF_SPLIT") && (
          <>
            <Field label="자르는 도구">
              <select className={field} value={d.cutter} onChange={(e) => setD({ ...d, cutter: e.target.value as SurfDraft["cutter"] })}>
                <option value="plane">평면</option>
                <option value="sketch">스케치 (닫힌 윤곽 투영)</option>
                <option value="tool">곡면</option>
              </select>
            </Field>
            {d.cutter === "plane" && (
              <div className="grid grid-cols-2 gap-2">
                <Field label="평면 위의 점">
                  <input className={field} value={d.origin} onChange={text("origin")} />
                </Field>
                <Field label="평면 법선">
                  <input className={field} value={d.normal} onChange={text("normal")} />
                </Field>
              </div>
            )}
            {d.cutter === "sketch" && pick("스케치 (닫힌 윤곽)", "sketch", sketches)}
            {d.cutter === "tool" && pick("자를 곡면", "tool", bodies)}
            {d.kind === "SURF_TRIM" && flag("keepNegative", d.cutter === "sketch" ? "윤곽 바깥쪽을 남김" : "법선 반대쪽을 남김")}
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
type Draft = { faces: { face_idx: number; draft_min_deg: number; draft_max_deg: number; undercut: boolean }[] };
const CURV = { gauss: "가우스", mean: "평균", k_min: "최소", k_max: "최대" } as const;
const fmt = (n: number) => (Math.abs(n) < 1e-9 ? "0" : Math.abs(n) < 1e-3 ? n.toExponential(2) : n.toLocaleString("ko-KR", { maximumFractionDigits: 4 }));

/** plan §23 S8: quality analysis of the selected body. Curvature goes to the viewport as coloured
 * sample points, zebra stripes are drawn by the viewport from the mesh normals. */
export function SurfaceAnalysis({ featureId, zebra, onZebra, onCloud }: { featureId: number; zebra: boolean; onZebra: (on: boolean) => void; onCloud: (c: Cloud | null) => void }) {
  const [curv, setCurv] = useState<{ data: Curv; key: keyof typeof CURV } | null>(null);
  const [cont, setCont] = useState<Cont | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- on unmount only: invalidate in-flight requests and clear points
  useEffect(() => () => { reqId.current += 1; onCloud(null); }, []);
  const run = <T,>(what: string, json: object, then: (r: T) => void) => {
    const id = ++reqId.current;
    setBusy(true);
    setError(null);
    api<T>(`/features/${featureId}/${what}`, { method: "POST", json }).then(
      (r) => { if (reqId.current === id) { setBusy(false); then(r); } },
      (e) => { if (reqId.current === id) { setBusy(false); setError(errText(e)); } },
    );
  };
  const show = (data: Curv, key: keyof typeof CURV) => {
    setCurv({ data, key });
    onCloud({ positions: new Float32Array(data.samples.flatMap((s) => [s.x, s.y, s.z])), colors: rampColors(data.samples.map((s) => s[key])) });
  };
  let minVal = Infinity;
  let maxVal = -Infinity;
  let hasVal = false;
  if (curv) {
    for (const s of curv.data.samples) {
      const v = s[curv.key];
      if (!Number.isFinite(v)) continue;
      hasVal = true;
      if (v < minVal) minVal = v;
      if (v > maxVal) maxVal = v;
    }
  }
  const ok = (pass: boolean) => (pass ? "text-[var(--color-success-text)]" : "text-red-600");
  return (
    <section aria-label="곡면 분석" className="space-y-2 border-t border-line pt-3 text-sm">
      <h3 className="font-semibold text-foreground">곡면 분석</h3>
      <div className="flex flex-wrap gap-1">
        <button type="button" disabled={busy} aria-busy={busy} className={btn2} onClick={() => run<Curv>("curvature-map", { n_u: 30, n_v: 30 }, (r) => show(r, curv?.key ?? "gauss"))}>
          곡률
        </button>
        <button type="button" disabled={busy} aria-busy={busy} className={btn2} onClick={() => run<Cont>("continuity-check", {}, setCont)}>
          연속성
        </button>
        <button type="button" disabled={busy} aria-busy={busy} className={btn2} onClick={() => run<Draft>("draft-analysis", { pull_direction: [0, 0, 1] }, setDraft)}>
          구배 (+Z)
        </button>
        <button type="button" aria-pressed={zebra} className={`${btn2} ${zebra ? "ring-2 ring-[var(--color-primary)]" : ""}`} onClick={() => onZebra(!zebra)}>
          제브라
        </button>
        {(curv || cont || draft) && (
          <button type="button" className={btn2} onClick={() => (reqId.current += 1, setBusy(false), setCurv(null), setCont(null), setDraft(null), onCloud(null))}>
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
            <span className="text-blue-600">■</span> {hasVal ? fmt(minVal) : "-"} ~ <span className="text-red-600">■</span> {hasVal ? fmt(maxVal) : "-"} (1/mm{curv.key === "gauss" ? "²" : ""})
          </p>
          <p className="text-xs text-muted-foreground">몸체의 모든 면을 면마다 30×30 격자로 계산합니다 (잘려 나간 부분은 건너뜀).</p>
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
      {draft && <p className="text-xs text-muted-foreground">+Z로 뺄 때: 벽 0°, 위를 보는 면 +, 아래를 보는 면 − (언더컷).</p>}
      {draft && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th>면</th>
              <th>구배 최소 (°)</th>
              <th>최대 (°)</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.faces.map((f) => (
              <tr key={f.face_idx}>
                <td>{f.face_idx + 1}</td>
                <td>{fmt(f.draft_min_deg)}</td>
                <td>{fmt(f.draft_max_deg)}</td>
                <td className={f.undercut ? "text-red-600" : "text-muted-foreground"}>{f.undercut ? "언더컷" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
