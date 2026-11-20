import * as THREE from "three";

export type CaeDisplayMode = "von_mises" | "displacement" | "safety_factor";

export interface CaeResultData {
  study_id: number;
  node_count: number;
  element_count: number;
  max_disp_mm: number;
  max_von_mises_mpa: number;
  min_safety_factor: number;
  nodes: [number, number, number][];
  elements: [number, number, number, number][];
  displacements_mm: [number, number, number][];
  von_mises_mpa: number[];
  safety_factor: number[];
}

export interface CaeOverlayHandle {
  group: THREE.Group;
  minVal: number;
  maxVal: number;
  dispose: () => void;
}

/**
 * Blue -> Cyan -> Green -> Yellow -> Red engineering colormap for normalized t in [0, 1].
 * For safety_factor, invert=true maps low safety factor to red and high safety factor to blue/green.
 */
export function caeColorRamp(t: number, invert = false): [number, number, number] {
  const u = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  const x = invert ? 1 - u : u;
  if (x < 0.25) {
    const s = x / 0.25;
    return [0, s, 1];
  }
  if (x < 0.5) {
    const s = (x - 0.25) / 0.25;
    return [0, 1, 1 - s];
  }
  if (x < 0.75) {
    const s = (x - 0.5) / 0.25;
    return [s, 1, 0];
  }
  const s = (x - 0.75) / 0.25;
  return [1, 1 - s, 0];
}

export function buildCaeResultOverlay(
  result: CaeResultData,
  mode: CaeDisplayMode,
  deformScale: number,
): CaeOverlayHandle {
  const group = new THREE.Group();
  group.name = "cae-result-overlay";
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];

  const scale = Math.max(0, Math.min(50, deformScale));
  const nNodes = result.nodes.length;
  if (nNodes === 0) {
    return { group, minVal: 0, maxVal: 0, dispose: () => group.clear() };
  }

  const values: number[] = new Array(nNodes);
  for (let i = 0; i < nNodes; i++) {
    if (mode === "von_mises") {
      values[i] = result.von_mises_mpa[i] ?? 0;
    } else if (mode === "displacement") {
      const d = result.displacements_mm[i] ?? [0, 0, 0];
      values[i] = Math.hypot(d[0], d[1], d[2]);
    } else {
      values[i] = result.safety_factor[i] ?? 999;
    }
  }

  let minVal = Infinity;
  let maxVal = -Infinity;
  let minIdx = 0;
  let maxIdx = 0;
  for (let i = 0; i < nNodes; i++) {
    const v = values[i];
    if (v < minVal) {
      minVal = v;
      minIdx = i;
    }
    if (v > maxVal) {
      maxVal = v;
      maxIdx = i;
    }
  }
  if (!Number.isFinite(minVal)) minVal = 0;
  if (!Number.isFinite(maxVal)) maxVal = 0;
  const span = Math.max(1e-9, maxVal - minVal);
  const invert = mode === "safety_factor";

  const positions = new Float32Array(nNodes * 3);
  const colors = new Float32Array(nNodes * 3);
  for (let i = 0; i < nNodes; i++) {
    const [x, y, z] = result.nodes[i];
    const [ux, uy, uz] = result.displacements_mm[i] ?? [0, 0, 0];
    positions[3 * i] = x + ux * scale;
    positions[3 * i + 1] = y + uy * scale;
    positions[3 * i + 2] = z + uz * scale;

    const [r, g, b] = caeColorRamp((values[i] - minVal) / span, invert);
    colors[3 * i] = r;
    colors[3 * i + 1] = g;
    colors[3 * i + 2] = b;
  }

  const ptsGeom = new THREE.BufferGeometry();
  ptsGeom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  ptsGeom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const ptsMat = new THREE.PointsMaterial({ size: 4, vertexColors: true });
  geometries.push(ptsGeom);
  materials.push(ptsMat);
  group.add(new THREE.Points(ptsGeom, ptsMat));

  // Max and Min markers
  const maxGeom = new THREE.SphereGeometry(2.2, 12, 12);
  const maxMat = new THREE.MeshBasicMaterial({ color: 0xdc2626 });
  const maxMesh = new THREE.Mesh(maxGeom, maxMat);
  maxMesh.position.set(
    positions[3 * maxIdx],
    positions[3 * maxIdx + 1],
    positions[3 * maxIdx + 2],
  );
  geometries.push(maxGeom);
  materials.push(maxMat);
  group.add(maxMesh);

  const minGeom = new THREE.SphereGeometry(2.2, 12, 12);
  const minMat = new THREE.MeshBasicMaterial({ color: 0x2563eb });
  const minMesh = new THREE.Mesh(minGeom, minMat);
  minMesh.position.set(
    positions[3 * minIdx],
    positions[3 * minIdx + 1],
    positions[3 * minIdx + 2],
  );
  geometries.push(minGeom);
  materials.push(minMat);
  group.add(minMesh);

  return {
    group,
    minVal,
    maxVal,
    dispose: () => {
      for (const g of geometries) g.dispose();
      for (const m of materials) m.dispose();
      group.clear();
    },
  };
}
