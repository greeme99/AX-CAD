// 2D automation A5: start-up files of the PC library, as cadcraft/AutoCAD do: acad.pgp (aliases),
// acad.lsp and acaddoc.lsp (AX-LISP into the screen's namespace), then S::STARTUP when they define it.
// They only define: a start-up file cannot ask for input or change the drawing (a command it
// issues is dropped and said so). Only files the user put in the library run; a drawing never brings its own.
import { applyPgp } from "./aliases";
import { newSession, runLisp, type Host, type Session } from "./lisp";

export type LibraryItem = { name: string; source: string };
const find = (items: LibraryItem[], name: string) => items.find((f) => f.name.toLowerCase() === name);

export async function runStartup(items: LibraryItem[], session: Session, host: Host): Promise<string[]> {
  const notes: string[] = [];
  const pgp = find(items, "acad.pgp");
  if (pgp) {
    const r = applyPgp(pgp.source);
    notes.push(`별칭 ${r.count}개 (acad.pgp)${r.skipped.length ? ` · 건너뜀 ${r.skipped.length}: ${r.skipped[0]}` : ""}`);
  }
  const safe: Host = { layers: host.layers, layer: host.layer, files: host.files }; // no model, no input
  for (const name of ["acad.lsp", "acaddoc.lsp"]) {
    const f = find(items, name);
    if (!f) continue;
    try {
      const out = await runLisp(f.source, safe, undefined, session);
      notes.push(`시작 LISP ${name}` + (out.segments.length ? " · 도면 명령은 시작 파일에서 실행하지 않습니다" : ""));
    } catch (e) {
      notes.push(`시작 LISP ${name} 오류: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  if (items.some((f) => /^acad(doc)?\.lsp$/i.test(f.name))) {
    try {
      await runLisp("(if S::STARTUP (S::STARTUP))", safe, undefined, session); // defined by the files above, if at all
    } catch (e) {
      notes.push(`S::STARTUP 오류: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return notes;
}
export { newSession };
