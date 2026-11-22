"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { transformBBox } from "@/lib/cad/assembly";
import { edgeSegments, fitDistance, meshBuffers, sigKey, unionBBox, VIEW_DIRS, type BBox, type MeshJson, type Sig, type ViewName } from "@/lib/cad/mesh";
import { toLocal, toWorld, ydir, type Plane, type V3 } from "@/lib/cad/sketch";
import type { Pt } from "@/lib/cad/view";

export type OverlayColor = "line" | "construction" | "selected" | "preview" | "conflict" | "done" | "shown";
/** Sketch geometry drawn on its plane (2D paths in sketch coordinates). */
export type Overlay = { plane: Plane; paths: { pts: Pt[]; color: OverlayColor }[]; points?: { p: Pt; color: OverlayColor }[] };

type Props = {
  /** matrix: Track 9 assembly placement (column-major 4x4, part -> world); picks stay in part coordinates */
  meshes: { id: number; mesh: MeshJson; matrix?: number[] }[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  overlays?: Overlay[];
  /** Track 8 P2-1 sketch mode: the camera faces this plane, rotation is off and the pointer goes
   * to onSketch in sketch coordinates (tol = 6 px in mm) */
  sketchPlane?: Plane | null;
  onSketch?: (kind: "move" | "click", p: Pt, tol: number) => void;
  /** pick mode for a sketch plane: a click on a planar face reports its point and normal */
  onFacePick?: ((point: V3, normal: V3) => void) | null;
  /** Track 8 P2-4 pick mode on one body: a click on an edge (EDGE), a face (FACE) or either
   * (ANY: the edge when one is under the cursor, P2-5 measurement) toggles it */
  refPick?: RefPick | null;
  /** plan §23 S8: this body is drawn with zebra stripes (reflection lines from the mesh normals) */
  zebraId?: number | null;
  /** plan §23 S8: analysis samples as coloured points (xyz / rgb triples) */
  cloud?: { positions: Float32Array; colors: Float32Array } | null;
};
export type RefPick = { bodyId: number; mode: "EDGE" | "FACE" | "ANY"; selected: Sig[]; onToggle: (s: Sig) => void };

type RenderMode = "shaded-edges" | "shaded" | "wireframe";
const RENDER_MODES: [RenderMode, string][] = [
  ["shaded-edges", "모서리 음영"],
  ["shaded", "음영"],
  ["wireframe", "와이어프레임"],
];

const FOV = 45;
const VIEWS: [ViewName, string][] = [["front", "정면"], ["top", "평면"], ["right", "측면"], ["iso", "등각"]];
const CUBE_SIZE = 100;
const CUBE_FACES: { name: string; dir: readonly [number, number, number]; rot: [number, number, number]; pos: [number, number, number] }[] = [
  { name: "정면", dir: [0, -1, 0], rot: [Math.PI / 2, 0, 0], pos: [0, 0, -CUBE_SIZE / 2] },
  { name: "후면", dir: [0, 1, 0], rot: [-Math.PI / 2, 0, Math.PI], pos: [0, 0, CUBE_SIZE / 2] },
  { name: "평면", dir: [0, 0, 1], rot: [0, 0, 0], pos: [0, CUBE_SIZE / 2, 0] },
  { name: "저면", dir: [0, 0, -1], rot: [Math.PI, 0, 0], pos: [0, -CUBE_SIZE / 2, 0] },
  { name: "우측", dir: [1, 0, 0], rot: [Math.PI / 2, Math.PI / 2, 0], pos: [CUBE_SIZE / 2, 0, 0] },
  { name: "좌측", dir: [-1, 0, 0], rot: [Math.PI / 2, -Math.PI / 2, 0], pos: [-CUBE_SIZE / 2, 0, 0] },
];
const GREY = 0x9aa3af;
const PICK_PX = 6;
const PICKED = 0xfbbf24; // picked edges / faces: amber, apart from the blue of the selected body
const NORMAL_TOL = 1e-3; // a face is planar when all its mesh normals agree
// FreeCAD Sketcher colours: white edit geometry, blue construction, green when fully constrained
const OVERLAY: Record<OverlayColor, number> = { line: 0xf3f4f6, construction: 0x60a5fa, selected: 0x2563eb, preview: 0xfbbf24, conflict: 0xef4444, done: 0x22c55e, shown: 0x94a3b8 };
const solidMat = () => new THREE.MeshStandardMaterial({ color: GREY, roughness: 0.6, metalness: 0.1, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
// stripes of the view direction mirrored in the surface: they kink at a G0 edge, break direction at G1
const zebraMat = () =>
  new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    vertexShader: "varying vec3 n; varying vec3 v; void main() { vec4 p = modelViewMatrix * vec4(position, 1.0); n = normalMatrix * normal; v = p.xyz; gl_Position = projectionMatrix * p; }",
    fragmentShader: "varying vec3 n; varying vec3 v; void main() { vec3 r = reflect(normalize(v), normalize(n)); gl_FragColor = vec4(vec3(step(0.5, fract(r.y * 6.0)) * 0.85 + 0.1), 1.0); }",
  });
const token = (el: Element, name: string, fallback: string) => getComputedStyle(el).getPropertyValue(name).trim() || fallback;

type Ctx = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  group: THREE.Group;
  overlay: THREE.Group;
  picks: THREE.Group;
  selBox: THREE.Group;
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

export default function ThreeViewport({ meshes, selectedId, onSelect, overlays = [], sketchPlane = null, onSketch, onFacePick = null, refPick = null, zebraId = null, cloud = null }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const ctx = useRef<Ctx | null>(null);
  const [renderMode, setRenderMode] = useState<RenderMode>("shaded-edges");
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [webglError, setWebglError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const selRef = useRef({ selectedId, onSelect, sketchPlane, onSketch, onFacePick, refPick });
  selRef.current = { selectedId, onSelect, sketchPlane, onSketch, onFacePick, refPick };

  // renderer / camera / controls: created once (or re-created on WebGL retry)
  useEffect(() => {
    const wrap = wrapRef.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      setWebglError("3D 뷰포트(WebGL)를 초기화할 수 없습니다. 브라우저의 하드웨어 가속 설정을 확인하거나 재시도하세요.");
      return;
    }
    setWebglError(null);
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    const canvas = renderer.domElement;
    const onContextLost = (e: Event) => {
      e.preventDefault();
      setWebglError("WebGL 그래픽 컨텍스트가 손실되었습니다. GPU 드라이버 리셋 또는 메모리 부족일 수 있습니다.");
    };
    const onContextRestored = () => {
      setWebglError(null);
      ctx.current?.draw();
    };
    canvas.addEventListener("webglcontextlost", onContextLost);
    canvas.addEventListener("webglcontextrestored", onContextRestored);
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
    const selBox = new THREE.Group();
    scene.add(selBox);

    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 10000);
    camera.up.set(0, 0, 1); // Z up, set before the controls read it
    camera.position.set(100, -100, 100);
    const controls = new OrbitControls(camera, canvas);
    controls.listenToKeyEvents(canvas);
    const draw = () => renderer.render(scene, camera);
    controls.addEventListener("change", draw); // render on demand, no idle loop

    const c: Ctx = {
      renderer, scene, camera, controls, group, overlay, picks, selBox, draw, bbox: null,
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
    let lastHover: number | null = null;
    const onMove = (e: PointerEvent) => {
      const { sketchPlane: pl, onSketch: cb } = selRef.current;
      if (pl && cb) {
        const hit = onPlane(e, pl);
        if (hit) cb("move", ...hit);
        return;
      }
      if (e.buttons !== 0) return; // skip hover raycast while orbiting/panning
      aim(e);
      const hit = ray.intersectObjects(group.children, false)[0];
      const next = hit ? (hit.object.userData.id as number) : null;
      if (next !== lastHover) {
        lastHover = next;
        canvas.style.cursor = next != null ? "pointer" : "grab";
        setHoveredId(next);
      }
    };
    const onLeave = () => {
      if (lastHover !== null) {
        lastHover = null;
        canvas.style.cursor = "grab";
        setHoveredId(null);
      }
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
        if (rp.mode !== "FACE") {
          const tol = PICK_PX * mmPerPx(camera, controls.target, h);
          ray.params.Line = { threshold: tol };
          // an edge in front of (or on) the face under the cursor, not one hidden behind the body
          const edge = ray.intersectObject(body.children[0], false).find((x) => x.distance <= (face?.distance ?? Infinity) + tol);
          const sigs = body.userData.edgeSigs as Sig[] | undefined;
          if (edge?.index != null && sigs) return rp.onToggle(sigs[(body.userData.edgeOf as Int32Array)[edge.index / 2]]);
          if (rp.mode === "EDGE") return;
        }
        const sig = face?.faceIndex != null ? (body.userData.faceSigs as (Sig | undefined)[])[faceOf(body, face.faceIndex)] : undefined;
        if (sig) rp.onToggle(sig);
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
    canvas.addEventListener("pointerleave", onLeave);

    // --- ViewCube: mini 3D orientation cube + XYZ axes gizmo in the top-right corner ---
    const cubeDiv = document.createElement("div");
    cubeDiv.tabIndex = 0;
    cubeDiv.setAttribute("role", "group");
    cubeDiv.setAttribute("aria-label", "3D ViewCube (클릭 또는 Enter로 등각 시점 전환)");
    cubeDiv.style.cssText =
      "position:absolute;top:12px;right:12px;width:108px;height:108px;pointer-events:auto;border-radius:12px;background:rgba(15,23,42,0.45);backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,0.12);display:flex;align-items:center;justify-content:center;";
    const onCubeKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        c.frame(VIEW_DIRS.iso);
      }
    };
    cubeDiv.addEventListener("keydown", onCubeKeyDown);
    wrap.appendChild(cubeDiv);
    const cubeRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    cubeRenderer.setSize(104, 104);
    cubeRenderer.setPixelRatio(window.devicePixelRatio || 1);
    cubeRenderer.domElement.style.cssText = "cursor:pointer;border-radius:10px;";
    cubeDiv.appendChild(cubeRenderer.domElement);

    const cubeScene = new THREE.Scene();
    const cubeCamera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 1000);
    cubeCamera.up.set(0, 0, 1);
    cubeCamera.position.set(150, -150, 120);
    cubeCamera.lookAt(0, 0, 0);
    cubeScene.add(new THREE.AmbientLight(0xffffff, 0.9));
    cubeScene.add(new THREE.DirectionalLight(0xffffff, 1.2));

    // XYZ orientation axes gizmo anchored at cube corner
    const axes = new THREE.AxesHelper(CUBE_SIZE * 0.9);
    axes.position.set(-CUBE_SIZE / 2 - 4, -CUBE_SIZE / 2 - 4, -CUBE_SIZE / 2 - 4);
    cubeScene.add(axes);

    const cubeMeshes: THREE.Mesh[] = [];
    for (const face of CUBE_FACES) {
      const g = new THREE.PlaneGeometry(CUBE_SIZE * 0.95, CUBE_SIZE * 0.95);
      const cvs = document.createElement("canvas");
      cvs.width = 128; cvs.height = 128;
      const c2 = cvs.getContext("2d")!;
      c2.fillStyle = "rgba(30,41,59,0.9)";
      c2.fillRect(0, 0, 128, 128);
      c2.strokeStyle = "rgba(148,163,184,0.5)";
      c2.lineWidth = 3;
      c2.strokeRect(2, 2, 124, 124);
      c2.fillStyle = "#f1f5f9";
      c2.font = "bold 24px sans-serif";
      c2.textAlign = "center";
      c2.textBaseline = "middle";
      c2.fillText(face.name, 64, 64);
      const tex = new THREE.CanvasTexture(cvs);
      const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, color: 0xffffff });
      const m = new THREE.Mesh(g, mat);
      m.rotation.set(...face.rot);
      m.position.set(...face.pos);
      m.userData.viewDir = face.dir;
      cubeMeshes.push(m);
      cubeScene.add(m);
    }
    const wireG = new THREE.BoxGeometry(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE);
    cubeScene.add(new THREE.LineSegments(new THREE.EdgesGeometry(wireG), new THREE.LineBasicMaterial({ color: 0x94a3b8 })));
    wireG.dispose();

    const drawCube = () => {
      const dir = camera.position.clone().sub(controls.target).normalize().multiplyScalar(210);
      cubeCamera.position.copy(dir);
      cubeCamera.up.copy(camera.up);
      cubeCamera.lookAt(0, 0, 0);
      cubeRenderer.render(cubeScene, cubeCamera);
    };
    controls.addEventListener("change", drawCube);
    drawCube();

    const cubeRay = new THREE.Raycaster();
    let hoveredCubeFace: THREE.Mesh | null = null;
    const hitCubeFace = (e: MouseEvent): THREE.Mesh | null => {
      const rect = cubeRenderer.domElement.getBoundingClientRect();
      cubeRay.setFromCamera(
        new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1),
        cubeCamera,
      );
      return (cubeRay.intersectObjects(cubeMeshes, false)[0]?.object as THREE.Mesh) ?? null;
    };
    const onCubeMove = (e: MouseEvent) => {
      const hit = hitCubeFace(e);
      if (hit === hoveredCubeFace) return;
      if (hoveredCubeFace) (hoveredCubeFace.material as THREE.MeshBasicMaterial).color.setHex(0xffffff);
      hoveredCubeFace = hit;
      if (hoveredCubeFace) (hoveredCubeFace.material as THREE.MeshBasicMaterial).color.setHex(0x60a5fa);
      drawCube();
    };
    const onCubeLeave = () => {
      if (!hoveredCubeFace) return;
      (hoveredCubeFace.material as THREE.MeshBasicMaterial).color.setHex(0xffffff);
      hoveredCubeFace = null;
      drawCube();
    };
    const onCubeClick = (e: MouseEvent) => {
      const hit = hitCubeFace(e);
      if (hit?.userData.viewDir) c.frame(hit.userData.viewDir);
    };
    cubeRenderer.domElement.addEventListener("pointermove", onCubeMove);
    cubeRenderer.domElement.addEventListener("pointerleave", onCubeLeave);
    cubeRenderer.domElement.addEventListener("click", onCubeClick);

    return () => {
      ro.disconnect();
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.removeEventListener("webglcontextrestored", onContextRestored);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      cubeDiv.removeEventListener("keydown", onCubeKeyDown);
      cubeRenderer.domElement.removeEventListener("pointermove", onCubeMove);
      cubeRenderer.domElement.removeEventListener("pointerleave", onCubeLeave);
      cubeRenderer.domElement.removeEventListener("click", onCubeClick);
      controls.removeEventListener("change", drawCube);
      controls.dispose();
      cubeScene.traverse((obj) => {
        if (obj instanceof THREE.Mesh || obj instanceof THREE.LineSegments) {
          obj.geometry.dispose();
          const mat = obj.material;
          if (mat instanceof THREE.MeshBasicMaterial) mat.map?.dispose();
          if (mat instanceof THREE.Material) mat.dispose();
        }
      });
      renderer.dispose();
      cubeRenderer.dispose();
      canvas.remove();
      cubeDiv.remove();
      ctx.current = null;
    };
  }, [retryKey]);

  // rebuild meshes when the data changes
  useEffect(() => {
    const c = ctx.current;
    if (!c) return;
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
    for (const { id, mesh, matrix } of meshes) {
      const b = meshBuffers(mesh.faces);
      if (!b.indices.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(b.positions, 3));
      g.setAttribute("normal", new THREE.BufferAttribute(b.normals, 3));
      g.setIndex(new THREE.BufferAttribute(b.indices, 1));
      const m = new THREE.Mesh(g, solidMat());
      m.userData.id = id;
      if (matrix) {
        m.matrixAutoUpdate = false;
        m.matrix.fromArray(matrix);
      }
      let t = 0; // triangle range of each OCCT face, for the planar-face pick
      m.userData.faces = mesh.faces.map((f) => [t, (t += f.indices.length / 3)]);
      m.userData.faceSigs = mesh.faces.map((f) => f.sig);
      let edges: THREE.BufferGeometry;
      if (mesh.edges?.length) {
        // the B-Rep's own edges (Track 8 P2-4): what a pick selects
        const seg = edgeSegments(mesh.edges);
        edges = new THREE.BufferGeometry();
        edges.setAttribute("position", new THREE.BufferAttribute(seg.positions, 3));
        m.userData.edgeOf = seg.edgeOf;
        m.userData.edgeSigs = mesh.edges.map((e) => e.sig);
        m.userData.edgePts = mesh.edges.map((e) => e.points);
      } else {
        edges = new THREE.EdgesGeometry(g, 20);
      }
      m.add(new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0x1f2937 })));
      c.group.add(m);
    }
    c.bbox = unionBBox(meshes.map((m) => (m.matrix ? transformBBox(m.mesh.bbox, m.matrix) : m.mesh.bbox)));
    // ponytail: re-frames only when the viewport was empty (keeps the user's camera after regenerate), no per-edit animation
    if (!hadMeshes) c.frame(VIEW_DIRS.iso);
    else c.draw();
    return disposeAll;
  }, [meshes]);

  // tint the selected / hovered feature, render 3D bounding box for selection, & apply render mode
  useEffect(() => {
    const c = ctx.current;
    if (!c) return;
    const sel = new THREE.Color(token(wrapRef.current!, "--cad-selection-outline", "#4f8cff"));
    const hov = new THREE.Color(token(wrapRef.current!, "--cad-hover-outline", "#60a5fa"));
    const clearSelBox = () => {
      for (const h of c.selBox.children as THREE.LineSegments[]) {
        h.geometry.dispose();
        (h.material as THREE.Material).dispose();
      }
      c.selBox.clear();
    };
    clearSelBox();
    for (const o of c.group.children as THREE.Mesh[]) {
      const id = o.userData.id as number;
      const isSelected = id === selectedId;
      const isHovered = !isSelected && id === hoveredId;
      const zebra = id === zebraId;
      if (zebra !== !!o.userData.zebra) {
        (o.material as THREE.Material).dispose();
        o.material = zebra ? zebraMat() : solidMat();
        o.userData.zebra = zebra;
      }
      const mat = o.material as THREE.Material;
      mat.visible = zebra || renderMode !== "wireframe";
      if (!zebra) {
        (mat as THREE.MeshStandardMaterial).color.set(isSelected ? sel : isHovered ? 0xb8c4d4 : GREY);
      }
      for (const e of o.children as THREE.LineSegments[]) {
        e.visible = renderMode !== "shaded" || isSelected || isHovered;
        const edgeColor = isSelected ? sel.getHex() : isHovered ? hov.getHex() : renderMode === "wireframe" ? 0xcbd5e1 : 0x1f2937;
        (e.material as THREE.LineBasicMaterial).color.setHex(edgeColor);
      }
      if (isSelected) {
        const box = new THREE.Box3().setFromObject(o);
        if (!box.isEmpty()) {
          const helper = new THREE.Box3Helper(box, sel.getHex());
          const hMat = helper.material as THREE.LineBasicMaterial;
          hMat.transparent = true;
          hMat.opacity = 0.9;
          c.selBox.add(helper);
        }
      }
    }
    c.draw();
    return clearSelBox;
  }, [selectedId, hoveredId, meshes, zebraId, renderMode]);

  useEffect(() => {
    const c = ctx.current!;
    if (!cloud) return;
    const g = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(cloud.positions, 3)).setAttribute("color", new THREE.BufferAttribute(cloud.colors, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ size: 7, sizeAttenuation: false, vertexColors: true, depthTest: false }));
    pts.renderOrder = 5;
    c.scene.add(pts);
    c.draw();
    return () => {
      c.scene.remove(pts);
      g.dispose();
      (pts.material as THREE.Material).dispose();
      if (ctx.current) c.draw();
    };
  }, [cloud]);

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
      if (refPick.mode !== "FACE") {
        const pts = ((body.userData.edgeSigs ?? []) as Sig[]).flatMap((s, i) => (want.has(sigKey(s)) ? [(body.userData.edgePts as number[][])[i]] : []));
        const seg = edgeSegments(pts.map((points) => ({ points, sig: { kind: "", mid: [0, 0, 0] } })));
        const g = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(seg.positions, 3));
        const line = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: PICKED, depthTest: false }));
        line.renderOrder = 4;
        line.matrixAutoUpdate = false;
        line.matrix.copy(body.matrix);
        c.picks.add(line);
      }
      if (refPick.mode !== "EDGE") {
        // copy the picked faces' triangles (sharing the body's attributes would free them on dispose)
        const pos = body.geometry.getAttribute("position");
        const idx = body.geometry.getIndex()!;
        const out: number[] = [];
        (body.userData.faces as [number, number][]).forEach(([a, b], i) => {
          const s = (body.userData.faceSigs as (Sig | undefined)[])[i];
          if (s && want.has(sigKey(s))) for (let k = a * 3; k < b * 3; k++) out.push(pos.getX(idx.getX(k)), pos.getY(idx.getX(k)), pos.getZ(idx.getX(k)));
        });
        const g = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(out, 3));
        const fm = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: PICKED, transparent: true, opacity: 0.6, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
        fm.matrixAutoUpdate = false;
        fm.matrix.copy(body.matrix);
        c.picks.add(fm);
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

  const modeMenuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!modeMenuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModeMenuOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      if (modeMenuRef.current && !modeMenuRef.current.contains(e.target as Node)) setModeMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [modeMenuOpen]);

  const btn =
    "inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-foreground transition-all hover:bg-hover hover:-translate-y-0.5 active:scale-95 active:bg-[var(--color-primary-light)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus-ring)]";
  const currentModeLabel = RENDER_MODES.find(([m]) => m === renderMode)?.[1] ?? "모서리 음영";
  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden bg-canvas">
      {webglError && (
        <div
          role="alert"
          className="absolute inset-0 z-40 flex items-center justify-center bg-background/90 p-6 backdrop-blur-sm"
        >
          <div className="max-w-md space-y-3 rounded-xl border border-[var(--color-cad-warning)]/40 bg-card p-5 text-sm shadow-xl">
            <p className="font-semibold text-[var(--color-cad-warning)]">3D 뷰포트 그래픽 오류</p>
            <p className="text-body">{webglError}</p>
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => setRetryKey((k) => k + 1)}
                className="rounded-md bg-[var(--color-primary-solid)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--color-primary-solid-hover)]"
              >
                뷰포트 재시도
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="rounded-md border border-line px-3 py-1.5 text-xs text-foreground hover:bg-hover"
              >
                페이지 새로고침
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="cad-floating-pill absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1 px-2 py-1">
        {!sketchPlane && (
          <>
            {VIEWS.map(([v, label]) => (
              <button key={v} type="button" onClick={() => ctx.current?.frame(VIEW_DIRS[v])} className={btn}>
                {label}
              </button>
            ))}
            <span className="mx-1 h-4 w-px bg-[var(--cad-glass-border)]" aria-hidden="true" />
            <div ref={modeMenuRef} className="relative">
              <button
                type="button"
                aria-expanded={modeMenuOpen}
                aria-haspopup="menu"
                onClick={() => setModeMenuOpen((v) => !v)}
                className={btn}
              >
                <span>{currentModeLabel}</span>
                <span aria-hidden="true" className="text-[10px] opacity-70">▾</span>
              </button>
              {modeMenuOpen && (
                <div
                  role="menu"
                  className="absolute bottom-full left-1/2 mb-2 min-w-[144px] -translate-x-1/2 rounded-lg border border-[var(--color-border-strong)] bg-card/95 py-1 shadow-xl backdrop-blur-md"
                >
                  {RENDER_MODES.map(([mode, label]) => {
                    const active = renderMode === mode;
                    return (
                      <button
                        key={mode}
                        type="button"
                        role="menuitemradio"
                        aria-checked={active}
                        onClick={() => {
                          setRenderMode(mode);
                          setModeMenuOpen(false);
                        }}
                        className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs transition-colors focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-[var(--color-focus-ring)] ${
                          active
                            ? "bg-[var(--color-primary-light)] font-semibold text-foreground hover:bg-[var(--color-primary)]/25"
                            : "text-body hover:bg-hover"
                        }`}
                      >
                        <span>{label}</span>
                        {active && <span aria-hidden="true" className="font-bold text-[var(--color-primary)]">✓</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <span className="mx-1 h-4 w-px bg-[var(--cad-glass-border)]" aria-hidden="true" />
          </>
        )}
        <button type="button" onClick={() => ctx.current?.frame()} aria-label="화면 맞춤 (Fit)" className={`${btn} font-semibold text-[var(--color-primary)]`}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 9V5a1 1 0 0 1 1-1h4" /><path d="M20 9V5a1 1 0 0 0-1-1h-4" />
            <path d="M4 15v4a1 1 0 0 0 1 1h4" /><path d="M20 15v4a1 1 0 0 1-1 1h-4" />
          </svg>
          <span>Fit</span>
        </button>
      </div>
    </div>
  );
}
