"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import CanvasViewport, { type RenderEntity } from "@/components/cad/CanvasViewport";
import FeatureTree, { booleanCandidates, featureName, type BooleanOp, type Direction, type Feature } from "@/components/cad/FeatureTree";
import ThreeViewport from "@/components/cad/ThreeViewport";
import { btn2, btnDanger, btnPrimary, Err, field, Field, link, Loading, Modal } from "@/components/ui";
import { api, ApiError, can, errText, type Doc, type List, type User } from "@/lib/api";
import { distToPaths, hitPaths } from "@/lib/cad/geom";
import type { MeshJson } from "@/lib/cad/mesh";
import type { Extents, Pt } from "@/lib/cad/view";
import { useApi } from "@/lib/useApi";

type RenderData = { extents: Extents; layers: { name: string; visible: boolean }[]; entities: RenderEntity[] };

const PROFILE_TYPES = ["LINE", "ARC", "CIRCLE", "LWPOLYLINE"];
const NOOP = () => {};
const NO_SNAPS = new Set<never>();
const num = (n: number) => n.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
const dangling = (e: unknown): Pt[] => {
  const d = e instanceof ApiError && e.code === "GEOM_OPEN_WIRE" ? (e.details as { dangling?: unknown } | undefined)?.dangling : null;
  return Array.isArray(d) ? d.filter((p): p is Pt => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite)) : [];
};

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

type RevolveFormState = { ax: string; ay: string; dx: string; dy: string; angle: string };
function RevolveFields({ state, onChange }: { state: RevolveFormState; onChange: (next: RevolveFormState) => void }) {
  const set = (k: keyof RevolveFormState) => (e: React.ChangeEvent<HTMLInputElement>) => onChange({ ...state, [k]: e.target.value });
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="축 원점 X (mm)">
          <input type="number" step="any" value={state.ax} onChange={set("ax")} className={`${field} font-mono`} />
        </Field>
        <Field label="축 원점 Y (mm)">
          <input type="number" step="any" value={state.ay} onChange={set("ay")} className={`${field} font-mono`} />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="축 방향 dX">
          <input type="number" step="any" value={state.dx} onChange={set("dx")} className={`${field} font-mono`} />
        </Field>
        <Field label="축 방향 dY">
          <input type="number" step="any" value={state.dy} onChange={set("dy")} className={`${field} font-mono`} />
        </Field>
        <Field label="각도 (0~360°)">
          <input type="number" min="0" max="360" step="any" value={state.angle} onChange={set("angle")} className={`${field} font-mono`} />
        </Field>
      </div>
    </div>
  );
}

