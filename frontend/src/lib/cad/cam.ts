import * as THREE from "three";

export interface CamSegment {
  kind: "RAPID" | "CUT" | "PLUNGE" | "ARC_CW" | "ARC_CCW";
  start: [number, number, number];
  end: [number, number, number];
  center?: [number, number];
  feed?: number;
}

export interface CamToolpathData {
  setup_id: number;
  kind: "MILL_25D" | "LASER_2D";
  segment_count: number;
  cut_length_mm: number;
  rapid_length_mm: number;
  cycle_time_s: number;
  segments: CamSegment[];
}

export interface CamOverlayHandle {
  group: THREE.Group;
  dispose: () => void;
}

/**
 * Build Three.js toolpath simulation overlay (solid cut lines + dashed rapid lines + tool marker)
 * with guaranteed BufferGeometry & Material disposal on cleanup/recompute.
 */
export function buildCamToolpathOverlay(
  segments: CamSegment[],
  progressPct: number,
): CamOverlayHandle {
  const group = new THREE.Group();
  group.name = "cam-toolpath-overlay";

  const clamped = Math.max(0, Math.min(100, progressPct));
  const visibleCount = Math.round((segments.length * clamped) / 100);
  const activeSegments = segments.slice(0, visibleCount);

  const cutPositions: number[] = [];
  const rapidPositions: number[] = [];
  let markerPos: [number, number, number] | null = null;

  for (const seg of activeSegments) {
    const [sx, sy, sz] = seg.start;
    const [ex, ey, ez] = seg.end;
    if (seg.kind === "RAPID") {
      rapidPositions.push(sx, sy, sz, ex, ey, ez);
    } else {
      cutPositions.push(sx, sy, sz, ex, ey, ez);
    }
    markerPos = seg.end;
  }

  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];

  if (cutPositions.length > 0) {
    const cutGeom = new THREE.BufferGeometry();
    cutGeom.setAttribute("position", new THREE.Float32BufferAttribute(cutPositions, 3));
    const cutMat = new THREE.LineBasicMaterial({ color: 0x2563eb });
    geometries.push(cutGeom);
    materials.push(cutMat);
    group.add(new THREE.LineSegments(cutGeom, cutMat));
  }

  if (rapidPositions.length > 0) {
    const rapidGeom = new THREE.BufferGeometry();
    rapidGeom.setAttribute("position", new THREE.Float32BufferAttribute(rapidPositions, 3));
    const rapidMat = new THREE.LineDashedMaterial({
      color: 0xd97706,
      dashSize: 3,
      gapSize: 2,
    });
    const rapidLines = new THREE.LineSegments(rapidGeom, rapidMat);
    rapidLines.computeLineDistances();
    geometries.push(rapidGeom);
    materials.push(rapidMat);
    group.add(rapidLines);
  }

  if (markerPos) {
    const markerGeom = new THREE.ConeGeometry(2.5, 8, 12);
    markerGeom.rotateX(-Math.PI / 2);
    const markerMat = new THREE.MeshBasicMaterial({ color: 0xdc2626 });
    const markerMesh = new THREE.Mesh(markerGeom, markerMat);
    markerMesh.position.set(markerPos[0], markerPos[1], markerPos[2] + 4);
    geometries.push(markerGeom);
    materials.push(markerMat);
    group.add(markerMesh);
  }

  return {
    group,
    dispose: () => {
      for (const g of geometries) g.dispose();
      for (const m of materials) m.dispose();
      group.clear();
    },
  };
}
