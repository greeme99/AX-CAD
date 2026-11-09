"use client";

import { useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { MAX_SCRIPT_BYTES } from "@/lib/cad/axs";
import type { Geom } from "@/lib/cad/geom";
import { useApi } from "@/lib/useApi";

// Track 9 Q4: .axs command scripts and parametric blocks in the 2D editor (plan.md §15.5)

// A4: opened with a finished command journal (initial), which can be edited, saved as a file and run
export function ScriptDialog({ initial, fileName = "script.axs", onRun, onLibrary, onClose }: { initial?: string; fileName?: string; onRun: (text: string) => string | null; onLibrary?: (name: string, source: string) => Promise<string>; onClose: () => void }) {
  const [text, setText] = useState(initial ?? "; 예: 사각형과 구멍\nREC\n0,0\n@100,50\nC\n20,25\n4\n");
  const [error, setError] = useState<string | null>(null);
  const [libName, setLibName] = useState(fileName.replace(/\.axs$/i, ""));
  const [saved, setSaved] = useState<string | null>(null);
  const toLibrary = async () => {
    setError(null);
    try {
      setSaved(`라이브러리에 저장: ${await onLibrary!(libName, text)} (명령행: SCRIPT ${libName.replace(/\.axs$/i, "")})`);
    } catch (e) {
      setSaved(null);
      setError(errText(e));
    }
  };
  const saveFile = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName.replace(/[\\/:*?"<>|]/g, "_");
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000); // after the browser has started the download
  };
  const load = (f: File | undefined) => {
    if (!f) return;
    if (f.size > MAX_SCRIPT_BYTES) return setError("스크립트가 너무 큽니다 (1 MB 이하)");
    f.text().then(setText, (e) => setError(errText(e)));
  };
  return (
    <Modal title={initial ? "명령 기록 (.axs)" : "명령 스크립트 (.axs)"} onClose={onClose} wide>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const err = onRun(text);
          if (err) setError(err);
          else onClose();
        }}
      >
        <p className="text-sm text-muted-foreground">한 줄에 명령 프롬프트 입력 하나 (빈 줄 = Enter, ^C = 취소, ; 로 시작하면 주석). 전체가 한 번의 실행 취소로 적용되고, 오류가 나면 그 줄을 알려 주고 아무것도 바꾸지 않습니다.</p>
        <Field label="스크립트 파일 열기">
          <input type="file" accept=".axs,.scr,.txt" className="text-sm" onChange={(e) => load(e.target.files?.[0])} />
        </Field>
        <label htmlFor="axs-text" className="sr-only">
          스크립트
        </label>
        <textarea id="axs-text" className={`${field} h-64 font-mono`} value={text} spellCheck={false} onChange={(e) => setText(e.target.value)} />
        <Err text={error} />
        {onLibrary && (
          <div className="flex flex-wrap items-end gap-2">
            <Field label="라이브러리 이름 (.axs)">
              <input className={field} value={libName} maxLength={56} onChange={(e) => setLibName(e.target.value)} />
            </Field>
            <button type="button" className={btn2} onClick={() => void toLibrary()}>
              라이브러리에 저장
            </button>
            {saved && (
              <span role="status" className="text-sm text-muted-foreground">
                {saved}
              </span>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            {initial ? "닫기" : "취소"}
          </button>
          <button type="button" className={btn2} onClick={saveFile}>
            파일로 저장 (.axs)
          </button>
          <button type="submit" className={btnPrimary}>
            {initial ? "이 도면에 실행" : "실행"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type Param = { name: string; label?: string; default: number; min: number; max: number };
type Template = { name: string; label: string; params: Param[]; states: string[]; builtin: boolean };
export type Placed = { name: string; geoms: Geom[]; description: string; values: Record<string, string>; state: string | null };
export type ParamInitial = { template: string; values: Record<string, string>; state: string | null; at?: [number, number] };

export function ParamBlockDialog({ initial, onPlace, onClose }: { initial?: ParamInitial; onPlace: (b: Placed, at: [number, number]) => void; onClose: () => void }) {
  const list = useApi<{ items: Template[] }>("/param-blocks");
  const [name, setName] = useState(initial?.template ?? "HOLE_PLATE");
  const t = list.data?.items.find((x) => x.name === name);
  const [vals, setVals] = useState<Record<string, string>>(initial?.values ?? {});
  const [state, setState] = useState<string | null>(initial?.state ?? null);
  const [at, setAt] = useState(initial?.at ? initial.at.join(",") : "0,0");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = (n: string) => {
    setName(n);
    setVals({});
    setState(null);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!t) return;
    const m = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(at);
    if (!m) return setError("삽입점을 x,y 로 입력하세요");
    const values: Record<string, number> = {};
    for (const p of t.params) {
      const raw = vals[p.name];
      if (raw === undefined || raw.trim() === "") continue;
      const v = Number(raw);
      if (!Number.isFinite(v)) return setError(`${p.label ?? p.name}에 숫자를 입력하세요`);
      values[p.name] = v;
    }
    setBusy(true);
    setError(null);
    try {
      const b = await api<Placed>(`/param-blocks/${encodeURIComponent(t.name)}/instantiate`, { method: "POST", json: { values, state: t.states.length ? (state ?? t.states[0]) : null } });
      onPlace(b, [Number(m[1]), Number(m[2])]);
      onClose();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={initial ? "파라미터 블록 수정" : "파라미터 블록"} onClose={onClose}>
      <form className="space-y-3" onSubmit={submit}>
        {list.error && <Err text={errText(list.error)} />}
        <Field label="블록">
          <select className={field} value={name} disabled={!!initial} onChange={(e) => pick(e.target.value)}>
            {list.data?.items.map((x) => (
              <option key={x.name} value={x.name}>
                {x.label}
                {x.builtin ? "" : " (회사)"}
              </option>
            ))}
          </select>
        </Field>
        {t && (
          <div className="grid grid-cols-2 gap-2">
            {t.params.map((p) => (
              <Field key={p.name} label={`${p.label ?? p.name} (${p.min}~${p.max})`}>
                <input className={field} inputMode="decimal" placeholder={String(p.default)} value={vals[p.name] ?? ""} onChange={(e) => setVals({ ...vals, [p.name]: e.target.value })} />
              </Field>
            ))}
          </div>
        )}
        {t && t.states.length > 0 && (
          <Field label="가시성 상태">
            <select className={field} value={state ?? t.states[0]} onChange={(e) => setState(e.target.value)}>
              {t.states.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
        )}
        <Field label="삽입점 x,y (mm)">
          <input className={field} value={at} onChange={(e) => setAt(e.target.value)} />
        </Field>
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            취소
          </button>
          <button type="submit" className={btnPrimary} disabled={busy || !t}>
            {initial ? "다시 만들기" : "삽입"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
