// 2D automation A2: VBA macros in the 2D screen (plan.md §20). A safe VBA subset with its own
// parser and interpreter — no eval, no COM, files, network or OS. The drawing is the same object
// model as AX-LISP's vla- functions (objmodel.ts): ThisDrawing.ModelSpace.AddLine, Layers.Add,
// ActiveLayer, entity properties and Move/Rotate/... The screen applies changes() as one undo step.
import { isByRef } from "./axobj";
import { describe, DrawingModel, getProp, glob, invoke, itemsOf, isModelObj, ModelError, putProp, type Changes, type Obj, type OVal } from "./objmodel";
import type { Pt } from "./view";

export const VBA_LIMITS = { source: 256 * 1024, steps: 1_000_000, depth: 200, array: 20_000, string: 64 * 1024, ms: 30_000 };

export class VbaError extends Error {
  constructor(
    message: string,
    readonly line?: number,
  ) {
    super(message);
  }
}

// --- values ---------------------------------------------------------------------------------

type VArr = { kind: "array"; lower: number; items: Val[] };
type Special = { kind: "util" } | { kind: "debug" } | { kind: "err" };
export type Val = number | string | boolean | null | VArr | Obj | Special;
const isArr = (v: Val): v is VArr => typeof v === "object" && v !== null && "kind" in v && v.kind === "array";
const isSpecial = (v: Val): v is Special => typeof v === "object" && v !== null && "kind" in v && v.kind !== "array";

function show(v: Val): string {
  if (v === null) return "";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(15)));
  if (typeof v === "string") return v;
  if (isArr(v)) return `(배열 ${v.items.length})`;
  if (isSpecial(v)) return v.kind;
  return `(객체 ${v.t})`;
}

// --- lexer ----------------------------------------------------------------------------------

// sp: whitespace before the token — "Debug.Print .Name" (a With member) vs "a.Name"
type Tok = { t: string; kind: "num" | "str" | "id" | "op" | "nl"; line: number; sp: boolean };

