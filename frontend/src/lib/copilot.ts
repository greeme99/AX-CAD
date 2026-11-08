// Track 9 Q1: AI Copilot client types and helpers (plan.md §15.2).

export type Page = "home" | "project" | "document" | "cad2d" | "model3d" | "quote" | "admin" | "other";
export type CopilotContext = { page: Page; document_id?: number; revision_id?: string; quote_id?: number };
export type Severity = "ERROR" | "WARN" | "INFO";
export type Target = { kind: "feature" | "line" | "entity"; id: number | string };
export type Finding = { severity: Severity; code: string; message: string; fix: string; target: Target | null };
export type Findings = { document_type: string | null; findings: Finding[]; counts: Record<Severity, number> };
export type Topic = { id: string; q: string; a: string };
export type Action =
  | { kind: "cad2d"; inputs: string[]; summary: string }
  | { kind: "feature"; feature_type: string; params: Record<string, unknown>; summary: string }
  | { kind: "dialog"; dialog: string; values: Record<string, unknown>; summary: string };
export type Answer = { answer: string; source: "RULE" | "KB" | "LLM" | "NONE"; model?: string | null; actions: Action[]; findings: Finding[]; related: Topic[] };
export type Settings = {
  llm_enabled: boolean;
  llm_configured: boolean;
  key_source: "env" | "saved" | null;
  provider: string;
  model: string;
  // absent while an older API (2.0.x) is still running after an update (BUG-61)
  gemini_models?: string[];
  ollama_enabled?: boolean;
  ollama_url?: string;
  ollama_models?: string[];
};

/** What a page can do with a proposal; a page that cannot run a kind leaves it out. */
export type Handlers = {
  cad2d?: (inputs: string[]) => string | null; // null = done, else the error to show
  feature?: (featureType: string, params: Record<string, unknown>) => Promise<string | null>;
  dialog?: (dialog: string, values: Record<string, unknown>) => void;
  target?: (t: Target) => void;
  /** Track 11: run an AX-LISP program (and then one of its c: commands); null error = applied */
  lisp?: (src: string, command?: string) => Promise<LispRun>;
};
export type LispRun = { error: string | null; log: string[]; commands: string[]; applied: number };

export const OPEN_KEY = "axcad.copilot.open";
export const SOURCE_LABEL: Record<Answer["source"], string> = { RULE: "규칙", KB: "도움말", LLM: "LLM", NONE: "답 없음" };
export const SEVERITY_LABEL: Record<Severity, string> = { ERROR: "오류", WARN: "경고", INFO: "참고" };

/** Saved open state; storage can be blocked (private window, previews): default closed. */
export function loadOpen(store: Pick<Storage, "getItem"> | undefined): boolean {
  try {
    return store?.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveOpen(store: Pick<Storage, "setItem"> | undefined, open: boolean) {
  try {
    store?.setItem(OPEN_KEY, open ? "1" : "0");
  } catch {
    // remembering is a convenience only
  }
}

/** Ctrl+. (or Cmd+. on macOS) toggles the panel, wherever the focus is. */
export const isToggleKey = (e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">) =>
  e.key === "." && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey;

/** Query string for the findings of a context, or null when there is nothing to check. */
export function findingsPath(c: CopilotContext): string | null {
  if (c.quote_id) return `/copilot/findings?quote_id=${c.quote_id}`;
  if (c.revision_id) return `/copilot/findings?revision_id=${encodeURIComponent(c.revision_id)}`;
  if (c.document_id) return `/copilot/findings?document_id=${c.document_id}`;
  return null;
}

/** Whether a page registered a handler that can run this proposal. */
export function runnable(a: Action, h: Handlers): boolean {
  return a.kind === "cad2d" ? !!h.cad2d : a.kind === "feature" ? !!h.feature : !!h.dialog;
}

export const whereToRun: Record<Action["kind"], string> = {
  cad2d: "2D 도면 편집 화면에서 실행할 수 있습니다",
  feature: "3D 모델 화면에서 실행할 수 있습니다",
  dialog: "3D 모델 화면에서 열 수 있습니다",
};

/** The page context from the URL, so business pages need no wiring (a 2D viewer passes ?doc=). */
export function contextFromPath(path: string): CopilotContext {
  const seg = path.split("?")[0].split("/").filter(Boolean);
  const id = (s?: string) => (s && /^\d{1,18}$/.test(s) ? Number(s) : undefined);
  switch (seg[0]) {
    case undefined:
      return { page: "home" };
    case "projects":
      return { page: "project" };
    case "documents":
      return { page: "document", document_id: id(seg[1]) };
    case "model":
      return { page: "model3d", document_id: id(seg[1]) };
    case "viewer":
      return { page: "cad2d", revision_id: seg[1] && /^[0-9a-f]{32}$/.test(seg[1]) ? seg[1] : undefined };
    case "quotes":
      return { page: "quote", quote_id: id(seg[1]) };
    case "admin":
      return { page: "admin" };
    default:
      return { page: "other" };
  }
}
