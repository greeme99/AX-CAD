// Pure-TS helpers for Photo-to-3D scale calibration, turntable angles, and request validation.

export type PhotoMode = "ORTHO_MULTI" | "TURNTABLE" | "FREE_ANGLE";
export type OutputMode = "HYBRID" | "PARAMETRIC" | "VISUAL_HULL";
export type ViewLabel = "FRONT" | "BACK" | "LEFT" | "RIGHT" | "SIDE" | "TOP" | "BOTTOM" | "ANGLE";

export const MAX_PHOTO3D_FILES = 36;
export const MAX_PHOTO3D_MB = 15;

export function pixelDistance(p1: [number, number], p2: [number, number]): number {
  return Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
}

export function mmPerPixel(referenceMm: number, p1: [number, number], p2: [number, number]): number | null {
  if (!Number.isFinite(referenceMm) || referenceMm <= 0) return null;
  const d = pixelDistance(p1, p2);
  return d >= 2 ? referenceMm / d : null;
}

export function defaultViewLabel(mode: PhotoMode, index: number): ViewLabel {
  if (mode === "ORTHO_MULTI") {
    const seq: ViewLabel[] = ["FRONT", "SIDE", "TOP", "BACK", "LEFT", "BOTTOM"];
    return seq[index] ?? "ANGLE";
  }
  return "ANGLE";
}

export function uniformTurntableAngles(count: number): number[] {
  if (count <= 0) return [];
  return Array.from({ length: count }, (_, i) => Math.round(((360 * i) / count) * 10) / 10);
}

export function validatePhoto3dInputs(args: {
  fileCount: number;
  mode: PhotoMode;
  referenceMm: number;
}): string | null {
  if (args.fileCount < 1) return "사진을 1장 이상 선택하세요.";
  if (args.fileCount > MAX_PHOTO3D_FILES) return `최대 ${MAX_PHOTO3D_FILES}장까지 업로드할 수 있습니다.`;
  if (args.mode === "TURNTABLE" && args.fileCount < 4) {
    return "연속 턴테이블 복원은 최소 4장 이상의 연속 사진이 필요합니다.";
  }
  if (!Number.isFinite(args.referenceMm) || args.referenceMm <= 0 || args.referenceMm > 1e6) {
    return "기준 치수(mm)는 0보다 큰 숫자를 입력하세요.";
  }
  return null;
}
