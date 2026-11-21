/**
 * AX-CAD Design Tokens (Single Source of Truth)
 * Generated based on ui-ux-pro-max and DESIGN.md specification
 */

import tokensJson from "./design-tokens.json";

export const tokens = tokensJson.tokens;

export type ThemeMode = "light" | "dark";

export type WorkbenchMode =
  | "sketch"
  | "model"
  | "surface"
  | "assembly"
  | "sheetmetal"
  | "cam"
  | "drawing";

export const WORKBENCH_META: Record<
  WorkbenchMode,
  { label: string; accent: string; lightBg: string }
> = {
  sketch: {
    label: "스케치",
    accent: tokens.color.workbenches.sketch.accent,
    lightBg: tokens.color.workbenches.sketch.light,
  },
  model: {
    label: "3D 모델링",
    accent: tokens.color.workbenches.model.accent,
    lightBg: tokens.color.workbenches.model.light,
  },
  surface: {
    label: "자유곡면",
    accent: tokens.color.workbenches.surface.accent,
    lightBg: tokens.color.workbenches.surface.light,
  },
  assembly: {
    label: "어셈블리",
    accent: tokens.color.workbenches.assembly.accent,
    lightBg: tokens.color.workbenches.assembly.light,
  },
  sheetmetal: {
    label: "판금",
    accent: tokens.color.workbenches.sheetmetal.accent,
    lightBg: tokens.color.workbenches.sheetmetal.light,
  },
  cam: {
    label: "CAM 공구경로",
    accent: tokens.color.workbenches.cam.accent,
    lightBg: tokens.color.workbenches.cam.light,
  },
  drawing: {
    label: "2D 도면/치수",
    accent: tokens.color.workbenches.drawing.accent,
    lightBg: tokens.color.workbenches.drawing.light,
  },
};
