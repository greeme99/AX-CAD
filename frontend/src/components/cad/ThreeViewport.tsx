"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { edgeSegments, fitDistance, meshBuffers, sigKey, unionBBox, VIEW_DIRS, type BBox, type MeshJson, type Sig, type ViewName } from "@/lib/cad/mesh";
import { toLocal, toWorld, ydir, type Plane, type V3 } from "@/lib/cad/sketch";
import type { Pt } from "@/lib/cad/view";

export type OverlayColor = "line" | "construction" | "selected" | "preview" | "conflict" | "done" | "shown";
/** Sketch geometry drawn on its plane (2D paths in sketch coordinates). */
export type Overlay = { plane: Plane; paths: { pts: Pt[]; color: OverlayColor }[]; points?: { p: Pt; color: OverlayColor }[] };

type Props = {
  meshes: { id: number; mesh: MeshJson }[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  overlays?: Overlay[];
  /** Track 8 P2-1 sketch mode: the camera faces this plane, rotation is off and the pointer goes
   * to onSketch in sketch coordinates (tol = 6 px in mm) */
  sketchPlane?: Plane | null;
  onSketch?: (kind: "move" | "click", p: Pt, tol: number) => void;
  /** pick mode for a sketch plane: a click on a planar face reports its point and normal */
  onFacePick?: ((point: V3, normal: V3) => void) | null;
  /** Track 8 P2-4 pick mode on one body: a click on an edge (EDGE) or a face (FACE) toggles it */
  refPick?: RefPick | null;
};
export type RefPick = { bodyId: number; mode: "EDGE" | "FACE"; selected: Sig[]; onToggle: (s: Sig) => void };

const FOV = 45;
const VIEWS: [ViewName, string][] = [["front", "정면"], ["top", "평면"], ["right", "측면"], ["iso", "등각"]];
const GREY = 0x9aa3af;
const PICK_PX = 6;
const NORMAL_TOL = 1e-3; // a face is planar when all its mesh normals agree
// FreeCAD Sketcher colours: white edit geometry, blue construction, green when fully constrained
const OVERLAY: Record<OverlayColor, number> = { line: 0xf3f4f6, construction: 0x60a5fa, selected: 0x2563eb, preview: 0xfbbf24, conflict: 0xef4444, done: 0x22c55e, shown: 0x94a3b8 };
const token = (el: Element, name: string, fallback: string) => getComputedStyle(el).getPropertyValue(name).trim() || fallback;

type Ctx = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  group: THREE.Group;
  overlay: THREE.Group;
  picks: THREE.Group;
  draw: () => void;
  frame: (dir?: readonly [number, number, number]) => void;
  bbox: BBox | null;
};

/** World mm per screen pixel at the orbit target. */
function mmPerPx(camera: THREE.PerspectiveCamera, target: THREE.Vector3, height: number) {
  return (2 * camera.position.distanceTo(target) * Math.tan((camera.fov * Math.PI) / 360)) / Math.max(height, 1);
}

/** Planar-face test on the merged mesh: every normal of the OCCT face the hit triangle belongs to. */
function planarNormal(mesh: THREE.Mesh, tri: number): V3 | null {
  const face = (mesh.userData.faces as [number, number][]).find(([a, b]) => tri >= a && tri < b);
  if (!face) return null;
  const idx = mesh.geometry.getIndex()!;
  const n = mesh.geometry.getAttribute("normal");
  const first = new THREE.Vector3().fromBufferAttribute(n, idx.getX(face[0] * 3));
  for (let i = face[0] * 3; i < face[1] * 3; i++) if (new THREE.Vector3().fromBufferAttribute(n, idx.getX(i)).distanceTo(first) > NORMAL_TOL) return null;
  return first.normalize().toArray() as V3;
}

/** OCCT face (index into the mesh's faces) of a hit triangle. */
const faceOf = (mesh: THREE.Mesh, tri: number) => (mesh.userData.faces as [number, number][]).findIndex(([a, b]) => tri >= a && tri < b);

