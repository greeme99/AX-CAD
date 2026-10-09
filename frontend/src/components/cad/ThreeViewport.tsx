"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { fitDistance, meshBuffers, unionBBox, VIEW_DIRS, type BBox, type MeshJson, type ViewName } from "@/lib/cad/mesh";

type Props = {
  meshes: { id: number; mesh: MeshJson }[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
};

const FOV = 45;
const VIEWS: [ViewName, string][] = [["front", "정면"], ["top", "평면"], ["right", "측면"], ["iso", "등각"]];
const GREY = 0x9aa3af;
const token = (el: Element, name: string, fallback: string) => getComputedStyle(el).getPropertyValue(name).trim() || fallback;

type Ctx = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  group: THREE.Group;
  draw: () => void;
  frame: (dir?: readonly [number, number, number]) => void;
  bbox: BBox | null;
};

export default function ThreeViewport({ meshes, selectedId, onSelect }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const ctx = useRef<Ctx | null>(null);
  const selRef = useRef({ selectedId, onSelect });
  selRef.current = { selectedId, onSelect };

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

    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 10000);
    camera.up.set(0, 0, 1); // Z up, set before the controls read it
    camera.position.set(100, -100, 100);
    const controls = new OrbitControls(camera, canvas);
    controls.listenToKeyEvents(canvas);
    const draw = () => renderer.render(scene, camera);
    controls.addEventListener("change", draw); // render on demand, no idle loop

    const c: Ctx = {
      renderer, scene, camera, controls, group, draw, bbox: null,
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
    const onDown = (e: PointerEvent) => (down = [e.clientX, e.clientY]);
    const onUp = (e: PointerEvent) => {
      if (!down || e.button !== 0 || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
      const r = canvas.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
      const hit = ray.intersectObjects(group.children, false)[0];
      selRef.current.onSelect(hit ? (hit.object.userData.id as number) : null);
    };
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerup", onUp);

    return () => {
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
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
      m.add(new THREE.LineSegments(new THREE.EdgesGeometry(g, 20), new THREE.LineBasicMaterial({ color: 0x1f2937 })));
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

  const btn = "rounded-md border border-line bg-card px-3 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring";
  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden bg-canvas">
      <div className="absolute bottom-3 left-3 flex gap-2">
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
