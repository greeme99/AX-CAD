"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import CanvasViewport, { type RenderEntity } from "@/components/cad/CanvasViewport";
import FeatureTree from "@/components/cad/FeatureTree";
import ThreeViewport from "@/components/cad/ThreeViewport";
import { btn2, btnDanger, btnPrimary, Err, field, Field, link, Loading, Modal } from "@/components/ui";
import { api, ApiError, can, errText, type Doc, type List, type User } from "@/lib/api";
import { axisLine, booleanCandidates, failedDependent, featureName, OP_SYMBOL, revolveParams, type AxisDraft, type BooleanOp, type Direction, type Feature } from "@/lib/cad/features";
import { distToPaths, hitPaths } from "@/lib/cad/geom";
import type { MeshJson } from "@/lib/cad/mesh";
import type { Extents, Pt } from "@/lib/cad/view";
import { useApi } from "@/lib/useApi";

type RenderData = { extents: Extents; layers: { name: string; visible: boolean }[]; entities: RenderEntity[] };
type SketchKind = "EXTRUDE" | "REVOLVE";
type BoolDraft = { op: BooleanOp; target: string; tool: string };

const PROFILE_TYPES = ["LINE", "ARC", "CIRCLE", "LWPOLYLINE"];
const NOOP = () => {};
const NO_SNAPS = new Set<never>();
const AXIS_Y: AxisDraft = { px: "0", py: "0", dx: "0", dy: "1", angle: "360" };
const num = (n: number) => n.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
const dangling = (e: unknown): Pt[] => {
  const d = e instanceof ApiError && e.code === "GEOM_OPEN_WIRE" ? (e.details as { dangling?: unknown } | undefined)?.dangling : null;
  return Array.isArray(d) ? d.filter((p): p is Pt => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite)) : [];
};
const extrudeOf = (distance: string, direction: Direction) => (Number(distance) > 0 ? { distance: Number(distance), direction } : "거리는 0보다 커야 합니다.");
const booleanOf = (b: BoolDraft) =>
  !b.target || !b.tool ? "대상과 도구 형상을 선택하세요." : b.target === b.tool ? "대상과 도구는 서로 달라야 합니다." : { op: b.op, target_feature_id: Number(b.target), tool_feature_id: Number(b.tool) };
const axisOf = (p: { axis_point: Pt; axis_dir: Pt; angle_deg: number }): AxisDraft => ({ px: String(p.axis_point[0]), py: String(p.axis_point[1]), dx: String(p.axis_dir[0]), dy: String(p.axis_dir[1]), angle: String(p.angle_deg) });

/** API error text; a dependent BOOLEAN failure names that feature (the whole change was rolled back). */
function failText(e: unknown, features: Feature[]) {
  const dep = e instanceof ApiError ? failedDependent(e.details) : null;
  const f = features.find((x) => x.feature_id === dep);
  return f ? `${featureName(f)} 재생성 실패로 변경이 취소되었습니다: ${errText(e)}` : errText(e);
}

function DistanceDir({ distance, direction, onChange }: { distance: string; direction: Direction; onChange: (d: string, dir: Direction) => void }) {
  return (
    <div className="flex gap-3">
      <Field label="거리 (mm)">
        <input type="number" min="0" step="any" value={distance} onChange={(e) => onChange(e.target.value, direction)} className={`${field} font-mono`} />
      </Field>
      <Field label="방향">
        <select value={direction} onChange={(e) => onChange(distance, e.target.value as Direction)} className={field}>
          <option value="+Z">+Z</option>
          <option value="-Z">−Z</option>
        </select>
      </Field>
    </div>
  );
}

