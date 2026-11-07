"use client";

import { useState } from "react";
import { btn2, btnPrimary, field } from "@/components/ui";
import { LIMITS } from "@/lib/cad/lisp";
import type { LispRun } from "@/lib/copilot";
import { useCopilot } from "./CopilotProvider";

// Track 11: AX-LISP in the Copilot Drawing tab — write or open a .lsp, run it, run its c: commands

const SAMPLE = `; AX-LISP 예: 구멍 4개 판 (AutoLISP 문법)
(defun c:plate (/ p w h e d)
  (setq p '(0 0) w 120 h 80 e 15 d 10)
  (command "REC" p (list w h))
  (foreach c (list (list e e) (list (- w e) e) (list (- w e) (- h e)) (list e (- h e)))
    (command "C" c (/ d 2)))
  (princ "판을 그렸습니다"))
(c:plate)
`;

const SAMPLE_3D = `; AX-LISP 3D 예: 받침 위에 기둥 4개 (BOX·CYLINDER·SPHERE·CONE·TORUS)
(command "BOX" 120 80 10 '(0 0 0))
(foreach p (list '(20 20) '(100 20) '(100 60) '(20 60))
  (command "CYLINDER" 6 40 (list (car p) (cadr p) 10)))
`;

export default function LispTab() {
  const cp = useCopilot()!;
  const [src, setSrc] = useState(cp.context.page === "model3d" ? SAMPLE_3D : SAMPLE);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<LispRun | null>(null);
  const run = cp.handlers.current?.lisp;
  const go = async (command?: string) => {
    if (!run || busy) return;
    setBusy(true);
    try {
      setRes(await run(src, command));
    } finally {
      setBusy(false);
    }
  };
  const open = (f: File | undefined) => {
    if (!f) return;
    if (f.size > LIMITS.source) return setRes({ error: "파일이 너무 큽니다 (256 KB 이하)", log: [], commands: [], applied: 0 });
    f.text().then(setSrc, (e) => setRes({ error: String(e), log: [], commands: [], applied: 0 }));
  };
  return (
    <div className="space-y-2 p-3 text-sm">
      <p className="text-xs text-muted-foreground">AutoLISP 문법(defun·setq·command·polar·foreach…)으로 그립니다. 파일·네트워크에 접근하지 않고, 도면은 명령 입력으로만 바뀌며 실행 취소 한 번으로 되돌립니다.</p>
      <label htmlFor="lisp-src" className="sr-only">
        LISP 프로그램
      </label>
      <textarea id="lisp-src" value={src} onChange={(e) => setSrc(e.target.value)} spellCheck={false} className={`${field} h-56 font-mono text-xs`} />
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btnPrimary} disabled={!run || busy} onClick={() => void go()}>
          {busy ? "실행 중…" : "실행"}
        </button>
        <label className={`${btn2} cursor-pointer`}>
          .lsp 열기
          <input type="file" accept=".lsp,.txt" className="sr-only" onChange={(e) => open(e.target.files?.[0])} />
        </label>
      </div>
      {!run && <p className="text-xs text-muted-foreground">도면 편집(2D) 또는 3D 모델 화면에서 실행할 수 있습니다.</p>}
      {res && (
        <div role="status" className="space-y-1 rounded-md bg-[var(--color-bg-subtle)] p-2 text-xs">
          {res.error ? <p className="text-red-600">실행 실패: {res.error} (도면은 바뀌지 않았습니다)</p> : <p className="text-[var(--color-success-text)]">실행했습니다 · 변경 {res.applied}건</p>}
          {res.log.map((l, i) => (
            <p key={i} className="font-mono text-body">
              {l}
            </p>
          ))}
          {!!res.commands.length && (
            <div className="flex flex-wrap items-center gap-1 pt-1">
              <span className="text-muted-foreground">정의된 명령:</span>
              {res.commands.map((c) => (
                <button key={c} type="button" className={btn2} disabled={busy} onClick={() => void go(c)}>
                  {c}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