export default function ThreeViewport({ meshes, selectedId, onSelect, overlays = [], sketchPlane = null, onSketch, onFacePick = null, refPick = null }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const ctx = useRef<Ctx | null>(null);
  const selRef = useRef({ selectedId, onSelect, sketchPlane, onSketch, onFacePick, refPick });
  selRef.current = { selectedId, onSelect, sketchPlane, onSketch, onFacePick, refPick };

  // renderer / camera / controls: created once
  useEffect(() => {
    const wrap = wrapRef.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    const canvas = renderer.domElement;
    canvas.className = "block h-full w-full outline-none focus-visible:outline-2 focus-visible:outline-ring";
    canvas.tabIndex = 0;
    canvas.setAttribute("aria-label", "3D 모델 뷰포트: 좌 드래그 회전, 우 드래그 이동, 휠 확대/축소, 방향키 이동");
    wrap.prepend(canvas);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(token(wrap, "--canvas-bg", "#181b20"));
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444a55, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.8);
    sun.position.set(1, -2, 3);
    scene.add(sun);
    const group = new THREE.Group();
    scene.add(group);
    const overlay = new THREE.Group();
    scene.add(overlay);
    const picks = new THREE.Group();
    scene.add(picks);

    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 10000);
    camera.up.set(0, 0, 1); // Z up, set before the controls read it
    camera.position.set(100, -100, 100);
    const controls = new OrbitControls(camera, canvas);
    controls.listenToKeyEvents(canvas);
    const draw = () => renderer.render(scene, camera);
    controls.addEventListener("change", draw); // render on demand, no idle loop

    const c: Ctx = {
      renderer, scene, camera, controls, group, overlay, picks, draw, bbox: null,
      frame(dir) {
        const d = dir ?? camera.position.clone().sub(controls.target).normalize().toArray();
        const b = c.bbox ?? { min: [-50, -50, -50] as [number, number, number], max: [50, 50, 50] as [number, number, number] };
        const center = new THREE.Vector3(...b.min).add(new THREE.Vector3(...b.max)).multiplyScalar(0.5);
        const dist = fitDistance(b, FOV, camera.aspect);
        camera.position.copy(center).addScaledVector(new THREE.Vector3(...d), dist);
        camera.near = dist / 100;
        camera.far = dist * 100;
        camera.updateProjectionMatrix();
        controls.target.copy(center);
        controls.update();
        draw();
      },
    };
    ctx.current = c;

    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      draw();
    });
    ro.observe(wrap);

    // click = pointer down/up without a drag (drag rotates/pans)
    let down: [number, number] | null = null;
    const ray = new THREE.Raycaster();
    const aim = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
      return r.height;
    };
    /** Cursor on the sketch plane in sketch coordinates, and the pick tolerance in mm. */
    const onPlane = (e: PointerEvent, pl: Plane): [Pt, number] | null => {
      const h = aim(e);
      const n = new THREE.Vector3(...pl.normal).normalize();
      const at = ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, new THREE.Vector3(...pl.origin)), new THREE.Vector3());
      return at ? [toLocal(pl, at.toArray() as V3), PICK_PX * mmPerPx(camera, controls.target, h)] : null;
    };
    const onDown = (e: PointerEvent) => (down = [e.clientX, e.clientY]);
    const onMove = (e: PointerEvent) => {
      const { sketchPlane: pl, onSketch: cb } = selRef.current;
      const hit = pl && cb ? onPlane(e, pl) : null;
      if (hit) cb!("move", ...hit);
    };
    const onUp = (e: PointerEvent) => {
      if (!down || e.button !== 0 || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
      const { sketchPlane: pl, onSketch: cb, onFacePick: pick, refPick: rp } = selRef.current;
      if (pl && cb) {
        const hit = onPlane(e, pl);
        if (hit) cb("click", ...hit);
        return;
      }
      const h = aim(e);
      if (rp) {
        const body = (group.children as THREE.Mesh[]).find((o) => o.userData.id === rp.bodyId);
        if (!body) return;
        const face = ray.intersectObject(body, false)[0];
        if (rp.mode === "FACE") {
          const sig = face?.faceIndex != null ? (body.userData.faceSigs as (Sig | undefined)[])[faceOf(body, face.faceIndex)] : undefined;
          if (sig) rp.onToggle(sig);
          return;
        }
        const tol = PICK_PX * mmPerPx(camera, controls.target, h);
        ray.params.Line = { threshold: tol };
        // an edge in front of (or on) the face under the cursor, not one hidden behind the body
        const edge = ray.intersectObject(body.children[0], false).find((x) => x.distance <= (face?.distance ?? Infinity) + tol);
        const sigs = body.userData.edgeSigs as Sig[] | undefined;
        if (edge?.index != null && sigs) rp.onToggle(sigs[(body.userData.edgeOf as Int32Array)[edge.index / 2]]);
        return;
      }
      const hit = ray.intersectObjects(group.children, false)[0];
      if (pick) {
        const n = hit && hit.faceIndex != null ? planarNormal(hit.object as THREE.Mesh, hit.faceIndex) : null;
        if (hit && n) pick(hit.point.toArray() as V3, n);
        return;
      }
      selRef.current.onSelect(hit ? (hit.object.userData.id as number) : null);
    };
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);

    return () => {
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      controls.dispose();
      renderer.dispose();
      canvas.remove();
      ctx.current = null;
    };
  }, []);

  // rebuild meshes when the data changes
  useEffect(() => {
    const c = ctx.current!;
    const disposeAll = () => {
      for (const o of c.group.children as THREE.Mesh[]) {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
        for (const e of o.children as THREE.LineSegments[]) {
          e.geometry.dispose();
          (e.material as THREE.Material).dispose();
        }
      }
      c.group.clear();
    };
    const hadMeshes = c.bbox !== null;
    disposeAll();
    for (const { id, mesh } of meshes) {
      const b = meshBuffers(mesh.faces);
      if (!b.indices.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(b.positions, 3));
      g.setAttribute("normal", new THREE.BufferAttribute(b.normals, 3));
      g.setIndex(new THREE.BufferAttribute(b.indices, 1));
      const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: GREY, roughness: 0.6, metalness: 0.1, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
      m.userData.id = id;
      let t = 0; // triangle range of each OCCT face, for the planar-face pick
      m.userData.faces = mesh.faces.map((f) => [t, (t += f.indices.length / 3)]);
      m.userData.faceSigs = mesh.faces.map((f) => f.sig);
      let edges: THREE.BufferGeometry = new THREE.EdgesGeometry(g, 20);
      if (mesh.edges?.length) {
        // the B-Rep's own edges (Track 8 P2-4): what a pick selects
        const seg = edgeSegments(mesh.edges);
        edges = new THREE.BufferGeometry();
        edges.setAttribute("position", new THREE.BufferAttribute(seg.positions, 3));
        m.userData.edgeOf = seg.edgeOf;
        m.userData.edgeSigs = mesh.edges.map((e) => e.sig);
        m.userData.edgePts = mesh.edges.map((e) => e.points);
      }
      m.add(new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0x1f2937 })));
      c.group.add(m);
    }
    c.bbox = unionBBox(meshes.map((m) => m.mesh.bbox));
    // ponytail: re-frames only when the viewport was empty (keeps the user's camera after regenerate), no per-edit animation
    if (!hadMeshes) c.frame(VIEW_DIRS.iso);
    else c.draw();
    return disposeAll;
  }, [meshes]);

  // tint the selected feature (also re-applied after a rebuild)
  useEffect(() => {
    const c = ctx.current!;
    const sel = new THREE.Color(token(wrapRef.current!, "--color-cad-selection", "#2563eb"));
    for (const o of c.group.children as THREE.Mesh[]) (o.material as THREE.MeshStandardMaterial).color.set(o.userData.id === selectedId ? sel : GREY);
    c.draw();
  }, [selectedId, meshes]);

  // picked edges / faces of the pick mode, drawn over the body
  useEffect(() => {
    const c = ctx.current!;
    const clear = () => {
      for (const o of c.picks.children as THREE.Mesh[]) {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      }
      c.picks.clear();
    };
    clear();
    const body = refPick && (c.group.children as THREE.Mesh[]).find((o) => o.userData.id === refPick.bodyId);
    if (body && refPick.selected.length) {
      const want = new Set(refPick.selected.map(sigKey));
      const color = new THREE.Color(token(wrapRef.current!, "--color-cad-selection", "#2563eb"));
      if (refPick.mode === "EDGE") {
        const pts = ((body.userData.edgeSigs ?? []) as Sig[]).flatMap((s, i) => (want.has(sigKey(s)) ? [(body.userData.edgePts as number[][])[i]] : []));
        const seg = edgeSegments(pts.map((points) => ({ points, sig: { kind: "", mid: [0, 0, 0] } })));
        const g = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(seg.positions, 3));
        const line = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xfbbf24, depthTest: false }));
        line.renderOrder = 4;
        c.picks.add(line);
      } else {
        // copy the picked faces' triangles (sharing the body's attributes would free them on dispose)
        const pos = body.geometry.getAttribute("position");
        const idx = body.geometry.getIndex()!;
        const out: number[] = [];
        (body.userData.faces as [number, number][]).forEach(([a, b], i) => {
          const s = (body.userData.faceSigs as (Sig | undefined)[])[i];
          if (s && want.has(sigKey(s))) for (let k = a * 3; k < b * 3; k++) out.push(pos.getX(idx.getX(k)), pos.getY(idx.getX(k)), pos.getZ(idx.getX(k)));
        });
        const g = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(out, 3));
        c.picks.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 })));
      }
    }
    c.draw();
    return clear;
  }, [refPick, meshes]);

  // sketch overlays: lines on top of the bodies so the sketch stays readable inside a part
  useEffect(() => {
    const c = ctx.current!;
    const clear = () => {
      for (const o of c.overlay.children as (THREE.Line | THREE.Points)[]) {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      }
      c.overlay.clear();
    };
    clear();
    for (const ov of overlays) {
      for (const { pts, color } of ov.paths) {
        if (pts.length < 2) continue;
        const g = new THREE.BufferGeometry().setFromPoints(pts.map((p) => new THREE.Vector3(...toWorld(ov.plane, p))));
        const line = new THREE.Line(g, new THREE.LineBasicMaterial({ color: OVERLAY[color], depthTest: color === "shown" }));
        line.renderOrder = 2;
        c.overlay.add(line);
      }
      for (const { p, color } of ov.points ?? []) {
        const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...toWorld(ov.plane, p))]);
        const dot = new THREE.Points(g, new THREE.PointsMaterial({ color: OVERLAY[color], size: 6, sizeAttenuation: false, depthTest: false }));
        dot.renderOrder = 3;
        c.overlay.add(dot);
      }
    }
    c.draw();
    return clear;
  }, [overlays]);

  // sketch mode: look straight at the plane (sketch y up), no rotation. The viewport is mounted
  // fresh for each sketch session, so leaving it needs no restore.
  const planeKey = sketchPlane ? JSON.stringify(sketchPlane) : "";
  useEffect(() => {
    const c = ctx.current!;
    const pl = sketchPlane;
    if (!pl) return;
    const n = new THREE.Vector3(...pl.normal).normalize();
    const o = new THREE.Vector3(...pl.origin);
    const b = c.bbox;
    const center = b ? new THREE.Vector3(...b.min).add(new THREE.Vector3(...b.max)).multiplyScalar(0.5) : o.clone();
    center.addScaledVector(n, -center.clone().sub(o).dot(n)); // onto the plane
    const dist = b ? fitDistance(b, FOV, c.camera.aspect) : 200;
    c.camera.up.set(...ydir(pl));
    c.camera.position.copy(center).addScaledVector(n, dist);
    c.camera.near = dist / 100;
    c.camera.far = dist * 100;
    c.camera.updateProjectionMatrix();
    c.controls.target.copy(center);
    c.controls.enableRotate = false;
    c.camera.lookAt(center);
    c.draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- planeKey stands for the plane's value
  }, [planeKey]);

  const btn = "rounded-md border border-line bg-card px-3 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring";
  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden bg-canvas">
      <div className={`absolute bottom-3 left-3 flex gap-2 ${sketchPlane ? "hidden" : ""}`}>
        {VIEWS.map(([v, label]) => (
          <button key={v} type="button" onClick={() => ctx.current?.frame(VIEW_DIRS[v])} className={btn}>
            {label}
          </button>
        ))}
      </div>
      <button type="button" onClick={() => ctx.current?.frame()} className={`${btn} absolute right-3 bottom-3`}>
        Fit
      </button>
    </div>
  );
}