function BooleanFields({
  op,
  targetId,
  toolId,
  candidates,
  onChange,
}: {
  op: BooleanOp;
  targetId: string;
  toolId: string;
  candidates: Feature[];
  onChange: (op: BooleanOp, targetId: string, toolId: string) => void;
}) {
  return (
    <div className="space-y-3">
      <Field label="연산 종류">
        <select value={op} onChange={(e) => onChange(e.target.value as BooleanOp, targetId, toolId)} className={field}>
          <option value="FUSE">Fuse (합집합)</option>
          <option value="CUT">Cut (차집합)</option>
          <option value="COMMON">Common (교집합)</option>
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="대상 형상 (Target)">
          <select value={targetId} onChange={(e) => onChange(op, e.target.value, toolId)} className={field}>
            {candidates.map((c) => (
              <option key={c.feature_id} value={String(c.feature_id)}>
                {featureName(c)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="도구 형상 (Tool)">
          <select value={toolId} onChange={(e) => onChange(op, targetId, e.target.value)} className={field}>
            {candidates.map((c) => (
              <option key={c.feature_id} value={String(c.feature_id)}>
                {featureName(c)}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </div>
  );
}

function ProfilePicker({
  revisionId,
  picked,
  markers,
  onToggle,
}: {
  revisionId: string;
  picked: Set<string>;
  markers: Pt[];
  onToggle: (handle: string) => void;
}) {
  const render = useApi<RenderData>(`/revisions/${revisionId}/render`);
  const d = render.data;
  const hidden = useMemo(() => new Set(d?.layers.filter((l) => !l.visible).map((l) => l.name)), [d]);
  const pickable = (e: RenderEntity) => !!e.geom && PROFILE_TYPES.includes(e.geom.type) && !hidden.has(e.layer);

  const onPick = (_p: Pt, _shift: boolean, scale: number, raw: Pt) => {
    let best: string | null = null;
    let bestD = 5 / scale; // 5 px, same as the 2D viewer
    for (const e of d?.entities ?? []) {
      const dist = pickable(e) ? distToPaths(hitPaths(e), raw) : Infinity;
      if (dist <= bestD) [best, bestD] = [e.handle, dist];
    }
    if (best) onToggle(best);
  };

  return (
    <>
      <p className="text-sm text-muted-foreground">폐곡선을 이루는 엔티티를 클릭해 선택하세요. (선, 호, 원, 폴리라인) 선택됨: {picked.size}개</p>
      <div className="h-80 overflow-hidden rounded-md border border-line">
        {d ? (
          <CanvasViewport entities={d.entities} extents={d.extents} hidden={hidden} onCursor={NOOP} onZoom={NOOP} selected={picked} preview={[]} onPick={onPick} snapOn={false} snapKinds={NO_SNAPS} gridStep={10} showGrid={false} markers={markers} />
        ) : (
          <div className="p-3">{render.error ? <Err text={errText(render.error)} /> : <Loading />}</div>
        )}
      </div>
    </>
  );
}

// FN-10: pick a closed profile on the current revision, then POST the EXTRUDE feature
function ExtrudeDialog({ docId, revisionId, onClose, onDone }: { docId: string; revisionId: string; onClose: () => void; onDone: (id: number) => void }) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [distance, setDistance] = useState("10");
  const [direction, setDirection] = useState<Direction>("+Z");
  const [error, setError] = useState<string | null>(null);
  const [markers, setMarkers] = useState<Pt[]>([]);
  const [busy, setBusy] = useState(false);

  const toggle = (h: string) => {
    setMarkers([]);
    setPicked((s) => {
      const n = new Set(s);
      if (!n.delete(h)) n.add(h);
      return n;
    });
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const dist = Number(distance);
    if (!picked.size) return setError("프로파일 엔티티를 하나 이상 선택하세요.");
    if (!(dist > 0)) return setError("거리는 0보다 커야 합니다.");
    setBusy(true);
    setError(null);
    setMarkers([]);
    try {
      const f = await api<Feature>(`/documents/${docId}/features`, { method: "POST", json: { feature_type: "EXTRUDE", params: { source_revision_id: revisionId, handles: [...picked], distance: dist, direction } } });
      onDone(f.feature_id);
    } catch (err) {
      setError(errText(err));
      setMarkers(dangling(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Extrude" onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-3">
        <ProfilePicker revisionId={revisionId} picked={picked} markers={markers} onToggle={toggle} />
        <DistanceDir distance={distance} direction={direction} onChange={(a, b) => (setDistance(a), setDirection(b))} />
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
            {busy ? "생성 중..." : "생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// FN-10 (S6): pick a closed profile on the current revision, then POST the REVOLVE feature
function RevolveDialog({ docId, revisionId, onClose, onDone }: { docId: string; revisionId: string; onClose: () => void; onDone: (id: number) => void }) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [form, setForm] = useState<RevolveFormState>({ ax: "0", ay: "0", dx: "0", dy: "1", angle: "360" });
  const [error, setError] = useState<string | null>(null);
  const [markers, setMarkers] = useState<Pt[]>([]);
  const [busy, setBusy] = useState(false);

  const toggle = (h: string) => {
    setMarkers([]);
    setPicked((s) => {
      const n = new Set(s);
      if (!n.delete(h)) n.add(h);
      return n;
    });
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const ax = Number(form.ax);
    const ay = Number(form.ay);
    const dx = Number(form.dx);
    const dy = Number(form.dy);
    const angle = Number(form.angle);
    if (!picked.size) return setError("프로파일 엔티티를 하나 이상 선택하세요.");
    if (![ax, ay, dx, dy, angle].every(Number.isFinite) || (dx === 0 && dy === 0)) return setError("회전축 방향 벡터는 (0, 0)일 수 없습니다.");
    if (!(angle > 0 && angle <= 360)) return setError("회전 각도는 0° 초과 360° 이하여야 합니다.");
    setBusy(true);
    setError(null);
    setMarkers([]);
    try {
      const f = await api<Feature>(`/documents/${docId}/features`, {
        method: "POST",
        json: { feature_type: "REVOLVE", params: { source_revision_id: revisionId, handles: [...picked], axis_point: [ax, ay], axis_dir: [dx, dy], angle_deg: angle } },
      });
      onDone(f.feature_id);
    } catch (err) {
      setError(errText(err));
      setMarkers(dangling(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Revolve" onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-3">
        <ProfilePicker revisionId={revisionId} picked={picked} markers={markers} onToggle={toggle} />
        <RevolveFields state={form} onChange={setForm} />
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
            {busy ? "생성 중..." : "생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// FN-11 (S6): pick target and tool bodies and POST the BOOLEAN feature
function BooleanDialog({ docId, candidates, onClose, onDone }: { docId: string; candidates: Feature[]; onClose: () => void; onDone: (id: number) => void }) {
  const [op, setOp] = useState<BooleanOp>("CUT");
  const [targetId, setTargetId] = useState(String(candidates[0]?.feature_id ?? ""));
  const [toolId, setToolId] = useState(String(candidates[1]?.feature_id ?? candidates[0]?.feature_id ?? ""));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const target = Number(targetId);
    const tool = Number(toolId);
    if (!target || !tool || target === tool) return setError("서로 다른 대상(Target)과 도구(Tool) 형상을 선택하세요.");
    setBusy(true);
    setError(null);
    try {
      const f = await api<Feature>(`/documents/${docId}/features`, {
        method: "POST",
        json: { feature_type: "BOOLEAN", params: { op, target_feature_id: target, tool_feature_id: tool } },
      });
      onDone(f.feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Boolean 연산" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <BooleanFields op={op} targetId={targetId} toolId={toolId} candidates={candidates} onChange={(o, a, b) => (setOp(o), setTargetId(a), setToolId(b))} />
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
            {busy ? "연산 중..." : "생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Properties({ f, features, canEdit, onChanged, onDeleted }: { f: Feature; features: Feature[]; canEdit: boolean; onChanged: () => void; onDeleted: () => void }) {
  const [distance, setDistance] = useState(f.feature_type === "EXTRUDE" ? String(f.params.distance) : "10");
  const [direction, setDirection] = useState<Direction>(f.feature_type === "EXTRUDE" ? f.params.direction : "+Z");
  const [rev, setRev] = useState<RevolveFormState>(() =>
    f.feature_type === "REVOLVE"
      ? { ax: String(f.params.axis_point[0]), ay: String(f.params.axis_point[1]), dx: String(f.params.axis_dir[0]), dy: String(f.params.axis_dir[1]), angle: String(f.params.angle_deg) }
      : { ax: "0", ay: "0", dx: "0", dy: "1", angle: "360" },
  );
  const [boolOp, setBoolOp] = useState<BooleanOp>(f.feature_type === "BOOLEAN" ? f.params.op : "CUT");
  const [targetId, setTargetId] = useState(f.feature_type === "BOOLEAN" ? String(f.params.target_feature_id) : "");
  const [toolId, setToolId] = useState(f.feature_type === "BOOLEAN" ? String(f.params.tool_feature_id) : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const candidates = useMemo(() => (f.feature_type === "BOOLEAN" ? booleanCandidates(features, f.seq, f.feature_id) : []), [f, features]);

  async function run(fn: () => Promise<unknown>, done: () => void) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      done();
    } catch (e) {
      setError(errText(e));
      onChanged(); // a failed regenerate may still flip the feature to ERROR
    } finally {
      setBusy(false);
    }
  }
  const regen = () => {
    if (f.feature_type === "EXTRUDE") {
      const dist = Number(distance);
      if (!(dist > 0)) return setError("거리는 0보다 커야 합니다.");
      void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: { ...f.params, distance: dist, direction } } }), onChanged);
      return;
    }
    if (f.feature_type === "REVOLVE") {
      const ax = Number(rev.ax);
      const ay = Number(rev.ay);
      const dx = Number(rev.dx);
      const dy = Number(rev.dy);
      const angle = Number(rev.angle);
      if (![ax, ay, dx, dy, angle].every(Number.isFinite) || (dx === 0 && dy === 0)) return setError("회전축 방향 벡터는 (0, 0)일 수 없습니다.");
      if (!(angle > 0 && angle <= 360)) return setError("회전 각도는 0° 초과 360° 이하여야 합니다.");
      void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: { ...f.params, axis_point: [ax, ay], axis_dir: [dx, dy], angle_deg: angle } } }), onChanged);
      return;
    }
    const target = Number(targetId);
    const tool = Number(toolId);
    if (!target || !tool || target === tool) return setError("서로 다른 대상(Target)과 도구(Tool) 형상을 선택하세요.");
    void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: { op: boolOp, target_feature_id: target, tool_feature_id: tool } } }), onChanged);
  };

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-foreground">{featureName(f)}</h2>
      {f.status === "ERROR" && <Err text={`⚠ ${f.error_code ? errText(new ApiError(f.error_code, f.error_code)) : "재생성 오류"}`} />}
      <fieldset disabled={!canEdit || busy} className="space-y-3">
        {f.feature_type === "EXTRUDE" && <DistanceDir distance={distance} direction={direction} onChange={(a, b) => (setDistance(a), setDirection(b))} />}
        {f.feature_type === "REVOLVE" && <RevolveFields state={rev} onChange={setRev} />}
        {f.feature_type === "BOOLEAN" && <BooleanFields op={boolOp} targetId={targetId} toolId={toolId} candidates={candidates} onChange={(o, a, b) => (setBoolOp(o), setTargetId(a), setToolId(b))} />}
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
          <p className="mb-4 text-sm">{featureName(f)}을(를) 삭제할까요?</p>
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
  const [modal, setModal] = useState<"EXTRUDE" | "REVOLVE" | "BOOLEAN" | null>(null);
  const [meshes, setMeshes] = useState<Record<string, MeshJson>>({});
  const [meshError, setMeshError] = useState<string | null>(null);
  const canEdit = !!me && can(me, "DESIGNER");
  const features = useMemo(() => feats.data?.items.toSorted((a, b) => a.seq - b.seq) ?? [], [feats.data]);
  const unconsumed = useMemo(() => booleanCandidates(features), [features]);
  const key = (f: Feature) => `${f.feature_id}:${f.updated_at}`;

  useEffect(() => {
    let live = true;
    for (const f of features) {
      if (f.status !== "OK" || !f.visible || meshes[key(f)]) continue;
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

  const shown = useMemo(() => features.filter((f) => f.status === "OK" && f.visible && meshes[key(f)]).map((f) => ({ id: f.feature_id, mesh: meshes[key(f)] })), [features, meshes]);
  const tris = shown.reduce((s, m) => s + m.mesh.triangle_count, 0);
  const sel = features.find((f) => f.feature_id === selectedId) ?? null;
  const m = sel?.metrics;
  const d = doc.data;
  const revId = d?.current_revision_id != null ? String(d.current_revision_id) : null;

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
        <aside aria-label="Feature" className="w-60 shrink-0 space-y-3 overflow-y-auto border-r border-line bg-card p-3">
          <div className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">Feature</h2>
            {canEdit && (
              <div className="flex flex-wrap gap-1.5">
                <button type="button" disabled={!revId} title={revId ? undefined : "리비전이 없습니다"} onClick={() => setModal("EXTRUDE")} className={btn2}>
                  Extrude
                </button>
                <button type="button" disabled={!revId} title={revId ? undefined : "리비전이 없습니다"} onClick={() => setModal("REVOLVE")} className={btn2}>
                  Revolve
                </button>
                <button type="button" disabled={unconsumed.length < 2} title={unconsumed.length >= 2 ? undefined : "조합할 바디가 2개 이상 필요합니다"} onClick={() => setModal("BOOLEAN")} className={btn2}>
                  Boolean
                </button>
              </div>
            )}
          </div>
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

      {modal === "EXTRUDE" && revId && (
        <ExtrudeDialog
          docId={docId}
          revisionId={revId}
          onClose={() => setModal(null)}
          onDone={(id) => {
            setModal(null);
            setSelectedId(id);
            feats.reload();
          }}
        />
      )}
      {modal === "REVOLVE" && revId && (
        <RevolveDialog
          docId={docId}
          revisionId={revId}
          onClose={() => setModal(null)}
          onDone={(id) => {
            setModal(null);
            setSelectedId(id);
            feats.reload();
          }}
        />
      )}
      {modal === "BOOLEAN" && (
        <BooleanDialog
          docId={docId}
          candidates={unconsumed}
          onClose={() => setModal(null)}
          onDone={(id) => {
            setModal(null);
            setSelectedId(id);
            feats.reload();
          }}
        />
      )}
    </div>
  );
}

export default function ModelPage({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return <Workbench docId={documentId} />;
}