function AxisAngle({ v, onChange }: { v: AxisDraft; onChange: (v: AxisDraft) => void }) {
  const input = (k: keyof AxisDraft, label: string) => (
    <Field label={label}>
      <input type="number" step="any" value={v[k]} onChange={(e) => onChange({ ...v, [k]: e.target.value })} className={`${field} font-mono`} />
    </Field>
  );
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        {input("px", "축 기준점 X (mm)")}
        {input("py", "축 기준점 Y (mm)")}
        {input("dx", "축 방향 X")}
        {input("dy", "축 방향 Y")}
      </div>
      <div className="flex items-end gap-3">
        {input("angle", "각도 (°)")}
        <button type="button" onClick={() => onChange({ ...v, dx: "1", dy: "0" })} className={`${btn2} whitespace-nowrap`}>
          X축
        </button>
        <button type="button" onClick={() => onChange({ ...v, dx: "0", dy: "1" })} className={`${btn2} whitespace-nowrap`}>
          Y축
        </button>
      </div>
    </div>
  );
}

function BooleanFields({ v, candidates, onChange }: { v: BoolDraft; candidates: Feature[]; onChange: (v: BoolDraft) => void }) {
  const pick = (k: "target" | "tool", label: string) => (
    <Field label={label}>
      <select value={v[k]} onChange={(e) => onChange({ ...v, [k]: e.target.value })} className={field}>
        <option value="">선택</option>
        {candidates.map((f) => (
          <option key={f.feature_id} value={f.feature_id}>
            {featureName(f)}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <div className="space-y-3">
      <Field label="연산">
        <select value={v.op} onChange={(e) => onChange({ ...v, op: e.target.value as BooleanOp })} className={field}>
          <option value="FUSE">합집합 (Fuse {OP_SYMBOL.FUSE})</option>
          <option value="CUT">차집합 (Cut {OP_SYMBOL.CUT})</option>
          <option value="COMMON">교집합 (Common {OP_SYMBOL.COMMON})</option>
        </select>
      </Field>
      <div className="flex gap-3">
        {pick("target", "대상 형상")}
        {pick("tool", "도구 형상")}
      </div>
    </div>
  );
}

function Actions({ busy, disabled, onClose }: { busy: boolean; disabled?: boolean; onClose: () => void }) {
  return (
    <div className="flex justify-end gap-2">
      <button type="button" onClick={onClose} className={btn2}>
        취소
      </button>
      <button type="submit" disabled={busy || disabled} aria-busy={busy} className={btnPrimary}>
        {busy ? "생성 중..." : "생성"}
      </button>
    </div>
  );
}

async function createFeature(docId: string, feature_type: Feature["feature_type"], params: object) {
  return api<Feature>(`/documents/${docId}/features`, { method: "POST", json: { feature_type, params } });
}

// FN-10: pick a closed profile on the current revision, then POST the EXTRUDE / REVOLVE feature
function SketchDialog({ kind, docId, revisionId, onClose, onDone }: { kind: SketchKind; docId: string; revisionId: string; onClose: () => void; onDone: (id: number) => void }) {
  const render = useApi<RenderData>(`/revisions/${revisionId}/render`);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [distance, setDistance] = useState("10");
  const [direction, setDirection] = useState<Direction>("+Z");
  const [axis, setAxis] = useState(AXIS_Y);
  const [error, setError] = useState<string | null>(null);
  const [markers, setMarkers] = useState<Pt[]>([]);
  const [busy, setBusy] = useState(false);
  const d = render.data;
  const hidden = useMemo(() => new Set(d?.layers.filter((l) => !l.visible).map((l) => l.name)), [d]);
  const preview = useMemo(() => (kind === "REVOLVE" && d ? [axisLine(axis, d.extents)] : []), [kind, axis, d]);
  const pickable = (e: RenderEntity) => !!e.geom && PROFILE_TYPES.includes(e.geom.type) && !hidden.has(e.layer);

  const onPick = (_p: Pt, _shift: boolean, scale: number, raw: Pt) => {
    let best: string | null = null;
    let bestD = 5 / scale; // 5 px, same as the 2D viewer
    for (const e of d?.entities ?? []) {
      const dist = pickable(e) ? distToPaths(hitPaths(e), raw) : Infinity;
      if (dist <= bestD) [best, bestD] = [e.handle, dist];
    }
    if (!best) return;
    setMarkers([]);
    setPicked((s) => {
      const n = new Set(s);
      if (!n.delete(best)) n.add(best);
      return n;
    });
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!picked.size) return setError("프로파일 엔티티를 하나 이상 선택하세요.");
    const p = kind === "EXTRUDE" ? extrudeOf(distance, direction) : revolveParams(axis);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    setMarkers([]);
    try {
      const f = await createFeature(docId, kind, { source_revision_id: revisionId, handles: [...picked], ...p });
      onDone(f.feature_id);
    } catch (err) {
      setError(errText(err));
      setMarkers(dangling(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={kind === "EXTRUDE" ? "Extrude" : "Revolve"} onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted-foreground">
          폐곡선을 이루는 엔티티를 클릭해 선택하세요. (선, 호, 원, 폴리라인) 선택됨: {picked.size}개{kind === "REVOLVE" && " · 회전축은 파란 선으로 표시되며 프로파일을 가로지르면 안 됩니다."}
        </p>
        <div className="h-80 overflow-hidden rounded-md border border-line">
          {d ? (
            <CanvasViewport entities={d.entities} extents={d.extents} hidden={hidden} onCursor={NOOP} onZoom={NOOP} selected={picked} preview={preview} onPick={onPick} snapOn={false} snapKinds={NO_SNAPS} gridStep={10} showGrid={false} markers={markers} />
          ) : (
            <div className="p-3">{render.error ? <Err text={errText(render.error)} /> : <Loading />}</div>
          )}
        </div>
        {kind === "EXTRUDE" ? <DistanceDir distance={distance} direction={direction} onChange={(a, b) => (setDistance(a), setDirection(b))} /> : <AxisAngle v={axis} onChange={setAxis} />}
        <Err text={error} />
        <Actions busy={busy} disabled={!d} onClose={onClose} />
      </form>
    </Modal>
  );
}

// FN-11: combine two visible bodies; the inputs become hidden (consumed)
function BooleanDialog({ docId, features, onClose, onDone }: { docId: string; features: Feature[]; onClose: () => void; onDone: (id: number) => void }) {
  const candidates = booleanCandidates(features);
  const [v, setV] = useState<BoolDraft>({ op: "CUT", target: String(candidates[0]?.feature_id ?? ""), tool: String(candidates[1]?.feature_id ?? "") });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = booleanOf(v);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      onDone((await createFeature(docId, "BOOLEAN", p)).feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Boolean" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted-foreground">대상에서 도구 형상으로 연산합니다. 입력 형상은 결과에 포함되어 화면에서 숨겨집니다.</p>
        <BooleanFields v={v} candidates={candidates} onChange={setV} />
        <Err text={error} />
        <Actions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

function Properties({ f, features, canEdit, onChanged, onDeleted }: { f: Feature; features: Feature[]; canEdit: boolean; onChanged: () => void; onDeleted: () => void }) {
  const [distance, setDistance] = useState(f.feature_type === "EXTRUDE" ? String(f.params.distance) : "");
  const [direction, setDirection] = useState<Direction>(f.feature_type === "EXTRUDE" ? f.params.direction : "+Z");
  const [axis, setAxis] = useState(f.feature_type === "REVOLVE" ? axisOf(f.params) : AXIS_Y);
  const [bool, setBool] = useState<BoolDraft>(f.feature_type === "BOOLEAN" ? { op: f.params.op, target: String(f.params.target_feature_id), tool: String(f.params.tool_feature_id) } : { op: "CUT", target: "", tool: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  async function run(fn: () => Promise<unknown>, done: () => void) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      done();
    } catch (e) {
      setError(failText(e, features));
      onChanged(); // a failed regenerate may still flip the feature to ERROR
    } finally {
      setBusy(false);
    }
  }
  const regen = () => {
    const p = f.feature_type === "EXTRUDE" ? extrudeOf(distance, direction) : f.feature_type === "REVOLVE" ? revolveParams(axis) : booleanOf(bool);
    if (typeof p === "string") return setError(p);
    void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: { ...f.params, ...p } } }), onChanged);
  };

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-foreground">{featureName(f)}</h2>
      {f.status === "ERROR" && <Err text={`⚠ ${f.error_code ? errText(new ApiError(f.error_code, f.error_code)) : "재생성 오류"}`} />}
      {!f.visible && <p className="text-sm text-muted-foreground">Boolean의 입력으로 사용 중이어서 화면에 표시되지 않습니다.</p>}
      <fieldset disabled={!canEdit || busy} className="space-y-3">
        {f.feature_type === "EXTRUDE" && <DistanceDir distance={distance} direction={direction} onChange={(a, b) => (setDistance(a), setDirection(b))} />}
        {f.feature_type === "REVOLVE" && <AxisAngle v={axis} onChange={setAxis} />}
        {f.feature_type === "BOOLEAN" && <BooleanFields v={bool} candidates={booleanCandidates(features, f)} onChange={setBool} />}
        {canEdit && (
          <div className="flex gap-2">
            <button type="button" onClick={regen} className={btnPrimary}>
              재생성
            </button>
            <button type="button" onClick={() => setConfirm(true)} className={btn2}>
              삭제
            </button>
          </div>
        )}
      </fieldset>
      <Err text={error} />
      {confirm && (
        <Modal title="Feature 삭제" onClose={() => setConfirm(false)}>
          <p className="mb-4 text-sm">
            {featureName(f)}을(를) 삭제할까요?{f.feature_type === "BOOLEAN" && " 입력 형상이 다시 표시됩니다."}
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setConfirm(false)} className={btn2}>
              취소
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirm(false);
                void run(() => api(`/features/${f.feature_id}`, { method: "DELETE" }), onDeleted);
              }}
              className={btnDanger}
            >
              삭제
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

const Metric = ({ label, value }: { label: string; value: string }) => (
  <div className="flex justify-between gap-2">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="text-right font-mono text-foreground">{value}</dd>
  </div>
);

function Workbench({ docId }: { docId: string }) {
  const me = useApi<User>("/auth/me").data;
  const doc = useApi<Doc>(`/documents/${encodeURIComponent(docId)}`);
  const feats = useApi<List<Feature>>(`/documents/${encodeURIComponent(docId)}/features`);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dialog, setDialog] = useState<SketchKind | "BOOLEAN" | null>(null);
  const [meshes, setMeshes] = useState<Record<string, MeshJson>>({});
  const [meshError, setMeshError] = useState<string | null>(null);
  const canEdit = !!me && can(me, "DESIGNER");
  const features = useMemo(() => feats.data?.items.toSorted((a, b) => a.seq - b.seq) ?? [], [feats.data]);
  const key = (f: Feature) => `${f.feature_id}:${f.updated_at}`;
  const drawn = (f: Feature) => f.status === "OK" && f.visible; // consumed BOOLEAN inputs live inside the result

  useEffect(() => {
    let live = true;
    for (const f of features) {
      if (!drawn(f) || meshes[key(f)]) continue;
      api<MeshJson>(`/features/${f.feature_id}/mesh`).then(
        (m) => live && setMeshes((s) => ({ ...s, [key(f)]: m })),
        (e) => live && setMeshError(errText(e)),
      );
    }
    return () => {
      live = false;
    };
    // ponytail: meshes cache never pruned and a re-run may refetch in flight ids, key by updated_at keeps it correct; add eviction if sessions get long
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [features]);

  const shown = useMemo(() => features.filter((f) => drawn(f) && meshes[key(f)]).map((f) => ({ id: f.feature_id, mesh: meshes[key(f)] })), [features, meshes]);
  const tris = shown.reduce((s, m) => s + m.mesh.triangle_count, 0);
  const sel = features.find((f) => f.feature_id === selectedId) ?? null;
  const m = sel?.metrics;
  const d = doc.data;
  const revId = d?.current_revision_id;
  const canBoolean = booleanCandidates(features).length >= 2;
  const done = (id: number) => {
    setDialog(null);
    setSelectedId(id);
    feats.reload();
  };

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm">
        <span className="font-semibold text-foreground">AX-CAD</span>
        <Link href={`/documents/${encodeURIComponent(docId)}`} className={link}>
          ← 도면 상세
        </Link>
        {d && (
          <span className="text-foreground">
            <span className="font-mono">{d.doc_no}</span> {d.title}
          </span>
        )}
        <nav aria-label="작업 화면" className="flex gap-1">
          {revId ? (
            <Link href={`/viewer/${revId}?doc=${encodeURIComponent(docId)}`} className="rounded-md px-3 py-1 text-body hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
              Draft
            </Link>
          ) : (
            <span className="px-3 py-1 text-muted-foreground">Draft</span>
          )}
          <span aria-current="page" className="rounded-md bg-[var(--color-primary-light)] px-3 py-1 font-medium text-[var(--color-primary)]">
            Model
          </span>
        </nav>
        {me && !canEdit && <span className="text-muted-foreground">읽기 전용</span>}
      </header>

      <div className="flex min-h-0 flex-1">
        <aside aria-label="Feature" className="w-64 shrink-0 space-y-3 overflow-y-auto border-r border-line bg-card p-3">
          <h2 className="text-sm font-semibold text-foreground">Feature</h2>
          {canEdit && (
            <div className="flex flex-wrap gap-1">
              {(["EXTRUDE", "REVOLVE"] as const).map((k) => (
                <button key={k} type="button" disabled={!revId} title={revId ? undefined : "리비전이 없습니다"} onClick={() => setDialog(k)} className={btn2}>
                  {k === "EXTRUDE" ? "Extrude" : "Revolve"}
                </button>
              ))}
              <button type="button" disabled={!canBoolean} title={canBoolean ? undefined : "표시 중인 형상이 2개 이상 필요합니다"} onClick={() => setDialog("BOOLEAN")} className={btn2}>
                Boolean
              </button>
            </div>
          )}
          <Err text={(feats.error && errText(feats.error)) || (doc.error && errText(doc.error)) || meshError} />
          {!feats.data && !feats.error ? <Loading /> : <FeatureTree features={features} selectedId={selectedId} onSelect={setSelectedId} />}
        </aside>

        <main className="min-w-0 flex-1">
          <ThreeViewport meshes={shown} selectedId={selectedId} onSelect={setSelectedId} />
        </main>

        <aside aria-label="속성" className="w-80 shrink-0 space-y-4 overflow-y-auto border-l border-line bg-card p-3">
          {sel ? (
            <>
              <Properties
                key={key(sel)}
                f={sel}
                features={features}
                canEdit={canEdit}
                onChanged={feats.reload}
                onDeleted={() => {
                  setSelectedId(null);
                  feats.reload();
                }}
              />
              <section aria-label="계측" className="space-y-1 border-t border-line pt-3 text-sm">
                <h3 className="font-semibold text-foreground">계측</h3>
                {m ? (
                  <dl className="space-y-1">
                    <Metric label="부피" value={`${num(m.volume_mm3)} mm³`} />
                    <Metric label="표면적" value={`${num(m.surface_area_mm2)} mm²`} />
                    <Metric label="BBox" value={`${m.bbox.size.map(num).join(" × ")} mm`} />
                  </dl>
                ) : (
                  <p className="text-muted-foreground">계측값이 없습니다.</p>
                )}
              </section>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Feature를 선택하세요.</p>
          )}
        </aside>
      </div>

      <footer className="flex h-8 shrink-0 items-center gap-6 border-t border-line bg-muted px-4 font-mono text-xs text-body">
        <span>선택: {sel ? featureName(sel) : "-"}</span>
        <span>삼각형 {tris.toLocaleString("ko-KR")}</span>
        <span>단위 mm</span>
        <span className="ml-auto font-sans text-muted-foreground">좌 드래그 회전 · 우 드래그 이동 · 휠 확대/축소</span>
      </footer>

      {(dialog === "EXTRUDE" || dialog === "REVOLVE") && revId && <SketchDialog kind={dialog} docId={docId} revisionId={revId} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "BOOLEAN" && <BooleanDialog docId={docId} features={features} onClose={() => setDialog(null)} onDone={done} />}
    </div>
  );
}

export default function ModelPage({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return <Workbench docId={documentId} />;
}
