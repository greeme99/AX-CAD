"use client";

import { useState } from "react";
import { btn2, btnPrimary, field } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { LIMITS } from "@/lib/cad/lisp";
import { useApi } from "@/lib/useApi";
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

// 2D automation A1: objects (entmake, vla-), a DCL dialog and a reactor — plan.md §20
const SAMPLE_OBJ = `; AX-LISP 객체 예: vla- 와 entmake (AutoCAD Visual LISP 문법)
(vl-load-com)
(setq ms (vla-get-ModelSpace (vla-get-ActiveDocument (vlax-get-acad-object))))
(setq pl (vla-AddLightWeightPolyline ms '(0 0 150 0 150 90 0 90)))
(vla-put-Closed pl :vlax-true)
(foreach c '((20 20) (130 20) (130 70) (20 70))
  (entmake (list '(0 . "CIRCLE") (cons 10 c) '(40 . 5.0) '(62 . 1))))
(princ (strcat "면적 " (rtos (vla-get-Area pl) 2 0) " mm²"))
`;

const SAMPLE_DCL = `; AX-LISP 대화상자 예: DCL 파일을 라이브러리에 쓰고 열어 판 크기를 묻습니다
(setq f (open "ax-plate.dcl" "w"))
(foreach ln '("plate : dialog { label = \\"판 만들기\\";"
              "  : edit_box { key = \\"w\\"; label = \\"폭 (mm)\\"; value = \\"120\\"; }"
              "  : edit_box { key = \\"h\\"; label = \\"높이 (mm)\\"; value = \\"80\\"; }"
              "  : toggle { key = \\"hole\\"; label = \\"모서리 구멍\\"; value = \\"1\\"; }"
              "  ok_cancel; }")
  (write-line ln f))
(close f)
(setq id (load_dialog "ax-plate.dcl"))
(new_dialog "plate" id)
(action_tile "accept" "(setq w (atof (get_tile \\"w\\")) h (atof (get_tile \\"h\\")) hole (get_tile \\"hole\\")) (done_dialog 1)")
(if (= (start_dialog) 1)
  (progn
    (entmake (list '(0 . "LWPOLYLINE") '(90 . 4) '(70 . 1) '(10 0 0) (list 10 w 0) (list 10 w h) (list 10 0 h)))
    (if (= hole "1")
      (foreach c (list (list 10 10) (list (- w 10) 10) (list (- w 10) (- h 10)) (list 10 (- h 10)))
        (entmake (list '(0 . "CIRCLE") (cons 10 c) '(40 . 4.0)))))))
(unload_dialog id)
`;

const SAMPLE_REACT = `; AX-LISP 반응자 예: 명령이 끝날 때마다 원 개수를 알려 줍니다 (이 화면에서 계속 동작)
(defun count-circles (r cmds / s)
  (setq s (ssget "X" '((0 . "CIRCLE"))))
  (princ (strcat (car cmds) " 끝 · 원 " (itoa (if s (sslength s) 0)) "개")))
(vlr-remove-all)
(vlr-command-reactor nil '((:vlr-commandEnded . count-circles)))
(princ "반응자를 등록했습니다. 명령을 실행해 보세요")
`;

type LibFile = { name: string; source: string; commands: string[] };

