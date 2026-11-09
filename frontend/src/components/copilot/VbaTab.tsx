"use client";

import { useMemo, useState } from "react";
import { btn2, btnPrimary, field } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { macros, parse, VBA_LIMITS, VbaError } from "@/lib/cad/vba";
import type { LispRun } from "@/lib/copilot";
import { useApi } from "@/lib/useApi";
import { useCopilot } from "./CopilotProvider";

// 2D automation A2: VBA macros in the Copilot Drawing tab — write or open a .bas, pick a Sub, run
// it on the drawing (ThisDrawing). Saved macros live in the PC library and run from the command
// line as VBARUN <macro>.

const SAMPLES: Record<string, [string, string]> = {
  draw: [
    "선·원 그리기",
    `' AX-CAD VBA 예: ThisDrawing.ModelSpace로 그립니다 (AutoCAD VBA와 같은 이름)
Sub Main()
    Dim p1(0 To 2) As Double, p2(0 To 2) As Double
    p2(0) = 150: p2(1) = 0
    Dim ln As AcadLine
    Set ln = ThisDrawing.ModelSpace.AddLine(p1, p2)
    ln.Color = acRed
    Dim c(0 To 2) As Double
    c(0) = 75: c(1) = 40
    ThisDrawing.ModelSpace.AddCircle c, 25
    MsgBox "선 길이 " & Format(ln.Length, "0.0") & " mm"
End Sub
`,
  ],
  plate: [
    "판과 구멍 (Function·For)",
    `' 판 W×H와 모서리에서 E 떨어진 구멍 4개
Function Pt(x As Double, y As Double) As Variant
    Dim p(0 To 2) As Double
    p(0) = x: p(1) = y
    Pt = p
End Function

Sub Plate()
    Const W = 200, H = 120, E = 15, D = 12
    Dim pl As AcadLWPolyline
    Set pl = ThisDrawing.ModelSpace.AddLightWeightPolyline(Array(0, 0, W, 0, W, H, 0, H))
    pl.Closed = True
    Dim xs As Variant, ys As Variant, i As Integer
    xs = Array(E, W - E, W - E, E): ys = Array(E, E, H - E, H - E)
    For i = 0 To 3
        ThisDrawing.ModelSpace.AddCircle Pt(xs(i), ys(i)), D / 2
    Next i
    Debug.Print "면적 " & pl.Area & " mm²"
End Sub
`,
  ],
  ask: [
    "입력 받기 (Utility)",
    `' 화면에서 중심점을 찍고 반지름을 입력하면 원을 그립니다
Sub CircleAt()
    Dim c As Variant, r As Double
    c = ThisDrawing.Utility.GetPoint(, "중심점: ")
    r = ThisDrawing.Utility.GetReal("반지름: ")
    ThisDrawing.ModelSpace.AddCircle c, r
End Sub
`,
  ],
  edit: [
    "일괄 수정 (For Each·Select Case)",
    `' 도면의 원은 빨강, 선은 파랑으로 바꾸고 개수를 셉니다
Sub Recolor()
    Dim e As Object, nc As Integer, nl As Integer
    For Each e In ThisDrawing.ModelSpace
        Select Case e.ObjectName
            Case "AcDbCircle": e.Color = acRed: nc = nc + 1
            Case "AcDbLine": e.Color = acBlue: nl = nl + 1
        End Select
    Next
    Debug.Print "원 " & nc & "개, 선 " & nl & "개"
End Sub
`,
  ],
};

type LibFile = { name: string; source: string };

