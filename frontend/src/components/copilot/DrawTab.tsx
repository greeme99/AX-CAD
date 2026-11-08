"use client";

import { useState } from "react";
import { btn2, btnPrimary, field } from "@/components/ui";
import { api, errText } from "@/lib/api";
import type { Action, LispRun } from "@/lib/copilot";
import { useCopilot } from "./CopilotProvider";

// Track 11 L3: Text to Draw — a sentence becomes AX-LISP (rules, else the LLM when it is on),
// shown as code to check or edit, then run on the 2D screen as one undo step

type Draft = { source: "RULE" | "LLM" | "NONE"; lisp?: string | null; summary?: string | null; error?: string | null; action?: Action; model?: string | null };
const EXAMPLES = {
  cad2d: ["100x50 사각형과 가운데 지름 20 원", "200x100 판 구멍 지름 10 모서리에서 15", "플랜지 외경 150 내경 50 구멍 14 8개 PCD 110", "선택한 것 오른쪽으로 50 이동"],
  model3d: ["100x50x20 상자 그리고 위에 지름 20 높이 30 원통", "반지름 20 높이 40 원뿔, 위에 반지름 10 구", "M8 탭 구멍 4개", "M10x30 볼트 8개"],
};

export default function DrawTab() {
  const cp = useCopilot()!;
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<LispRun | null>(null);
  const run = cp.handlers.current?.lisp;
  const page = cp.context.page === "model3d" ? "model3d" : "cad2d";

  const make = async (q: string) => {
    if (!q.trim() || busy) return;
    setBusy(true);
    setRes(null);
    try {
      const d = await api<Draft>("/copilot/draw", { method: "POST", json: { text: q.trim(), page } });
      setDraft(d);
      setCode(d.lisp ?? "");
    } catch (e) {
      setDraft({ source: "NONE", lisp: null, summary: null, error: errText(e) });
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!run || busy) return;
    setBusy(true);
    try {
      setRes(await run(code));
    } finally {
      setBusy(false);
    }
  };
  // a 3D proposal that is not a primitive (hole wizard, standard part, sheet metal): the page runs it
  const act = async (a: Action) => {
    const h = cp.handlers.current ?? {};
    let error: string | null = null;
    try {
      if (a.kind === "feature" && h.feature) error = await h.feature(a.feature_type, a.params);
      else if (a.kind === "dialog" && h.dialog) h.dialog(a.dialog, a.values);
    } catch (e) {
      error = errText(e);
    }
    setRes({ error, log: [], commands: [], applied: error ? 0 : 1 });
  };

  return (
    <div className="space-y-2 p-3 text-sm">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void make(text);
        }}
      >
        <label htmlFor="draw-text" className="sr-only">
          그릴 것
        </label>
        <input id="draw-text" value={text} maxLength={500} onChange={(e) => setText(e.target.value)} placeholder="그릴 것을 문장으로 입력하세요" className={field} autoComplete="off" />
        <button type="submit" className={`${btnPrimary} shrink-0`} disabled={busy || !text.trim()}>
          만들기
        </button>
      </form>
      {!draft && (
        <ul className="space-y-1 text-xs">
          {EXAMPLES[page].map((x) => (
            <li key={x}>
              <button
                type="button"
                className="text-left text-[var(--color-primary)] hover:underline"
                onClick={() => {
                  setText(x);
                  void make(x);
                }}
              >
                {x}
              </button>
            </li>
          ))}
        </ul>
      )}
      {draft?.error && <p className="text-red-600">{draft.error}</p>}
      {draft?.action && (
        <div className="space-y-2 rounded-md bg-[var(--color-bg-subtle)] p-2">
          <p className="text-xs font-medium text-foreground">미리보기 (규칙): {draft.summary}</p>
          <button type="button" className={btnPrimary} disabled={busy} onClick={() => void act(draft.action!)}>
            {draft.action.kind === "dialog" ? "열기" : "실행"}
          </button>
          {res && <p role="status" className={`text-xs ${res.error ? "text-red-600" : "text-[var(--color-success-text)]"}`}>{res.error ? `실행 실패: ${res.error}` : draft.action.kind === "dialog" ? "창을 열었습니다" : "실행했습니다"}</p>}
        </div>
      )}
      {draft?.lisp != null && (
        <div className="space-y-2 rounded-md bg-[var(--color-bg-subtle)] p-2">
          <p className="text-xs font-medium text-foreground">
            미리보기 ({draft.source === "LLM" ? `LLM${draft.model ? ` · ${draft.model}` : ""}` : "규칙"}): {draft.summary}
          </p>
          <label htmlFor="draw-code" className="sr-only">
            만든 LISP
          </label>
          <textarea id="draw-code" value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} className={`${field} h-40 font-mono text-xs`} />
          <div className="flex gap-2">
            <button type="button" className={btnPrimary} disabled={!run || busy || !code.trim()} onClick={() => void apply()}>
              실행
            </button>
            <button type="button" className={btn2} onClick={() => setDraft(null)}>
              다시 쓰기
            </button>
          </div>
          {!run && <p className="text-xs text-muted-foreground">도면 편집(2D) 또는 3D 모델 화면에서 실행할 수 있습니다.</p>}
          {res && <p role="status" className={`text-xs ${res.error ? "text-red-600" : "text-[var(--color-success-text)]"}`}>{res.error ? `실행 실패: ${res.error} (도면은 바뀌지 않았습니다)` : `실행했습니다 · 변경 ${res.applied}건`}</p>}
        </div>
      )}
    </div>
  );
}
