// Track 11 L4: AX-LISP on the 3D screen — (command "BOX" l w h [origin] [axis]) and friends become
// PRIMITIVE features (the same parameters as the primitive dialog), checked before any is created.
import type { Segment } from "./lisp";

export type Primitive = { kind: "BOX" | "CYLINDER" | "SPHERE" | "CONE" | "TORUS"; length?: number; width?: number; height?: number; radius?: number; radius2?: number; origin: [number, number, number]; axis: "X" | "Y" | "Z" };
const ARGS: Record<Primitive["kind"], (keyof Primitive)[]> = {
  BOX: ["length", "width", "height"],
  CYLINDER: ["radius", "height"],
  SPHERE: ["radius"],
  CONE: ["radius", "radius2", "height"],
  TORUS: ["radius", "radius2"],
};
const ALIAS: Record<string, Primitive["kind"]> = { BOX: "BOX", CYLINDER: "CYLINDER", CYL: "CYLINDER", SPHERE: "SPHERE", CONE: "CONE", TORUS: "TORUS" };
export const MAX_PRIMITIVES = 50;

/** The run's commands as primitives, or the first problem (nothing is created then). */
export function primitives(segments: Segment[]): Primitive[] | string {
  const tokens = segments.flatMap((s) => s.inputs);
  const out: Primitive[] = [];
  let i = 0;
  while (i < tokens.length) {
    const name = tokens[i++].toUpperCase();
    if (name === "") continue;
    const kind = ALIAS[name];
    if (!kind) return `3D 화면에서 쓸 수 없는 명령: ${name} (BOX·CYLINDER·SPHERE·CONE·TORUS)`;
    const p: Primitive = { kind, origin: [0, 0, 0], axis: "Z" };
    for (const k of ARGS[kind]) {
      const v = Number(tokens[i++]);
      const min = k === "radius2" && kind === "CONE" ? 0 : Number.MIN_VALUE;
      if (!Number.isFinite(v) || v < min || v > 100_000) return `${kind}: ${k} 값이 잘못되었습니다 (${tokens[i - 1] ?? "없음"})`;
      (p as Record<string, unknown>)[k] = v;
    }
    if (i < tokens.length && /^-?[\d.]+,-?[\d.]+(?:,-?[\d.]+)?$/.test(tokens[i])) {
      const [x, y, z = 0] = tokens[i++].split(",").map(Number);
      p.origin = [x, y, z];
    }
    if (i < tokens.length && /^[XYZ]$/i.test(tokens[i])) p.axis = tokens[i++].toUpperCase() as Primitive["axis"];
    out.push(p);
    if (out.length > MAX_PRIMITIVES) return `한 번에 ${MAX_PRIMITIVES}개까지 만들 수 있습니다`;
  }
  return out;
}
