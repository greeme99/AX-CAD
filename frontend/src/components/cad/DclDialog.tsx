"use client";

import { useState } from "react";
import { btn2, btnPrimary, field, Modal } from "@/components/ui";
import type { Tile } from "@/lib/cad/dcl";
import type { DialogRun, DialogState } from "@/lib/cad/lisp";

// 2D automation A1: draws an AX-LISP DCL dialog (start_dialog). Each change runs the tile's
// action_tile code through act(); the run says when done_dialog closes it.
export default function DclDialog({ run, onDone, onError }: { run: DialogRun; onDone: (status: number) => void; onError: (e: unknown) => void }) {
  const [state, setState] = useState<DialogState>(run.state);
  const [busy, setBusy] = useState(false);
  const off = (key?: string) => busy || (key !== undefined && state.disabled.includes(key));

  const act = async (key: string | undefined, value: string) => {
    if (!key) return;
    setBusy(true);
    try {
      const r = await run.act(key, value);
      setState(r.state);
      if (r.done !== null) onDone(r.done);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const local = (key: string | undefined, value: string) => key && setState((s) => ({ ...s, values: { ...s.values, [key]: value } }));
  // tile keys come from the program: only the state's own keys count ("constructor" is just a key)
  const v = (key?: string) => (key && Object.hasOwn(state.values, key) ? state.values[key] : "");

  const render = (t: Tile, i: number, group?: Tile): React.ReactNode => {
    const label = t.label ?? "";
    switch (t.type) {
      case "row":
      case "column":
      case "concatenation":
      case "paragraph":
      case "radio_row":
      case "radio_column":
        return (
          <div key={i} className={`flex gap-2 ${t.type.includes("row") || t.type === "concatenation" ? "flex-row flex-wrap items-end" : "flex-col"}`}>
            {label && <span className="text-sm font-medium text-foreground">{label}</span>}
            {t.children.map((c, j) => render(c, j, t.type.startsWith("radio") ? t : undefined))}
          </div>
        );
      case "boxed_row":
      case "boxed_column":
      case "boxed_radio_row":
      case "boxed_radio_column":
        return (
          <fieldset key={i} className={`flex gap-2 rounded-md border border-line p-2 ${t.type.includes("row") ? "flex-row flex-wrap items-end" : "flex-col"}`}>
            {label && <legend className="px-1 text-sm font-medium text-foreground">{label}</legend>}
            {t.children.map((c, j) => render(c, j, t.type.includes("radio") ? t : undefined))}
          </fieldset>
        );
      case "text":
      case "text_part":
        return (
          <p key={i} className="text-sm text-body">
            {label || v(t.key)}
          </p>
        );
      case "errtile":
        return (
          <p key={i} role="alert" className="min-h-5 text-sm text-red-600 dark:text-red-400">
            {v(t.key ?? "error")}
          </p>
        );
      case "edit_box":
        return (
          <label key={i} className="flex flex-col gap-1 text-sm">
            {label}
            <input className={field} value={v(t.key)} disabled={off(t.key)} onChange={(e) => local(t.key, e.target.value)} onBlur={(e) => void act(t.key, e.target.value)} />
          </label>
        );
      case "toggle":
        return (
          <label key={i} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={v(t.key) === "1"} disabled={off(t.key)} onChange={(e) => void act(t.key, e.target.checked ? "1" : "0")} />
            {label}
          </label>
        );
      case "radio_button":
        return (
          <label key={i} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name={group?.key ?? "dcl-radio"}
              checked={group?.key ? v(group.key) === t.key : v(t.key) === "1"}
              disabled={off(t.key)}
              onChange={async () => {
                if (group?.key && t.key) await act(group.key, t.key);
                await act(t.key, "1");
              }}
            />
            {label}
          </label>
        );
      case "list_box":
      case "popup_list": {
        const items = t.key && Object.hasOwn(state.lists, t.key) ? state.lists[t.key] : [];
        return (
          <label key={i} className="flex flex-col gap-1 text-sm">
            {label}
            <select className={field} size={t.type === "list_box" ? Math.min(8, Math.max(3, items.length)) : undefined} value={v(t.key)} disabled={off(t.key)} onChange={(e) => void act(t.key, e.target.value)}>
              {items.map((it, j) => (
                <option key={j} value={String(j)}>
                  {it}
                </option>
              ))}
            </select>
          </label>
        );
      }
      case "slider":
        return (
          <label key={i} className="flex flex-col gap-1 text-sm">
            {label}
            <input type="range" min={Number(t.attrs.min_value ?? 0)} max={Number(t.attrs.max_value ?? 10000)} value={Number(v(t.key) || 0)} disabled={off(t.key)} onChange={(e) => void act(t.key, e.target.value)} />
          </label>
        );
      case "button":
      case "image_button":
        return (
          <button key={i} type="button" className={t.attrs.is_default ? btnPrimary : btn2} disabled={off(t.key)} onClick={() => void act(t.key, "")}>
            {label || t.key}
          </button>
        );
      case "image":
        return <div key={i} className="h-12 w-24 rounded border border-dashed border-line" aria-hidden />;
      default: // spacer
        return <div key={i} className="h-2" aria-hidden />;
    }
  };

  // Esc / closing always ends the dialog: the cancel action runs, then it closes whatever it did
  const cancel = async () => {
    try {
      const r = await run.act("cancel", "");
      onDone(r.done ?? 0);
    } catch (e) {
      onError(e);
    }
  };
  return (
    <Modal title={run.tile.label ?? "AX-LISP 대화상자"} onClose={() => void cancel()}>
      <div className="flex flex-col gap-3">{run.tile.children.map((c, i) => render(c, i))}</div>
    </Modal>
  );
}
