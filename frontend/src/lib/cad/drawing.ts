/** Track 8 P3: the "도면 만들기" form (DrawingDialog) and the POST /documents/{id}/drawing body. */

export const VIEWS = [
  ["FRONT", "정면도"],
  ["TOP", "평면도"],
  ["RIGHT", "우측면도"],
  ["ISO", "등각도"],
] as const;
export const PAPERS = ["A4", "A3", "A2", "A1", "A0"] as const;
export const TOLERANCE = { f: "f 정밀", m: "m 보통", c: "c 거침", v: "v 매우 거침" } as const; // KS B ISO 2768-1
export const ROUGHNESS = ["0.8", "1.6", "3.2", "6.3", "12.5", "25"]; // backend ROUGHNESS (KS B ISO 1302)
const SCALE = /^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$/; // "1:2", "5:1"
export const MAX_EXTRA = 6; // sections / details each (backend MAX_EXTRA_VIEWS)

type View = (typeof VIEWS)[number][0];
export type Section = { axis: "X" | "Y" | "Z"; offset: string };
export type Detail = { view: "FRONT" | "TOP" | "RIGHT"; x: string; y: string; r: string; factor: string };
export type DrawingDraft = {
  views: View[];
  projection: "THIRD" | "FIRST";
  sheet: (typeof PAPERS)[number];
  portrait: boolean;
  scale: string; // empty = fit the sheet
  sections: Section[];
  details: Detail[];
  dimensions: boolean;
  tolerance: keyof typeof TOLERANCE;
  roughness: string; // empty = no note
  material: string;
};
export const DRAWING_DRAFT: DrawingDraft = {
  views: ["FRONT", "TOP", "RIGHT", "ISO"],
  projection: "THIRD",
  sheet: "A3",
  portrait: false,
  scale: "",
  sections: [],
  details: [],
  dimensions: true,
  tolerance: "m",
  roughness: "",
  material: "",
};

/** The POST /drawing body, or what is wrong with the draft. Sections take labels A, B, ... and
 *  details the letters after them (KS B 0001: one letter per view). */
export function drawingRequest(d: DrawingDraft): Record<string, unknown> | string {
  if (!d.views.length) return "투상도를 하나 이상 고르세요";
  const m = SCALE.exec(d.scale);
  if (d.scale.trim() && (!m || !Number(m[1]) || !Number(m[2]))) return "축척은 1:2, 5:1처럼 입력하세요 (비우면 자동)";
  const num = (s: string) => (s.trim() === "" ? NaN : Number(s));
  const letter = (i: number) => String.fromCharCode(65 + i);
  const sections = d.sections.map((s, i) => ({ label: letter(i), axis: s.axis, offset: num(s.offset) }));
  if (sections.some((s) => !Number.isFinite(s.offset))) return "단면 위치(mm)를 숫자로 입력하세요";
  const details = d.details.map((x, i) => ({ label: letter(sections.length + i), view: x.view, center: [num(x.x), num(x.y)], radius: num(x.r), factor: num(x.factor) }));
  if (details.some((x) => !x.center.every(Number.isFinite) || !(x.radius > 0) || !(x.factor > 1 && x.factor <= 10))) return "상세도: 중심·반지름(>0)·배율(1 초과 10 이하)을 숫자로 입력하세요";
  if (details.some((x) => !d.views.includes(x.view))) return "상세도의 원 뷰가 투상도에 포함되어야 합니다";
  return {
    views: VIEWS.map(([v]) => v).filter((v) => d.views.includes(v)),
    projection: d.projection,
    sheet: d.sheet,
    portrait: d.portrait,
    scale: m ? Number(m[1]) / Number(m[2]) : null,
    sections,
    details,
    dimensions: d.dimensions,
    tolerance: d.tolerance,
    roughness: d.roughness ? Number(d.roughness) : null, // null clears a stored note
    material: d.material.trim() || null,
  };
}