function lex(src: string): Tok[] {
  if (src.length > VBA_LIMITS.source) throw new VbaError("매크로가 너무 큽니다 (256 KB 이하)");
  const out: Tok[] = [];
  let i = 0;
  let line = 1;
  let start = 0;
  const push = (t: string, kind: Tok["kind"]) => out.push({ t, kind, line, sp: start > 0 && /[ \t]/.test(src[start - 1]) });
  while (i < src.length) {
    const c = src[i];
    start = i;
    if (c === "\r") {
      i++;
    } else if (c === "\n") {
      push("\n", "nl");
      line++;
      i++;
    } else if (c === " " || c === "\t") {
      i++;
    } else if (c === "_" && /[ \t]/.test(src[i - 1] ?? " ") && /^[ \t]*\r?\n/.test(src.slice(i + 1))) {
      // line continuation: " _" at the end of a line
      i = src.indexOf("\n", i) + 1;
      line++;
    } else if (c === "'" || (/^rem\b/i.test(src.slice(i, i + 4)) && (i === 0 || /[\s:]/.test(src[i - 1])))) {
      while (i < src.length && src[i] !== "\n") i++;
    } else if (c === '"') {
      let s = "";
      i++;
      for (;;) {
        if (i >= src.length || src[i] === "\n") throw new VbaError("닫히지 않은 문자열", line);
        if (src[i] === '"') {
          if (src[i + 1] === '"') {
            s += '"';
            i += 2;
            continue;
          }
          i++;
          break;
        }
        s += src[i++];
      }
      push(s, "str");
    } else if (/[0-9]/.test(c) || (c === "." && /[0-9]/.test(src[i + 1] ?? ""))) {
      const m = /^(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?[#!@%&]?/.exec(src.slice(i))!;
      push(m[0].replace(/[#!@%&]$/, ""), "num");
      i += m[0].length;
    } else if (/[A-Za-z_가-힣]/.test(c)) {
      const m = /^[A-Za-z_가-힣][\w가-힣]*[$%&!#@]?/.exec(src.slice(i))!;
      push(m[0].replace(/[$%&!#@]$/, ""), "id");
      i += m[0].length;
    } else if (c === ":") {
      push(":", "nl"); // statement separator (a single-line If keeps all of them)
      i++;
    } else {
      const two = src.slice(i, i + 2);
      if (["<>", "<=", ">="].includes(two)) {
        push(two, "op");
        i += 2;
      } else if ("+-*/\\^&=<>(),.".includes(c)) {
        push(c, "op");
        i++;
      } else throw new VbaError(`알 수 없는 문자 '${c}'`, line);
    }
  }
  push("\n", "nl");
  return out;
}

// --- AST ------------------------------------------------------------------------------------

type Expr =
  | { k: "num"; v: number }
  | { k: "str"; v: string }
  | { k: "lit"; v: boolean | null }
  | { k: "id"; name: string }
  | { k: "with" } // the object of the enclosing With block
  | { k: "member"; obj: Expr; name: string }
  | { k: "call"; fn: Expr; args: Expr[] }
  | { k: "un"; op: string; e: Expr }
  | { k: "bin"; op: string; a: Expr; b: Expr };

type Bound = { lo: Expr; hi: Expr };
type Stmt = { line: number } & (
  | { k: "dim"; vars: { name: string; bounds: Bound[] | null; type: string; events?: true }[] }
  | { k: "redim"; preserve: boolean; name: string; bounds: Bound[] }
  | { k: "const"; items: { name: string; e: Expr }[] }
  | { k: "assign"; target: Expr; e: Expr; set: boolean }
  | { k: "call"; e: Expr }
  | { k: "if"; branches: { cond: Expr; body: Stmt[] }[]; else: Stmt[] }
  | { k: "for"; v: string; from: Expr; to: Expr; step: Expr | null; body: Stmt[] }
  | { k: "foreach"; v: string; coll: Expr; body: Stmt[] }
  | { k: "do"; test: "while" | "until" | null; pre: boolean; cond: Expr | null; body: Stmt[] }
  | { k: "select"; e: Expr; cases: { tests: ({ is: string; e: Expr } | { lo: Expr; hi: Expr } | { e: Expr })[]; body: Stmt[] }[]; else: Stmt[] }
  | { k: "with"; obj: Expr; body: Stmt[] }
  | { k: "exit"; what: string }
  | { k: "onerror"; resume: boolean }
);
type Param = { name: string; byRef: boolean; optional: boolean; def: Expr | null; array: boolean };
type Proc = { kind: "sub" | "function"; name: string; params: Param[]; body: Stmt[]; line: number; isPrivate: boolean };
export type Program = { procs: Map<string, Proc>; module: Stmt[] };

const KEY = (t: Tok | undefined, word: string) => !!t && t.kind === "id" && t.t.toLowerCase() === word;

/** Source -> procedures and module-level declarations. */
export function parse(src: string): Program {
  const toks = lex(src);
  let p = 0;
  const peek = (o = 0) => toks[p + o];
  const line = () => toks[Math.min(p, toks.length - 1)].line;
  const fail = (msg: string): never => {
    throw new VbaError(msg, line());
  };
  const take = () => toks[p++];
  const isWord = (w: string, o = 0) => KEY(peek(o), w);
  const word = (w: string) => {
    if (!isWord(w)) fail(`'${w}'가 필요합니다 ('${peek()?.t === "\n" ? "줄 끝" : peek()?.t}')`);
    p++;
  };
  const op = (o: string) => {
    if (!(peek()?.kind === "op" && peek().t === o)) fail(`'${o}'가 필요합니다`);
    p++;
  };
  const isOp = (o: string, k = 0) => peek(k)?.kind === "op" && peek(k).t === o;
  const name = () => {
    const t = take();
    if (t?.kind !== "id") fail("이름이 필요합니다");
    return t.t;
  };
  const eol = () => {
    if (peek()?.kind !== "nl") fail(`문장 끝이 필요합니다 ('${peek()?.t}')`);
    while (peek()?.kind === "nl") p++;
  };
  const skipNl = () => {
    while (peek()?.kind === "nl") p++;
  };

  // expressions: precedence Imp < Eqv < Xor < Or < And < Not < comparison < & < +- < Mod < \ < */ < unary - < ^
  const BIN: string[][] = [["xor"], ["or"], ["and"]];
  const expr = (): Expr => logic(0);
  const logic = (lvl: number): Expr => {
    if (lvl === BIN.length) return not();
    let a = logic(lvl + 1);
    while (peek()?.kind === "id" && BIN[lvl].includes(peek().t.toLowerCase())) {
      const o = take().t.toLowerCase();
      a = { k: "bin", op: o, a, b: logic(lvl + 1) };
    }
    return a;
  };
  const not = (): Expr => (isWord("not") ? (p++, { k: "un", op: "not", e: not() }) : compare());
  const compare = (): Expr => {
    let a = concat();
    while ((peek()?.kind === "op" && ["=", "<>", "<", ">", "<=", ">="].includes(peek().t)) || isWord("is") || isWord("like")) {
      const o = take().t.toLowerCase();
      a = { k: "bin", op: o, a, b: concat() };
    }
    return a;
  };
  const concat = (): Expr => {
    let a = add();
    while (isOp("&")) {
      p++;
      a = { k: "bin", op: "&", a, b: add() };
    }
    return a;
  };
  const add = (): Expr => {
    let a = mod();
    while (isOp("+") || isOp("-")) {
      const o = take().t;
      a = { k: "bin", op: o, a, b: mod() };
    }
    return a;
  };
  const mod = (): Expr => {
    let a = idiv();
    while (isWord("mod")) {
      p++;
      a = { k: "bin", op: "mod", a, b: idiv() };
    }
    return a;
  };
  const idiv = (): Expr => {
    let a = mul();
    while (isOp("\\")) {
      p++;
      a = { k: "bin", op: "\\", a, b: mul() };
    }
    return a;
  };
  const mul = (): Expr => {
    let a = unary();
    while (isOp("*") || isOp("/")) {
      const o = take().t;
      a = { k: "bin", op: o, a, b: unary() };
    }
    return a;
  };
  const unary = (): Expr => {
    if (isOp("-")) {
      p++;
      return { k: "un", op: "-", e: unary() };
    }
    if (isOp("+")) {
      p++;
      return unary();
    }
    return pow();
  };
  const pow = (): Expr => {
    const a = postfix();
    if (isOp("^")) {
      p++;
      return { k: "bin", op: "^", a, b: unary() };
    }
    return a;
  };
  // an argument may be left out: GetPoint(, "prompt")
  const arg = (): Expr => (isOp(",") || isOp(")") ? { k: "lit", v: null } : expr());
  const args = (): Expr[] => {
    op("(");
    const out: Expr[] = [];
    if (!isOp(")")) {
      do out.push(arg());
      while (isOp(",") && (p++, true));
    }
    op(")");
    return out;
  };
  const postfix = (): Expr => {
    let e = primary();
    for (;;) {
      if (isOp(".") && !peek().sp) {
        p++;
        e = { k: "member", obj: e, name: name() };
      } else if (isOp("(")) e = { k: "call", fn: e, args: args() };
      else return e;
    }
  };
  const primary = (): Expr => {
    const t = peek();
    if (!t) fail("식이 필요합니다");
    if (t.kind === "num") return (p++, { k: "num", v: Number(t.t) });
    if (t.kind === "str") return (p++, { k: "str", v: t.t });
    if (isOp("(")) {
      p++;
      const e = expr();
      op(")");
      return e;
    }
    if (isOp(".")) {
      p++;
      return { k: "member", obj: { k: "with" }, name: name() };
    }
    if (t.kind === "id") {
      const w = t.t.toLowerCase();
      p++;
      if (w === "true" || w === "false") return { k: "lit", v: w === "true" };
      if (w === "nothing" || w === "empty" || w === "null") return { k: "lit", v: null };
      if (w === "new") fail("New로 객체를 만들 수 없습니다 (ThisDrawing의 객체를 쓰세요)");
      return { k: "id", name: t.t };
    }
    return fail(`식이 필요합니다 ('${t.t}')`);
  };

  const bounds = (): Bound[] => {
    op("(");
    const out: Bound[] = [];
    if (!isOp(")"))
      do {
        const a = expr();
        if (isWord("to")) {
          p++;
          out.push({ lo: a, hi: expr() });
        } else out.push({ lo: { k: "num", v: 0 }, hi: a });
      } while (isOp(",") && (p++, true));
    op(")");
    return out;
  };
  /** "As Type" (lower case, "" when left out): it sets the starting value of a Dim. */
  const asType = (): string => {
    if (!isWord("as")) return "";
    p++;
    if (isWord("new")) fail("New로 객체를 만들 수 없습니다");
    let t = name();
    if (isOp(".")) {
      p++; // AcadLine, AutoCAD.AcadEntity ...
      t = name();
    }
    return t.toLowerCase();
  };

  // statements until one of the end words (checked by the caller)
  const block = (ends: (() => boolean)[]): Stmt[] => {
    const out: Stmt[] = [];
    skipNl();
    while (peek() && !ends.some((f) => f())) {
      if (p >= toks.length - 1) fail("블록이 끝나지 않았습니다 (End ... 필요)");
      out.push(stmt());
      skipNl();
    }
    return out;
  };
  const endOf = (w: string) => () => isWord("end") && isWord(w, 1);

  const stmt = (): Stmt => {
    const ln = line();
    const w = peek().kind === "id" ? peek().t.toLowerCase() : "";
    if (w === "dim" || w === "private" || w === "public" || w === "static" || w === "global") {
      p++;
      const vars: { name: string; bounds: Bound[] | null; type: string; events?: true }[] = [];
      do {
        const events = isWord("withevents") && (p++, true);
        const n = name();
        const b = isOp("(") ? bounds() : null;
        vars.push({ name: n, bounds: b, type: asType(), ...(events ? { events: true as const } : {}) });
      } while (isOp(",") && (p++, true));
      eol();
      return { line: ln, k: "dim", vars };
    }
    if (w === "redim") {
      p++;
      const preserve = isWord("preserve") && (p++, true);
      const n = name();
      const b = bounds();
      asType();
      eol();
      return { line: ln, k: "redim", preserve, name: n, bounds: b };
    }
    if (w === "const") {
      p++;
      const items: { name: string; e: Expr }[] = [];
      do {
        const n = name();
        asType();
        op("=");
        items.push({ name: n, e: expr() });
      } while (isOp(",") && (p++, true));
      eol();
      return { line: ln, k: "const", items };
    }
    if (w === "set" || w === "let") {
      p++;
      const target = postfix();
      op("=");
      const e = expr();
      eol();
      return { line: ln, k: "assign", target, e, set: w === "set" };
    }
    if (w === "if") {
      p++;
      const cond = expr();
      word("then");
      if (peek().kind !== "nl") {
        // single-line If: If c Then a: b [Else c: d] — every statement on the line belongs to it
        const more = (): Stmt[] => {
          const out = [inline()];
          while (peek().kind === "nl" && peek().t === ":" && peek(1)?.kind !== "nl") {
            p++;
            if (isWord("else")) break;
            out.push(inline());
          }
          return out;
        };
        const then = more();
        let els: Stmt[] = [];
        if (isWord("else")) {
          p++;
          els = more();
        }
        eol();
        return { line: ln, k: "if", branches: [{ cond, body: then }], else: els };
      }
      const branches = [{ cond, body: block([() => isWord("elseif"), () => isWord("else"), endOf("if")]) }];
      let els: Stmt[] = [];
      while (isWord("elseif")) {
        p++;
        const c = expr();
        word("then");
        branches.push({ cond: c, body: block([() => isWord("elseif"), () => isWord("else"), endOf("if")]) });
      }
      if (isWord("else")) {
        p++;
        els = block([endOf("if")]);
      }
      word("end");
      word("if");
      eol();
      return { line: ln, k: "if", branches, else: els };
    }
    if (w === "for") {
      p++;
      if (isWord("each")) {
        p++;
        const v = name();
        word("in");
        const coll = expr();
        const body = block([() => isWord("next")]);
        word("next");
        if (peek().kind === "id") p++;
        eol();
        return { line: ln, k: "foreach", v, coll, body };
      }
      const v = name();
      op("=");
      const from = expr();
      word("to");
      const to = expr();
      const step = isWord("step") ? (p++, expr()) : null;
      const body = block([() => isWord("next")]);
      word("next");
      if (peek().kind === "id") p++;
      eol();
      return { line: ln, k: "for", v, from, to, step, body };
    }
    if (w === "do") {
      p++;
      let test: "while" | "until" | null = null;
      let cond: Expr | null = null;
      let pre = true;
      if (isWord("while") || isWord("until")) {
        test = take().t.toLowerCase() as "while" | "until";
        cond = expr();
      }
      const body = block([() => isWord("loop")]);
      word("loop");
      if (isWord("while") || isWord("until")) {
        if (test) fail("Do와 Loop 양쪽에 조건을 둘 수 없습니다");
        test = take().t.toLowerCase() as "while" | "until";
        cond = expr();
        pre = false;
      }
      eol();
      return { line: ln, k: "do", test, pre, cond, body };
    }
    if (w === "while") {
      p++;
      const cond = expr();
      const body = block([() => isWord("wend")]);
      word("wend");
      eol();
      return { line: ln, k: "do", test: "while", pre: true, cond, body };
    }
    if (w === "select") {
      p++;
      word("case");
      const e = expr();
      eol();
      const cases: Extract<Stmt, { k: "select" }>["cases"] = [];
      let els: Stmt[] = [];
      skipNl();
      while (isWord("case")) {
        p++;
        if (isWord("else")) {
          p++;
          els = block([endOf("select")]);
          break;
        }
        const tests: Extract<Stmt, { k: "select" }>["cases"][number]["tests"] = [];
        do {
          if (isWord("is")) {
            p++;
            const o = take().t;
            tests.push({ is: o, e: expr() });
          } else {
            const a = expr();
            if (isWord("to")) {
              p++;
              tests.push({ lo: a, hi: expr() });
            } else tests.push({ e: a });
          }
        } while (isOp(",") && (p++, true));
        cases.push({ tests, body: block([() => isWord("case"), endOf("select")]) });
      }
      word("end");
      word("select");
      eol();
      return { line: ln, k: "select", e, cases, else: els };
    }
    if (w === "with") {
      p++;
      const obj = expr();
      const body = block([endOf("with")]);
      word("end");
      word("with");
      eol();
      return { line: ln, k: "with", obj, body };
    }
    if (w === "on") {
      p++;
      word("error");
      if (isWord("resume")) {
        p++;
        word("next");
        eol();
        return { line: ln, k: "onerror", resume: true };
      }
      word("goto");
      if (peek().t !== "0") fail("On Error GoTo는 0만 됩니다 (On Error Resume Next를 쓰세요)");
      p++;
      eol();
      return { line: ln, k: "onerror", resume: false };
    }
    const s = inline();
    eol();
    return s;
  };

  // a statement that fits on one line (also the parts of a single-line If)
  const inline = (): Stmt => {
    const ln = line();
    if (isWord("exit")) {
      p++;
      return { line: ln, k: "exit", what: name().toLowerCase() };
    }
    if (isWord("call")) {
      p++;
      return { line: ln, k: "call", e: postfix() };
    }
    if (isWord("set") || isWord("let")) {
      const set = take().t.toLowerCase() === "set";
      const target = postfix();
      op("=");
      return { line: ln, k: "assign", target, e: expr(), set };
    }
    const target = postfix();
    if (isOp("=")) {
      p++;
      return { line: ln, k: "assign", target, e: expr(), set: false };
    }
    // a Sub call without parentheses: Foo a, b  /  obj.Method a, b
    if (peek().kind !== "nl" && !isWord("else")) {
      const more: Expr[] = [];
      do more.push(expr());
      while (isOp(",") && (p++, true));
      return { line: ln, k: "call", e: { k: "call", fn: target, args: more } };
    }
    return { line: ln, k: "call", e: target };
  };

  const procs = new Map<string, Proc>();
  const top: Stmt[] = [];
  skipNl();
  while (p < toks.length) {
    skipNl();
    if (p >= toks.length) break;
    let isPrivate = false;
    if ((isWord("private") || isWord("public")) && (isWord("sub", 1) || isWord("function", 1))) isPrivate = take().t.toLowerCase() === "private";
    if (isWord("option")) {
      while (peek().kind !== "nl") p++;
      continue;
    }
    if (isWord("attribute")) {
      while (peek().kind !== "nl") p++;
      continue;
    }
    if (isWord("sub") || isWord("function")) {
      const ln = line();
      const kind = take().t.toLowerCase() as "sub" | "function";
      const n = name();
      const params: Param[] = [];
      if (isOp("(")) {
        p++;
        if (!isOp(")"))
          do {
            const optional = isWord("optional") && (p++, true);
            let byRef = true;
            if (isWord("byval")) {
              p++;
              byRef = false;
            } else if (isWord("byref")) p++;
            const pn = name();
            const array = isOp("(") && (p++, op(")"), true);
            asType();
            const def = isOp("=") ? (p++, expr()) : null;
            params.push({ name: pn, byRef, optional, def, array });
          } while (isOp(",") && (p++, true));
        op(")");
      }
      asType();
      eol();
      const body = block([endOf(kind)]);
      word("end");
      word(kind);
      eol();
      if (procs.has(n.toLowerCase())) throw new VbaError(`같은 이름의 프로시저가 두 번 있습니다: ${n}`, ln);
      procs.set(n.toLowerCase(), { kind, name: n, params, body, line: ln, isPrivate });
      continue;
    }
    top.push(stmt());
  }
  return { procs, module: top };
}

// --- interpreter ----------------------------------------------------------------------------

export type VbaHost = {
  model?: DrawingModel;
  /** Utility.GetPoint / GetDistance / GetReal / GetInteger / GetString, InputBox: null = cancelled */
  input?: (kind: "point" | "dist" | "real" | "int" | "string" | "angle", prompt: string, base?: Pt) => Promise<Pt | number | string | null>;
  /** Utility.GetEntity */
  entsel?: (prompt: string) => Promise<{ handle: string; at: Pt } | null>;
};
export type VbaOutcome = { log: string[]; changes: Changes | null; value: Val };

type Ref = { v: Val };
type Frame = { vars: Map<string, Ref>; proc: Proc | null; resume: boolean; withs: Val[] };
type Run = { host: VbaHost; prog: Program; globals: Map<string, Ref>; steps: number; deadline: number; log: string[]; depth: number; err: { number: number; description: string }; byref?: Record<number, OVal> | null; kw?: { bits: number; words: string[] }; lastInput?: string; events?: ModelEvent[]; inEvent?: boolean; watched?: Set<string> };
type ModelEvent = { kind: "added" | "modified" | "erased"; handle: string };
class Exit {
  constructor(readonly what: string) {}
}

const CONSTS: Record<string, Val> = {
  vbcrlf: "\r\n",
  vbcr: "\r",
  vblf: "\n",
  vbtab: "\t",
  vbnewline: "\n",
  vbnullstring: "",
  // VarType values (VarType, CreateTypedArray)
  vbempty: 0,
  vbnull: 1,
  vbinteger: 2,
  vblong: 3,
  vbsingle: 4,
  vbdouble: 5,
  vbstring: 8,
  vbobject: 9,
  vbboolean: 11,
  vbvariant: 12,
  vbarray: 8192,
  vbokonly: 0,
  vbokcancel: 1,
  vbyesno: 4,
  vbok: 1,
  vbcancel: 2,
  vbyes: 6,
  vbno: 7,
  vbinformation: 64,
  vbexclamation: 48,
  vbcritical: 16,
  vbquestion: 32,
  acred: 1,
  acyellow: 2,
  acgreen: 3,
  accyan: 4,
  acblue: 5,
  acmagenta: 6,
  acwhite: 7,
  acbylayer: 256,
  acbyblock: 0,
  actrue: true,
  acfalse: false,
};

const num = (v: Val, what: string): number => {
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? -1 : 0;
  if (v === null) return 0;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  throw new VbaError(`${what}: 숫자가 필요합니다 (${show(v)})`);
};
const bool = (v: Val): boolean => (typeof v === "boolean" ? v : typeof v === "number" ? v !== 0 : typeof v === "string" ? v.toLowerCase() === "true" || (v !== "" && Number(v) !== 0 && !Number.isNaN(Number(v))) : v !== null);
const str = (v: Val): string => {
  const s = show(v);
  if (s.length > VBA_LIMITS.string) throw new VbaError("문자열은 64 KB까지입니다");
  return s;
};
const finite = (n: number) => {
  if (!Number.isFinite(n)) throw new VbaError("계산 결과가 숫자가 아닙니다 (0으로 나누기 등)");
  return n;
};
const arr = (items: Val[], lower = 0): VArr => {
  if (items.length > VBA_LIMITS.array) throw new VbaError(`배열은 ${VBA_LIMITS.array.toLocaleString()}개까지입니다`);
  return { kind: "array", lower, items };
};
/** objmodel values <-> VBA values: points and coordinate lists are arrays from 0. */
const fromO = (v: OVal): Val => (Array.isArray(v) ? arr(v.map(fromO)) : (v as Val));
const toO = (v: Val): OVal => {
  if (isArr(v)) return v.items.map(toO);
  if (isSpecial(v)) throw new VbaError("이 값은 도면 객체에 쓸 수 없습니다");
  return v as OVal;
};

function equal(a: Val, b: Val): boolean {
  if (typeof a === "string" || typeof b === "string") return str(a) === str(b);
  if (a === null || b === null) return a === b || num(a ?? 0, "=") === num(b ?? 0, "=");
  return num(a, "=") === num(b, "=");
}
function compare(op: string, a: Val, b: Val): boolean {
  if (op === "=") return equal(a, b);
  if (op === "<>") return !equal(a, b);
  const both = typeof a === "string" && typeof b === "string";
  const x = both ? a : num(a, op);
  const y = both ? b : num(b, op);
  return op === "<" ? x < y : op === ">" ? x > y : op === "<=" ? x <= y : x >= y;
}

async function evalExpr(e: Expr, f: Frame, r: Run): Promise<Val> {
  if (++r.steps > VBA_LIMITS.steps) throw new VbaError(`실행 단계가 ${VBA_LIMITS.steps.toLocaleString()}을 넘었습니다 (무한 반복?)`);
  if ((r.steps & 1023) === 0 && Date.now() > r.deadline) throw new VbaError("실행 시간이 30초를 넘었습니다");
  switch (e.k) {
    case "num":
    case "str":
    case "lit":
      return e.v;
    case "with": {
      const w = f.withs.at(-1);
      if (w === undefined) throw new VbaError("With 블록 밖에서 .이름을 쓸 수 없습니다");
      return w;
    }
    case "id":
      return identifier(e.name, f, r);
    case "member":
      return member(await evalExpr(e.obj, f, r), e.name, [], r, false);
    case "call":
      return callExpr(e, f, r);
    case "un": {
      const v = await evalExpr(e.e, f, r);
      return e.op === "not" ? (typeof v === "boolean" ? !v : ~num(v, "Not")) : -num(v, "-");
    }
    case "bin": {
      if (e.op === "and" || e.op === "or" || e.op === "xor") {
        const a = await evalExpr(e.a, f, r);
        const b = await evalExpr(e.b, f, r);
        if (typeof a === "boolean" || typeof b === "boolean" || true) {
          const x = bool(a);
          const y = bool(b);
          return e.op === "and" ? x && y : e.op === "or" ? x || y : x !== y;
        }
      }
      const a = await evalExpr(e.a, f, r);
      const b = await evalExpr(e.b, f, r);
      switch (e.op) {
        case "&":
          return str(str(a) + str(b));
        case "+":
          return typeof a === "string" && typeof b === "string" ? str(a + b) : finite(num(a, "+") + num(b, "+"));
        case "-":
          return finite(num(a, "-") - num(b, "-"));
        case "*":
          return finite(num(a, "*") * num(b, "*"));
        case "/":
          return finite(num(a, "/") / num(b, "/"));
        case "\\":
          return finite(Math.trunc(Math.round(num(a, "\\")) / Math.round(num(b, "\\"))));
        case "mod":
          return finite(Math.round(num(a, "Mod")) % Math.round(num(b, "Mod")));
        case "^":
          return finite(num(a, "^") ** num(b, "^"));
        case "is":
          return a === b || (a !== null && b !== null && typeof a === "object" && typeof b === "object" && JSON.stringify(a) === JSON.stringify(b));
        case "like": // no regular expression: no catastrophic backtracking on long text
          return glob(str(b), str(a));
        default:
          return compare(e.op, a, b);
      }
    }
  }
}

function lookup(name: string, f: Frame, r: Run): Ref | undefined {
  const k = name.toLowerCase();
  return f.vars.get(k) ?? r.globals.get(k);
}

async function identifier(name: string, f: Frame, r: Run): Promise<Val> {
  const ref = lookup(name, f, r);
  if (ref) return ref.v;
  const k = name.toLowerCase();
  if (Object.hasOwn(CONSTS, k)) return CONSTS[k];
  if (k === "thisdrawing") return model(r) && ({ t: "doc" } as Obj);
  if (k === "application") return model(r) && ({ t: "app" } as Obj);
  if (k === "debug") return { kind: "debug" };
  if (k === "err") return { kind: "err" };
  if (r.prog.procs.has(k) || Object.hasOwn(BUILTINS, k)) return callNamed(name, [], f, r, []);
  throw new VbaError(`정의되지 않은 이름: ${name} (Dim으로 선언하거나 철자를 확인하세요)`);
}

const model = (r: Run): DrawingModel => {
  if (!r.host.model) throw new VbaError("이 화면에서는 ThisDrawing을 쓸 수 없습니다 (2D 작도 화면에서 실행하세요)");
  return r.host.model;
};

async function callExpr(e: Extract<Expr, { k: "call" }>, f: Frame, r: Run): Promise<Val> {
  // obj.Method(args) / obj.Item(i)
  if (e.fn.k === "member") {
    const obj = await evalExpr(e.fn.obj, f, r);
    // Utility.GetEntity ent, pt, "prompt": the picked object and point come back through ent and pt
    // GetSubEntity ent, pt, matrix, context, "prompt": also the (identity) transform and no nesting
    const pick = e.fn.name.toLowerCase();
    if (isSpecial(obj) && obj.kind === "util" && (pick === "getentity" || pick === "getsubentity")) {
      if (!r.host.entsel) throw new VbaError("이 화면에서는 객체를 고를 수 없습니다");
      const pi = pick === "getentity" ? 2 : 4;
      const prompt = e.args[pi] ? str(await evalExpr(e.args[pi], f, r)) : "객체 선택: ";
      const got = await waiting(r, () => r.host.entsel!(prompt));
      if (!got) throw new VbaError("사용자가 선택을 취소했습니다");
      if (e.args[0]) await assign(e.args[0], { t: "ent", handle: got.handle }, f, r);
      if (e.args[1]) await assign(e.args[1], arr([got.at[0], got.at[1], 0]), f, r);
      if (pick === "getsubentity") {
        if (e.args[2]) await assign(e.args[2], fromO([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]), f, r);
        if (e.args[3]) await assign(e.args[3], arr([]), f, r);
      }
      return null;
    }
    const args: Val[] = [];
    for (const a of e.args) args.push(await evalExpr(a, f, r));
    r.byref = null;
    const out = await member(obj, e.fn.name, args, r, true);
    // W4: ByRef arguments (GetBoundingBox minPt, maxPt / GetXData app, types, values)
    const refs = r.byref as Record<number, OVal> | null;
    r.byref = null;
    if (refs) for (const [i, v] of Object.entries(refs)) if (e.args[Number(i)]) await assign(e.args[Number(i)], fromO(v), f, r);
    return out;
  }
  if (e.fn.k === "id") {
    const ref = lookup(e.fn.name, f, r);
    // arr(i) or arr(i, j): an array element
    if (ref && isArr(ref.v)) {
      const idx: number[] = [];
      for (const a of e.args) idx.push(num(await evalExpr(a, f, r), "인덱스"));
      return ref.v.items[index(ref.v, idx)];
    }
    if (ref && !isArr(ref.v)) {
      // a variable holding an object followed by (...): its default member (Item) — e.g. coll(0)
      const args: Val[] = [];
      for (const a of e.args) args.push(await evalExpr(a, f, r));
      return member(ref.v, "Item", args, r, true);
    }
    const args: Val[] = [];
    for (const a of e.args) args.push(await evalExpr(a, f, r));
    return callNamed(e.fn.name, args, f, r, e.args);
  }
  const fn = await evalExpr(e.fn, f, r);
  const args: Val[] = [];
  for (const a of e.args) args.push(await evalExpr(a, f, r));
  return member(fn, "Item", args, r, true);
}

function index(a: VArr, idx: number[]): number {
  // ponytail: one dimension; a 2D array is stored row by row only when declared that way
  const i = Math.trunc(idx[0]) - a.lower;
  if (idx.length !== 1 || i < 0 || i >= a.items.length) throw new VbaError(`배열 인덱스가 범위를 벗어났습니다 (${idx.join(", ")})`);
  return i;
}

async function member(obj: Val, name: string, args: Val[], r: Run, isCall: boolean): Promise<Val> {
  const k = name.toLowerCase();
  if (obj === null) throw new VbaError(`Nothing의 ${name}을 쓸 수 없습니다 (Set으로 객체를 넣었는지 확인하세요)`);
  if (isSpecial(obj)) {
    if (obj.kind === "debug" && k === "print") {
      log(r, args.map(str).join(" "));
      return null;
    }
    if (obj.kind === "err") {
      if (k === "number") return r.err.number;
      if (k === "description") return r.err.description;
      if (k === "clear") return (r.err = { number: 0, description: "" }), null;
      if (k === "raise") throw new VbaError(args[2] !== undefined ? str(args[2]) : `오류 ${str(args[0] ?? 0)}`);
    }
    if (obj.kind === "util") return utility(k, args, r);
    throw new VbaError(`${obj.kind}에 ${name}이 없습니다`);
  }
  if (isArr(obj)) throw new VbaError(`배열에는 ${name}이 없습니다`);
  if (typeof obj !== "object") throw new VbaError(`${show(obj)}은(는) 객체가 아니라 ${name}을 쓸 수 없습니다`);
  const m = model(r);
  if (obj.t === "doc" && k === "utility") return { kind: "util" };
  try {
    if (!isCall || (!args.length && isProperty(m, obj, name))) return fromO(getProp(m, obj, name));
    const res = invoke(m, obj, name, args.map(toO));
    if (!isByRef(res)) return fromO(res);
    r.byref = res.out;
    return fromO(res.ret);
  } catch (e) {
    if (e instanceof ModelError) throw new VbaError(e.message);
    throw e;
  }
}

function isProperty(m: DrawingModel, o: Obj, name: string): boolean {
  try {
    getProp(m, o, name);
    return true;
  } catch {
    return false;
  }
}

async function utility(k: string, args: Val[], r: Run): Promise<Val> {
  const prompt = typeof args.at(-1) === "string" ? (args.at(-1) as string) : "";
  const base = args.length && isArr(args[0]) ? (args[0].items.slice(0, 2).map((x) => num(x, "기준점")) as Pt) : undefined;
  const ask = async (kind: Parameters<NonNullable<VbaHost["input"]>>[0]) => {
    if (!r.host.input) throw new VbaError("이 화면에서는 입력을 받을 수 없습니다");
    const v = await waiting(r, () => r.host.input!(kind, prompt, base));
    if (v === null) throw new VbaError("사용자가 입력을 취소했습니다");
    return v;
  };
  switch (k) {
    case "getpoint": {
      const v = (await ask("point")) as Pt;
      return arr([v[0], v[1], 0]);
    }
    case "getdistance":
      return (await ask("dist")) as number;
    case "getreal":
      return (await ask("real")) as number;
    case "getinteger":
      return (await ask("int")) as number;
    case "getstring":
      return (await ask("string")) as string;
    case "getangle":
    case "getorientation":
      return (await ask("angle")) as number;
    case "prompt":
      log(r, str(args[0] ?? ""));
      return null;
    case "angletoreal":
      return (num(args[0], "AngleToReal") * Math.PI) / 180;
    case "polarpoint": {
      const p = isArr(args[0]) ? args[0].items.map((x) => num(x, "PolarPoint")) : [0, 0];
      const a = num(args[1], "PolarPoint");
      const d = num(args[2], "PolarPoint");
      return arr([p[0] + d * Math.cos(a), p[1] + d * Math.sin(a), 0]);
    }
    // W5 (plan §22.4): the rest of the Utility object
    case "anglefromxaxis": {
      const [a, b] = [pt3(args[0], "AngleFromXAxis"), pt3(args[1], "AngleFromXAxis")];
      return (Math.atan2(b[1] - a[1], b[0] - a[0]) + 2 * Math.PI) % (2 * Math.PI);
    }
    case "angletostring":
      return angleText(num(args[0], "AngleToString"), Math.trunc(num(args[1] ?? 0, "AngleToString")), Math.trunc(num(args[2] ?? 4, "AngleToString")));
    case "realtostring":
      return realText(num(args[0], "RealToString"), Math.trunc(num(args[1] ?? 2, "RealToString")), Math.trunc(num(args[2] ?? 4, "RealToString")));
    case "distancetoreal":
      return realValue(str(args[0] ?? ""), Math.trunc(num(args[1] ?? 2, "DistanceToReal")));
    case "createtypedarray": {
      // CreateTypedArray VarArr, Type, v1, v2, ...: VarArr gets the values (types are VBA's own)
      r.byref = { 0: args.slice(2).map((v) => toO(v)) as OVal[] };
      return null;
    }
    case "getcorner": {
      const v = (await ask("point")) as Pt;
      return arr([v[0], v[1], 0]);
    }
    case "initializeuserinput": {
      const words = str(args[1] ?? "").split(/\s+/).filter(Boolean);
      if (words.length > 100 || words.some((w) => w.length > 64)) throw new VbaError("InitializeUserInput: 키워드는 100개, 64자까지입니다");
      r.kw = { bits: Math.trunc(num(args[0] ?? 0, "InitializeUserInput")), words };
      return null;
    }
    case "getkeyword": {
      const kw = r.kw;
      r.kw = undefined; // like AutoCAD: applies to the next input only
      for (;;) {
        const v = str((await ask("string")) as string).trim();
        if (!v && !(kw && kw.bits & 1)) return "";
        const hit = kw?.words.find((w) => w.toUpperCase() === v.toUpperCase() || w.replace(/[a-z]/g, "") === v.toUpperCase());
        if (hit || !kw?.words.length) return (r.lastInput = hit ?? v);
        log(r, `키워드가 아닙니다: ${kw.words.join(" / ")}`);
        tick(r);
      }
    }
    case "getinput":
      return r.lastInput ?? "";
    case "getobjectidstring": {
      const o = args[0];
      if (!o || typeof o !== "object" || isSpecial(o) || isArr(o)) throw new VbaError("GetObjectIdString: 객체가 필요합니다");
      const id = getProp(model(r), o as Obj, "ObjectID");
      return args[1] ? `${Number(id).toString(16).toUpperCase()}` : String(id);
    }
    case "translatecoordinates": {
      // AX-CAD has one coordinate system (WCS = UCS) and its display is a 2D view: the point stays
      const from = Math.trunc(num(args[1] ?? 0, "TranslateCoordinates"));
      const to = Math.trunc(num(args[2] ?? 0, "TranslateCoordinates"));
      if (from < 0 || from > 3 || to < 0 || to > 3) throw new VbaError("TranslateCoordinates: 좌표계는 0(WCS)·1(UCS)·2(화면)·3(종이)입니다");
      return arr(pt3(args[0], "TranslateCoordinates"));
    }
    case "sendmodelessoperationstart":
    case "sendmodelessoperationended":
      return null; // a script owns the drawing until it ends: nothing to tell
    case "getentity":
    case "getsubentity":
      throw new VbaError(`${k}: 결과를 인수로 받는 문장으로 쓰세요 (ThisDrawing.Utility.GetEntity obj, pt, "프롬프트")`);
  }
  throw new VbaError(`Utility에 ${k}가 없습니다`);
}

const pt3 = (v: Val | undefined, what: string): number[] => {
  if (v === undefined || !isArr(v) || v.items.length < 2) throw new VbaError(`${what}: 점 배열이 필요합니다`);
  const p = v.items.slice(0, 3).map((x) => num(x, what));
  return [p[0], p[1], p[2] ?? 0];
};
/** AngleToString: 0 degrees, 1 deg/min/sec, 2 grads, 3 radians, 4 surveyor's units. */
function angleText(a: number, unit: number, prec: number): string {
  const p = Math.max(0, Math.min(8, prec));
  const deg = (((a * 180) / Math.PI) % 360 + 360) % 360;
  switch (unit) {
    case 0:
      return `${deg.toFixed(p)}`;
    case 1: {
      const d = Math.floor(deg);
      const m = Math.floor((deg - d) * 60);
      const s = ((deg - d) * 60 - m) * 60;
      return p === 0 ? `${d}d` : p <= 2 ? `${d}d${m}'` : `${d}d${m}'${s.toFixed(Math.max(0, p - 4))}"`;
    }
    case 2:
      return `${((deg * 400) / 360).toFixed(p)}g`;
    case 3:
      return `${((deg * Math.PI) / 180).toFixed(p)}r`;
    case 4: {
      const [ns, from] = deg <= 180 ? ["N", 90] : ["S", 270];
      const off = deg - from;
      return `${ns} ${Math.abs(off).toFixed(p)}d ${(deg <= 180 ? off <= 0 : off >= 0) ? "E" : "W"}`;
    }
  }
  throw new VbaError("AngleToString: 단위는 0~4입니다");
}
/** RealToString: 1 scientific, 2 decimal, 3 engineering (feet + decimal inches), 4 architectural, 5 fractional. */
function realText(v: number, unit: number, prec: number): string {
  const p = Math.max(0, Math.min(8, prec));
  const frac = (x: number) => {
    const den = 2 ** p;
    const whole = Math.trunc(x);
    const n = Math.round(Math.abs(x - whole) * den);
    if (!n) return `${whole}`;
    const g = gcd(n, den);
    return `${whole ? `${whole} ` : x < 0 ? "-" : ""}${n / g}/${den / g}`;
  };
  switch (unit) {
    case 1:
      return v.toExponential(p).replace(/e([+-])(\d)$/, "E$10$2").toUpperCase();
    case 2:
      return v.toFixed(p);
    case 3:
      return `${Math.trunc(v / 12)}'-${(Math.abs(v) % 12).toFixed(p)}"`;
    case 4:
      return `${Math.trunc(v / 12)}'-${frac(Math.abs(v) % 12)}"`;
    case 5:
      return frac(v);
  }
  throw new VbaError("RealToString: 단위는 1~5입니다");
}
const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);
/** DistanceToReal: a distance written in one of RealToString's units back to a number (inches for feet). */
function realValue(t: string, unit: number): number {
  const s = t.trim();
  if (s.length > 64) throw new VbaError("DistanceToReal: 64자 이내의 거리 문자열이 필요합니다"); // review H2: the patterns stay small

  const ftIn = /^(-?\d+(?:\.\d+)?)'(?:\s*-?\s*(\d+(?:\.\d+)?)?(?:\s*(\d+)\/(\d+))?")?$/.exec(s);
  if ((unit === 3 || unit === 4) && ftIn) return Number(ftIn[1]) * 12 + Number(ftIn[2] ?? 0) + (ftIn[3] ? Number(ftIn[3]) / Number(ftIn[4]) : 0);
  const fr = /^(-?\d+)?\s*(?:(\d+)\/(\d+))?$/.exec(s);
  if (unit === 5 && fr && (fr[1] || fr[2])) return Number(fr[1] ?? 0) + (fr[2] ? (Number(fr[2]) / Number(fr[3])) * (s.startsWith("-") ? -1 : 1) : 0);
  const n = Number(s);
  if (s && Number.isFinite(n) && unit >= 1 && unit <= 5) return n;
  throw new VbaError(`DistanceToReal: 단위 ${unit}의 거리로 읽을 수 없습니다: ${t}`);
}

const log = (r: Run, line: string) => {
  if (r.log.length < 1000) r.log.push(line.slice(0, 2000)); // output only: the rest is dropped
};
const tick = (r: Run) => {
  if (++r.steps > VBA_LIMITS.steps) throw new VbaError(`실행 단계가 ${VBA_LIMITS.steps.toLocaleString()}을 넘었습니다 (무한 반복?)`);
  if ((r.steps & 1023) === 0 && Date.now() > r.deadline) throw new VbaError("실행 시간이 30초를 넘었습니다");
};

async function waiting<X>(r: Run, f: () => Promise<X>): Promise<X> {
  const t0 = Date.now();
  try {
    return await f();
  } finally {
    r.deadline += Date.now() - t0;
  }
}

async function callNamed(name: string, args: Val[], f: Frame, r: Run, argExprs: Expr[]): Promise<Val> {
  const k = name.toLowerCase();
  const proc = r.prog.procs.get(k);
  if (proc) return callProc(proc, args, f, r, argExprs);
  if (Object.hasOwn(BUILTINS, k)) return BUILTINS[k](args, r);
  throw new VbaError(`정의되지 않은 Sub/Function: ${name}`);
}

async function callProc(proc: Proc, args: Val[], caller: Frame, r: Run, argExprs: Expr[]): Promise<Val> {
  if (++r.depth > VBA_LIMITS.depth) throw new VbaError("호출이 너무 깊습니다 (재귀?)");
  if (args.length > proc.params.length) throw new VbaError(`${proc.name}: 인수가 너무 많습니다`);
  const frame: Frame = { vars: new Map(), proc, resume: false, withs: [] };
  for (let i = 0; i < proc.params.length; i++) {
    const prm = proc.params[i];
    let v: Val | undefined = args[i];
    if (v === undefined) {
      if (!prm.optional) throw new VbaError(`${proc.name}: 인수 ${prm.name}가 모자랍니다`);
      v = prm.def ? await evalExpr(prm.def, frame, r) : null;
    }
    // ByRef: a variable passed in shares its slot with the parameter
    const ae = argExprs[i];
    const shared = prm.byRef && ae?.k === "id" ? lookup(ae.name, caller, r) : undefined;
    frame.vars.set(prm.name.toLowerCase(), shared ?? { v: isArr(v) && !prm.byRef ? arr([...v.items], v.lower) : v });
  }
  const result: Ref = { v: null };
  if (proc.kind === "function") frame.vars.set(proc.name.toLowerCase(), result);
  try {
    await runBlock(proc.body, frame, r);
  } catch (e) {
    if (!(e instanceof Exit && (e.what === proc.kind || e.what === "sub" || e.what === "function"))) throw e;
  } finally {
    r.depth--;
  }
  return result.v;
}

async function runBlock(body: Stmt[], f: Frame, r: Run): Promise<void> {
  for (const s of body) {
    try {
      await exec(s, f, r);
      if (r.events?.length && !r.inEvent) await drainEvents(r); // W5: after the statement that changed the drawing
    } catch (e) {
      if (e instanceof Exit) throw e;
      const err = e instanceof VbaError ? e : e instanceof ModelError ? new VbaError(e.message) : null;
      if (!err) throw e;
      if (f.resume && !/실행 단계|실행 시간|너무 깊/.test(err.message)) {
        r.err = { number: 1004, description: err.message };
        continue;
      }
      throw err.line ? err : new VbaError(err.message, s.line);
    }
  }
}

async function assign(target: Expr, v: Val, f: Frame, r: Run): Promise<void> {
  if (target.k === "id") {
    const ref = lookup(target.name, f, r);
    if (ref) ref.v = v;
    else f.vars.set(target.name.toLowerCase(), { v }); // implicit declaration (no Option Explicit)
    return;
  }
  if (target.k === "call" && target.fn.k === "id") {
    const ref = lookup(target.fn.name, f, r);
    if (!ref || !isArr(ref.v)) throw new VbaError(`${target.fn.name}은(는) 배열이 아닙니다`);
    const idx: number[] = [];
    for (const a of target.args) idx.push(num(await evalExpr(a, f, r), "인덱스"));
    ref.v.items[index(ref.v, idx)] = v;
    return;
  }
  if (target.k === "member") {
    const obj = await evalExpr(target.obj, f, r);
    if (obj === null || typeof obj !== "object" || isArr(obj) || isSpecial(obj)) throw new VbaError(`${target.name}을(를) 바꿀 객체가 없습니다`);
    try {
      putProp(model(r), obj, target.name, toO(v));
    } catch (e) {
      if (e instanceof ModelError) throw new VbaError(e.message);
      throw e;
    }
    return;
  }
  throw new VbaError("값을 넣을 수 없는 곳입니다");
}

async function exec(s: Stmt, f: Frame, r: Run): Promise<void> {
  switch (s.k) {
    case "dim":
      for (const d of s.vars) {
        const v: Val = d.bounds ? await newArray(d.bounds, f, r, start(d.type)) : start(d.type);
        f.vars.set(d.name.toLowerCase(), { v });
        if (d.events && !f.proc) (r.watched ??= new Set()).add(d.name.toLowerCase()); // W5: Dim WithEvents x (module level)
      }
      return;
    case "redim": {
      const a = await newArray(s.bounds, f, r);
      const ref = lookup(s.name, f, r) ?? (f.vars.set(s.name.toLowerCase(), { v: null }), f.vars.get(s.name.toLowerCase())!);
      if (s.preserve && isArr(ref.v)) ref.v.items.slice(0, a.items.length).forEach((x, i) => (a.items[i] = x));
      ref.v = a;
      return;
    }
    case "const":
      for (const c of s.items) f.vars.set(c.name.toLowerCase(), { v: await evalExpr(c.e, f, r) });
      return;
    case "assign":
      return assign(s.target, await evalExpr(s.e, f, r), f, r);
    case "call": {
      // a bare name runs the Sub: Foo / obj.Method
      const e = s.e;
      if (e.k === "id") await callNamed(e.name, [], f, r, []);
      else if (e.k === "member") await member(await evalExpr(e.obj, f, r), e.name, [], r, true);
      else await evalExpr(e, f, r);
      return;
    }
    case "if":
      for (const b of s.branches)
        if (bool(await evalExpr(b.cond, f, r))) {
          return runBlock(b.body, f, r);
        }
      return runBlock(s.else, f, r);
    case "for": {
      const from = num(await evalExpr(s.from, f, r), "For");
      const to = num(await evalExpr(s.to, f, r), "For");
      const step = s.step ? num(await evalExpr(s.step, f, r), "Step") : 1;
      if (step === 0) throw new VbaError("Step 0은 끝나지 않습니다");
      const ref = lookup(s.v, f, r) ?? (f.vars.set(s.v.toLowerCase(), { v: from }), f.vars.get(s.v.toLowerCase())!);
      for (ref.v = from; step > 0 ? (ref.v as number) <= to : (ref.v as number) >= to; ref.v = (ref.v as number) + step) {
        tick(r); // every pass counts, also with an empty body
        try {
          await runBlock(s.body, f, r);
        } catch (e) {
          if (e instanceof Exit && e.what === "for") return;
          throw e;
        }
      }
      return;
    }
    case "foreach": {
      const coll = await evalExpr(s.coll, f, r);
      let items: Val[];
      if (isArr(coll)) items = [...coll.items];
      else if (coll && typeof coll === "object" && !isSpecial(coll) && itemsOf(model(r), coll)) items = itemsOf(model(r), coll)!;
      else throw new VbaError("For Each에는 배열이나 컬렉션(ModelSpace, Layers, Blocks, SelectionSet ...)이 필요합니다");
      const ref = lookup(s.v, f, r) ?? (f.vars.set(s.v.toLowerCase(), { v: null }), f.vars.get(s.v.toLowerCase())!);
      for (const it of items) {
        tick(r);
        ref.v = it;
        try {
          await runBlock(s.body, f, r);
        } catch (e) {
          if (e instanceof Exit && e.what === "for") return;
          throw e;
        }
      }
      return;
    }
    case "do": {
      const test = async () => (s.cond ? (s.test === "while" ? bool(await evalExpr(s.cond, f, r)) : !bool(await evalExpr(s.cond, f, r))) : true);
      for (;;) {
        if (s.pre && !(await test())) return;
        try {
          await runBlock(s.body, f, r);
        } catch (e) {
          if (e instanceof Exit && e.what === "do") return;
          throw e;
        }
        if (!s.pre && !(await test())) return;
        tick(r);
      }
    }
    case "select": {
      const v = await evalExpr(s.e, f, r);
      for (const c of s.cases) {
        for (const t of c.tests) {
          let hit: boolean;
          if ("is" in t) hit = compare(t.is, v, await evalExpr(t.e, f, r));
          else if ("lo" in t) hit = compare(">=", v, await evalExpr(t.lo, f, r)) && compare("<=", v, await evalExpr(t.hi, f, r));
          else hit = equal(v, await evalExpr(t.e, f, r));
          if (hit) return runBlock(c.body, f, r);
        }
      }
      return runBlock(s.else, f, r);
    }
    case "with": {
      f.withs.push(await evalExpr(s.obj, f, r));
      try {
        await runBlock(s.body, f, r);
      } finally {
        f.withs.pop();
      }
      return;
    }
    case "exit":
      throw new Exit(s.what);
    case "onerror":
      f.resume = s.resume;
      if (s.resume) r.err = { number: 0, description: "" };
      return;
  }
}

/** A string length checked before the string is made. */
function size(n: number): number {
  if (!(n <= VBA_LIMITS.string)) throw new VbaError("문자열은 64 KB까지입니다");
  return Math.max(0, Math.trunc(n));
}

/** The value a Dim starts with: numbers 0, String "", Boolean False, the rest Empty/Nothing. */
function start(type: string): Val {
  if (["integer", "long", "longlong", "double", "single", "byte", "currency", "decimal"].includes(type)) return 0;
  if (type === "string") return "";
  if (type === "boolean") return false;
  return null;
}

async function newArray(bounds: Bound[], f: Frame, r: Run, fill: Val = 0): Promise<VArr> {
  if (bounds.length !== 1) throw new VbaError("배열은 1차원만 됩니다 (Dim a(0 To 5))");
  const lo = Math.trunc(num(await evalExpr(bounds[0].lo, f, r), "배열 크기"));
  const hi = Math.trunc(num(await evalExpr(bounds[0].hi, f, r), "배열 크기"));
  if (hi < lo - 1) throw new VbaError("배열 범위가 잘못되었습니다");
  return arr(Array.from({ length: hi - lo + 1 }, () => fill ?? 0), lo);
}

// --- built-in functions ---------------------------------------------------------------------

const BUILTINS: Record<string, (a: Val[], r: Run) => Val | Promise<Val>> = {
  msgbox: (a, r) => (log(r, str(a[0] ?? "")), 1),
  inputbox: async (a, r) => {
    if (!r.host.input) throw new VbaError("이 화면에서는 입력을 받을 수 없습니다");
    const v = await waiting(r, () => r.host.input!("string", str(a[0] ?? "")));
    return v === null ? "" : String(v);
  },
  len: (a) => str(a[0] ?? "").length,
  left: (a) => str(a[0]).slice(0, Math.max(0, num(a[1], "Left"))),
  right: (a) => {
    const s = str(a[0]);
    const n = Math.max(0, num(a[1], "Right"));
    return n ? s.slice(-n) : "";
  },
  mid: (a) => {
    const s = str(a[0]);
    const st = num(a[1], "Mid") - 1;
    return a[2] !== undefined ? s.substr(st, num(a[2], "Mid")) : s.slice(st);
  },
  instr: (a) => {
    const [start, s, t] = typeof a[0] === "number" ? [num(a[0], "InStr"), str(a[1]), str(a[2])] : [1, str(a[0]), str(a[1])];
    return s.indexOf(t, start - 1) + 1;
  },
  replace: (a) => str(str(a[0]).split(str(a[1])).join(str(a[2]))),
  ucase: (a) => str(a[0]).toUpperCase(),
  lcase: (a) => str(a[0]).toLowerCase(),
  trim: (a) => str(a[0]).trim(),
  ltrim: (a) => str(a[0]).replace(/^ +/, ""),
  rtrim: (a) => str(a[0]).replace(/ +$/, ""),
  space: (a) => " ".repeat(size(num(a[0], "Space"))),
  string: (a) => str(a[1]).charAt(0).repeat(size(num(a[0], "String"))),
  chr: (a) => String.fromCharCode(num(a[0], "Chr")),
  asc: (a) => str(a[0]).charCodeAt(0) || 0,
  cstr: (a) => str(a[0]),
  str: (a) => (num(a[0], "Str") >= 0 ? " " : "") + str(num(a[0], "Str")),
  cdbl: (a) => num(a[0], "CDbl"),
  csng: (a) => num(a[0], "CSng"),
  cint: (a) => Math.round(num(a[0], "CInt")),
  clng: (a) => Math.round(num(a[0], "CLng")),
  cbool: (a) => bool(a[0]),
  val: (a) => {
    const m = /^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?/.exec(str(a[0]));
    return m ? Number(m[0]) : 0;
  },
  format: (a) => {
    const n = num(a[0], "Format");
    const pat = a[1] !== undefined ? str(a[1]) : "";
    const dec = /\.(0+)/.exec(pat)?.[1].length ?? (pat ? 0 : undefined);
    return dec === undefined ? str(n) : n.toFixed(dec);
  },
  isnumeric: (a) => typeof a[0] === "number" || (typeof a[0] === "string" && a[0].trim() !== "" && Number.isFinite(Number(a[0]))),
  isempty: (a) => a[0] === null || a[0] === "",
  isnothing: (a) => a[0] === null,
  isarray: (a) => isArr(a[0] ?? null),
  isobject: (a) => typeof a[0] === "object" && a[0] !== null && !isArr(a[0]),
  abs: (a) => Math.abs(num(a[0], "Abs")),
  sgn: (a) => Math.sign(num(a[0], "Sgn")),
  int: (a) => Math.floor(num(a[0], "Int")),
  fix: (a) => Math.trunc(num(a[0], "Fix")),
  round: (a) => {
    const d = a[1] !== undefined ? num(a[1], "Round") : 0;
    return Math.round(num(a[0], "Round") * 10 ** d) / 10 ** d;
  },
  sqr: (a) => finite(Math.sqrt(num(a[0], "Sqr"))),
  sin: (a) => Math.sin(num(a[0], "Sin")),
  cos: (a) => Math.cos(num(a[0], "Cos")),
  tan: (a) => finite(Math.tan(num(a[0], "Tan"))),
  atn: (a) => Math.atan(num(a[0], "Atn")),
  exp: (a) => finite(Math.exp(num(a[0], "Exp"))),
  log: (a) => finite(Math.log(num(a[0], "Log"))),
  rnd: () => Math.random(),
  array: (a) => arr([...a]),
  ubound: (a) => {
    const v = a[0];
    if (!v || !isArr(v)) throw new VbaError("UBound: 배열이 필요합니다");
    return v.lower + v.items.length - 1;
  },
  lbound: (a) => {
    const v = a[0];
    if (!v || !isArr(v)) throw new VbaError("LBound: 배열이 필요합니다");
    return v.lower;
  },
  split: (a) => arr(str(a[0]).split(a[1] !== undefined ? str(a[1]) : " ")),
  join: (a) => {
    const v = a[0];
    if (!v || !isArr(v)) throw new VbaError("Join: 배열이 필요합니다");
    return str(v.items.map(str).join(a[1] !== undefined ? str(a[1]) : " "));
  },
  typename: (a, r) => {
    const v = a[0] ?? null;
    if (v === null) return "Nothing";
    if (typeof v === "number") return Number.isInteger(v) ? "Integer" : "Double";
    if (typeof v === "string") return "String";
    if (typeof v === "boolean") return "Boolean";
    if (isArr(v)) return "Variant()";
    if (isSpecial(v)) return v.kind;
    if (v.t === "table" || v.t === "rec") return `IAcad${describe(model(r), v).replace(/s$/, v.t === "table" ? "s" : "")}`;
    return ({ app: "IAcadApplication", doc: "IAcadDocument", space: "IAcadModelSpace", layers: "IAcadLayers", layer: "IAcadLayer", ent: "IAcadEntity", layout: "IAcadLayout" } as Record<string, string>)[v.t] ?? `IAcad${describe(model(r), v)}`;
  },
  iif: (a) => (bool(a[0]) ? a[1] : a[2]) ?? null,
};

// --- run ------------------------------------------------------------------------------------

/** Public Subs without parameters: the macros a user can run (VBARUN). */
export function macros(prog: Program): string[] {
  return [...prog.procs.values()].filter((p) => p.kind === "sub" && !p.isPrivate && !p.params.length).map((p) => p.name);
}

/** Runs one macro (default: the only parameterless Sub, or Main). */
export async function runVba(src: string, host: VbaHost = {}, macro?: string): Promise<VbaOutcome & { macros: string[] }> {
  const prog = parse(src);
  const list = macros(prog);
  const name = macro ?? (prog.procs.has("main") ? "main" : list.length === 1 ? list[0] : null);
  if (!name) throw new VbaError(list.length ? `실행할 매크로를 고르세요: ${list.join(", ")}` : "실행할 Sub가 없습니다 (Sub 이름() ... End Sub)");
  const proc = prog.procs.get(name.toLowerCase());
  if (!proc || proc.kind !== "sub") throw new VbaError(`매크로가 없습니다: ${name}`);
  if (proc.params.length) throw new VbaError(`${proc.name}: 인수가 있는 Sub는 매크로로 실행할 수 없습니다`);
  const r: Run = { host, prog, globals: new Map(), steps: 0, deadline: Date.now() + VBA_LIMITS.ms, log: [], depth: 0, err: { number: 0, description: "" } };
  r.host = { ...host, ...(host.model ? { model: watchModel(host.model, r) } : {}) };
  const top: Frame = { vars: r.globals, proc: null, resume: false, withs: [] };
  try {
    await runBlock(prog.module, top, r);
    const value = await callProc(proc, [], top, r, []);
    if (r.events?.length) await drainEvents(r);
    return { log: r.log, changes: host.model ? host.model.changes() : null, value, macros: list };
  } catch (e) {
    if (e instanceof Exit) throw new VbaError(`Exit ${e.what}가 맞는 블록 밖에 있습니다`);
    if (e instanceof RangeError) throw new VbaError("호출이 너무 깊습니다");
    throw e;
  }
}

export const vbaObject = isModelObj;

// --- W5 (plan §22.4): events -------------------------------------------------------------------

/** Document events a VBA project answers with `Sub ThisDrawing_<Event>(...)` (AutoCAD's names). */
export const VBA_DOC_EVENTS = ["BeginCommand", "EndCommand", "BeginLisp", "EndLisp", "LispCancelled", "ObjectAdded", "ObjectErased", "ObjectModified"] as const;
export type VbaDocEvent = (typeof VBA_DOC_EVENTS)[number];
let lastParsed: { src: string; prog: Program } | null = null;
const parsedOnce = (src: string): Program => (lastParsed?.src === src ? lastParsed.prog : (lastParsed = { src, prog: parse(src) }).prog);
/** The ThisDrawing_* handlers a project has (empty: it answers no event and need not stay loaded). */
export function eventHandlers(src: string): VbaDocEvent[] {
  const prog = parsedOnce(src);
  return VBA_DOC_EVENTS.filter((e) => prog.procs.get(`thisdrawing_${e.toLowerCase()}`)?.kind === "sub");
}
const MAX_EVENTS = 1000; // per run: what one macro's changes may wake
/** The model as a run sees it: its own changes are queued as ObjectAdded / Modified / Erased. */
function watchModel(m: DrawingModel, r: Run): DrawingModel {
  const push = (kind: ModelEvent["kind"], handle: string) => {
    if (r.inEvent) return; // changes made by event handlers wake nothing (no loops)
    const q = (r.events ??= []);
    if (q.length < MAX_EVENTS) q.push({ kind, handle });
  };
  return new Proxy(m, {
    get(t, k) {
      if (k === "add") return (...a: Parameters<DrawingModel["add"]>) => { const e = t.add(...a); push("added", e.handle); return e; };
      if (k === "set") return (...a: Parameters<DrawingModel["set"]>) => { const e = t.set(...a); push("modified", e.handle); return e; };
      if (k === "remove") return (h: string) => { t.remove(h); push("erased", h); };
      const v = Reflect.get(t, k, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
}
const objectId = (h: string) => (/^[0-9A-F]+$/i.test(h) ? parseInt(h, 16) : 0);
async function handle(r: Run, name: string, args: Val[]): Promise<void> {
  const p = r.prog.procs.get(name.toLowerCase());
  if (!p || p.kind !== "sub") return;
  await callProc(p, args.slice(0, p.params.length), { vars: r.globals, proc: null, resume: false, withs: [] }, r, []);
}
async function drainEvents(r: Run): Promise<void> {
  r.inEvent = true;
  try {
    while (r.events?.length) {
      const ev = r.events.shift()!;
      const obj: Obj = { t: "ent", handle: ev.handle };
      if (ev.kind === "added") await handle(r, "thisdrawing_objectadded", [obj]);
      if (ev.kind === "erased") await handle(r, "thisdrawing_objecterased", [objectId(ev.handle)]);
      if (ev.kind !== "modified") continue;
      await handle(r, "thisdrawing_objectmodified", [obj]);
      // Dim WithEvents x As AcadLine ... Sub x_Modified(ByVal pObject As AcadObject)
      for (const w of r.watched ?? []) {
        const v = r.globals.get(w)?.v;
        if (v && typeof v === "object" && !isArr(v) && !isSpecial(v) && (v as Obj).t === "ent" && (v as { handle: string }).handle === ev.handle) await handle(r, `${w}_modified`, [obj]);
      }
    }
  } finally {
    r.inEvent = false;
  }
}
/** Fires one document event at a loaded project (null: it has no handler for it). The handler's
 * own changes come back like a macro's; the events they would raise are not fired (no loops). */
export async function fireVbaEvent(src: string, event: VbaDocEvent, args: OVal[], host: VbaHost = {}, ms: number = VBA_LIMITS.ms): Promise<VbaOutcome | null> {
  const prog = parsedOnce(src); // review H1: a loaded project is parsed once, not for every event
  if (prog.procs.get(`thisdrawing_${event.toLowerCase()}`)?.kind !== "sub") return null;
  if (!(ms > 0)) throw new VbaError("이벤트 처리 시간이 다 됐습니다");
  // review H1: the caller gives what is left of its budget (one user command's events share it)
  const r: Run = { host, prog, globals: new Map(), steps: 0, deadline: Date.now() + Math.min(ms, VBA_LIMITS.ms), log: [], depth: 0, err: { number: 0, description: "" }, inEvent: true };
  const top: Frame = { vars: r.globals, proc: null, resume: false, withs: [] };
  try {
    await runBlock(prog.module, top, r);
    await handle(r, `thisdrawing_${event}`, args.map(fromO));
    return { log: r.log, changes: host.model ? host.model.changes() : null, value: null };
  } catch (e) {
    if (e instanceof Exit) throw new VbaError(`Exit ${e.what}가 맞는 블록 밖에 있습니다`);
    if (e instanceof RangeError) throw new VbaError("호출이 너무 깊습니다");
    throw e;
  }
}
