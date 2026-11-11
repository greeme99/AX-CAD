// W9 (plan §22.4): conformance — examples from Autodesk's AutoLISP / ActiveX references, rewritten
// (facts only, plan §22.6), in tools/conformance/cases: <name>.lsp|.bas and the output it must
// print, <name>.out (one princ / Debug.Print per line).
// A user's own corpus (plan §22.2: 95% pass): AXCAD_CORPUS=<folder> pnpm vitest run conformance
// runs every .lsp / .bas there (an .out beside a file is compared; without one it must just run).
import { describe, expect, it } from "vitest";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { DrawingModel, NO_TABLES } from "./objmodel";
import { runLisp } from "./lisp";
import { runVba } from "./vba";

const CASES = resolve(__dirname, "../../../../tools/conformance/cases");
const model = () => new DrawingModel([], [{ name: "0" }], "0", { ...NO_TABLES, regapps: ["ACAD"] });

async function output(file: string): Promise<string[]> {
  const src = readFileSync(file, "utf8");
  if (file.endsWith(".bas")) return (await runVba(src, { model: model() })).log;
  return (await runLisp(src, { model: model(), layers: ["0"], layer: "0" })).log;
}
const expected = (file: string) => {
  const out = file.replace(/\.(lsp|bas)$/, ".out");
  return existsSync(out) ? readFileSync(out, "utf8").replace(/\r/g, "").replace(/\n$/, "").split("\n") : null;
};
const programs = (dir: string) => readdirSync(dir).filter((f) => /\.(lsp|bas)$/i.test(f)).sort().map((f) => join(dir, f));

describe("conformance: reference examples", () => {
  for (const file of programs(CASES)) {
    it(file.replace(/^.*[\\/]/, ""), async () => {
      expect(await output(file)).toEqual(expected(file));
    });
  }
});

const corpus = process.env.AXCAD_CORPUS;
describe.runIf(Boolean(corpus))("conformance: the user's corpus", () => {
  it("passes", async () => {
    const files = programs(corpus!);
    const failed: string[] = [];
    for (const f of files) {
      try {
        const want = expected(f);
        const got = await output(f);
        if (want && JSON.stringify(got) !== JSON.stringify(want)) failed.push(`${f}: output differs`);
      } catch (e) {
        failed.push(`${f}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    const rate = files.length ? (100 * (files.length - failed.length)) / files.length : 100;
    console.log(`corpus: ${files.length - failed.length}/${files.length} passed (${rate.toFixed(1)}%)\n${failed.join("\n")}`);
    expect(rate).toBeGreaterThanOrEqual(Number(process.env.AXCAD_CORPUS_MIN ?? 95));
  });
});