export default function VbaTab() {
  const cp = useCopilot()!;
  const [src, setSrc] = useState(SAMPLES.draw[1]);
  const [macro, setMacro] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<LispRun | null>(null);
  const [name, setName] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const run = cp.handlers.current?.vba;
  const lib = useApi<{ items: LibFile[] }>("/lisp");
  const files = (lib.data?.items ?? []).filter((f) => f.name.toLowerCase().endsWith(".bas"));

  // the Subs a user can run, read from the text as it is typed (a parse error shows on run)
  const list = useMemo(() => {
    try {
      return macros(parse(src));
    } catch {
      return [];
    }
  }, [src]);
  const chosen = list.includes(macro) ? macro : (list[0] ?? "");

  const go = async () => {
    if (!run || busy) return;
    setBusy(true);
    try {
      setRes(await run(src, chosen || undefined));
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    const n = name.trim().replace(/\.bas$/i, "");
    if (!n) return;
    try {
      const all = (await api<{ items: LibFile[] }>("/lisp")).items;
      const file = `${n}.bas`;
      const next = [...all.filter((f) => f.name.toLowerCase() !== file.toLowerCase()).map(({ name: fn, source }) => ({ name: fn, source })), { name: file, source: src }];
      await api("/lisp", { method: "PUT", json: { files: next } });
      lib.reload();
      setNote(`'${file}'을(를) 저장했습니다. 명령행: VBARUN ${list[0] ?? "매크로"}`);
    } catch (e) {
      setNote(errText(e));
    }
  };
  const remove = async (file: string) => {
    try {
      const all = (await api<{ items: LibFile[] }>("/lisp")).items;
      await api("/lisp", { method: "PUT", json: { files: all.filter((f) => f.name !== file).map(({ name: fn, source }) => ({ name: fn, source })) } });
      lib.reload();
      setNote(`'${file}'을(를) 지웠습니다`);
    } catch (e) {
      setNote(errText(e));
    }
  };
  const open = (f: File | undefined) => {
    if (!f) return;
    if (f.size > VBA_LIMITS.source) return setRes({ error: "파일이 너무 큽니다 (256 KB 이하)", log: [], commands: [], applied: 0 });
    f.text().then((t) => {
      setSrc(t);
      setName(f.name.replace(/\.(bas|txt)$/i, "").replace(/[^\w가-힣 .-]/g, "_").slice(0, 56));
    }, (e) => setRes({ error: String(e), log: [], commands: [], applied: 0 }));
  };

  return (
    <div className="space-y-2 p-3 text-sm">
      <p className="text-xs text-muted-foreground">AutoCAD VBA 문법(Sub·Dim·For·If·With)과 ThisDrawing 객체(ModelSpace.AddLine·Layers·Utility.GetPoint…)로 그립니다. 파일·네트워크·외부 프로그램에는 접근하지 않고, 한 번 실행한 결과는 실행 취소 한 번으로 되돌립니다.</p>
      <label className="flex items-center gap-2 text-xs">
        예제
        <select className={`${field} w-auto py-1 text-xs`} defaultValue="" onChange={(e) => e.target.value && setSrc(SAMPLES[e.target.value][1])}>
          <option value="">고르기…</option>
          {Object.entries(SAMPLES).map(([k, [label]]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor="vba-src" className="sr-only">
        VBA 매크로
      </label>
      <textarea id="vba-src" value={src} onChange={(e) => setSrc(e.target.value)} spellCheck={false} className={`${field} h-56 font-mono text-xs`} />
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-xs">
          매크로
          <select aria-label="실행할 매크로" className={`${field} w-auto py-1 text-xs`} value={chosen} onChange={(e) => setMacro(e.target.value)} disabled={!list.length}>
            {list.length ? list.map((m) => <option key={m}>{m}</option>) : <option value="">(Sub 없음)</option>}
          </select>
        </label>
        <button type="button" className={btnPrimary} disabled={!run || busy} onClick={() => void go()}>
          {busy ? "실행 중…" : "실행"}
        </button>
        <label className={`${btn2} cursor-pointer`}>
          .bas 열기
          <input type="file" accept=".bas,.txt" className="sr-only" onChange={(e) => open(e.target.files?.[0])} />
        </label>
      </div>
      {!run && <p className="text-xs text-muted-foreground">도면 편집(2D) 화면에서 실행할 수 있습니다.</p>}
      {res && (
        <div role="status" className="space-y-1 rounded-md bg-[var(--color-bg-subtle)] p-2 text-xs">
          {res.error ? <p className="text-red-600">실행 실패: {res.error} (도면은 바뀌지 않았습니다)</p> : <p className="text-[var(--color-success-text)]">실행했습니다 · 변경 {res.applied}건</p>}
          {res.log.map((l, i) => (
            <p key={i} className="whitespace-pre-wrap font-mono text-body">
              {l}
            </p>
          ))}
        </div>
      )}
      <section aria-label="VBA 라이브러리" className="space-y-2 border-t border-line pt-2">
        <h3 className="text-xs font-semibold text-foreground">이 PC의 라이브러리 · 명령행 VBARUN 매크로</h3>
        <div className="flex flex-wrap gap-2">
          <label htmlFor="vba-name" className="sr-only">
            파일 이름
          </label>
          <input id="vba-name" value={name} maxLength={56} onChange={(e) => setName(e.target.value)} placeholder="파일 이름" className={`${field} w-36`} />
          <button type="button" className={btn2} disabled={!name.trim()} onClick={() => void save()}>
            라이브러리에 저장
          </button>
        </div>
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={f.name} className="flex items-center gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-foreground" title={f.name}>
                {f.name}{" "}
                <span className="font-mono text-muted-foreground">
                  {(() => {
                    try {
                      return macros(parse(f.source)).join(" ") || "(매크로 없음)";
                    } catch (e) {
                      return e instanceof VbaError ? "(문법 오류)" : "";
                    }
                  })()}
                </span>
              </span>
              <button
                type="button"
                className="text-[var(--color-primary)] hover:underline"
                onClick={() => {
                  setSrc(f.source);
                  setName(f.name.replace(/\.bas$/i, ""));
                }}
              >
                열기
              </button>
              <button type="button" className="text-red-600 hover:underline" onClick={() => void remove(f.name)}>
                삭제
              </button>
            </li>
          ))}
          {!files.length && <li className="text-xs text-muted-foreground">저장된 VBA 매크로가 없습니다.</li>}
        </ul>
      </section>
    </div>
  );
}