export default function LispTab() {
  const cp = useCopilot()!;
  const [src, setSrc] = useState(cp.context.page === "model3d" ? SAMPLE_3D : SAMPLE);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<LispRun | null>(null);
  const run = cp.handlers.current?.lisp;
  const lib = useApi<{ items: LibFile[] }>("/lisp");
  const files = lib.data?.items ?? [];
  const [name, setName] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const store = async (next: { name: string; source: string }[], done: string) => {
    try {
      await api("/lisp", { method: "PUT", json: { files: next.map(({ name, source }) => ({ name, source })) } });
      lib.reload();
      setNote(done);
    } catch (e) {
      setNote(errText(e));
    }
  };
  const importFiles = async (list: FileList | null) => {
    if (!list?.length) return;
    const add: { name: string; source: string }[] = [];
    for (const f of Array.from(list)) {
      if (f.size > 64 * 1024) return setNote(`${f.name}: 64 KB를 넘습니다`);
      add.push({ name: f.name.replace(/\.(lsp|txt)$/i, "").replace(/[^\w가-힣 .-]/g, "_").slice(0, 60), source: await f.text() });
    }
    const keep = files.filter((x) => !add.some((a) => a.name.toLowerCase() === x.name.toLowerCase()));
    await store([...keep, ...add], `${add.length}개 파일을 라이브러리에 넣었습니다`);
  };
  const exportFile = () => {
    const url = URL.createObjectURL(new Blob([src], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name.trim() || "ax-lisp"}.lsp`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000); // after the browser has started the download
  };
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
      <p className="text-xs text-muted-foreground">AutoLISP·Visual LISP 문법(command·entmake·vla-·DCL·vlr-…)으로 그립니다. 파일은 이 PC의 LISP 라이브러리만 쓰고 네트워크·OS에는 접근하지 않으며, 한 번 실행한 결과는 실행 취소 한 번으로 되돌립니다.</p>
      <label htmlFor="lisp-src" className="sr-only">
        LISP 프로그램
      </label>
      {cp.context.page !== "model3d" && (
        <label className="flex items-center gap-2 text-xs">
          예제
          <select className={`${field} w-auto py-1 text-xs`} defaultValue="" onChange={(e) => e.target.value && setSrc(({ cmd: SAMPLE, obj: SAMPLE_OBJ, dcl: SAMPLE_DCL, react: SAMPLE_REACT } as Record<string, string>)[e.target.value])}>
            <option value="">고르기…</option>
            <option value="cmd">명령(command)</option>
            <option value="obj">객체(vla·entmake)</option>
            <option value="dcl">대화상자(DCL)</option>
            <option value="react">반응자(vlr-)</option>
          </select>
        </label>
      )}
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
      <section aria-label="LISP 라이브러리" className="space-y-2 border-t border-line pt-2">
        <h3 className="text-xs font-semibold text-foreground">이 PC의 라이브러리 · 명령행에서 이름으로 실행</h3>
        <div className="flex flex-wrap gap-2">
          <label htmlFor="lisp-name" className="sr-only">
            파일 이름
          </label>
          <input id="lisp-name" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="파일 이름" className={`${field} w-36`} />
          <button
            type="button"
            className={btn2}
            disabled={!name.trim()}
            onClick={() => void store([...files.filter((x) => x.name.toLowerCase() !== name.trim().toLowerCase()), { name: name.trim(), source: src }], `'${name.trim()}'을(를) 저장했습니다`)}
          >
            라이브러리에 저장
          </button>
          <button type="button" className={btn2} onClick={exportFile}>
            .lsp 내보내기
          </button>
          <label className={`${btn2} cursor-pointer`}>
            .lsp 가져오기
            <input type="file" multiple accept=".lsp,.txt" className="sr-only" onChange={(e) => void importFiles(e.target.files)} />
          </label>
        </div>
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
        {lib.error && <p className="text-xs text-red-600">{errText(lib.error)}</p>}
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={f.name} className="flex items-center gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-foreground" title={f.name}>
                {f.name} <span className="font-mono text-muted-foreground">{f.commands.join(" ") || "(c: 명령 없음)"}</span>
              </span>
              <button
                type="button"
                className="text-[var(--color-primary)] hover:underline"
                onClick={() => {
                  setSrc(f.source);
                  setName(f.name);
                }}
              >
                열기
              </button>
              <button type="button" className="text-red-600 hover:underline" onClick={() => void store(files.filter((x) => x.name !== f.name), `'${f.name}'을(를) 지웠습니다`)}>
                삭제
              </button>
            </li>
          ))}
          {lib.data && !files.length && <li className="text-xs text-muted-foreground">비어 있습니다. 자주 쓰는 LISP를 저장하면 2D 명령행에 명령 이름(예: PLATE)을 입력해 실행합니다.</li>}
        </ul>
      </section>
    </div>
  );
}
