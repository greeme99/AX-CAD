// Track 11: AX-LISP — a safe AutoLISP subset (plan.md §17). Its own reader and evaluator: no eval,
// network or OS; the drawing changes through (command ...) inputs, which the screen runs through the
// command prompt reducer afterwards, and (2D automation A1, plan.md §20) through entmake/entmod/
// entdel and vla- objects on a copy of the drawing (objmodel.ts) — all applied as one undo step,
// nothing on an error. Files are the PC LISP library only; DCL dialogs are drawn by the screen.
import type { Pt } from "./view";
import type { Sel } from "./tools";
import { parseDcl, type Tile } from "./dcl";
import { wcmatch } from "./wcmatch";
import { DrawingModel, fromGroups, getProp, invoke, isModelObj, matches, ModelError, putProp, toGroups, type Changes, type Ent, type Group, type Obj, type OVal, type TableKind } from "./objmodel";

export class Sym {
  private static all = new Map<string, Sym>();
  private constructor(readonly name: string) {}
  static of(name: string): Sym {
    const n = name.toUpperCase();
    let s = Sym.all.get(n);
    if (!s) Sym.all.set(n, (s = new Sym(n)));
    return s;
  }
}
export type Pair = { kind: "pair"; car: Val; cdr: Val };
export type SelSet = { kind: "ss"; items: Sel[] };
export type Ename = { kind: "ename"; sel: Sel };
type Builtin = { kind: "builtin"; name: string; fn: (args: Val[], c: Run) => Val | Promise<Val> };
type Lambda = { kind: "lambda"; name: string; params: Sym[]; locals: Sym[]; body: Val[] };
export type Vla = { kind: "vla"; obj: Obj };
export type FileDesc = { kind: "file"; name: string; mode: "r" | "w" | "a"; text: string; pos: number; open: boolean };
export type Reactor = { kind: "reactor"; type: "command" | "object"; data: Val; owners: string[]; events: [string, Val][]; active: boolean };
export type Val = number | string | Sym | Val[] | Pair | SelSet | Ename | Builtin | Lambda | Vla | FileDesc | Reactor;

export const LIMITS = { source: 256 * 1024, steps: 1_000_000, depth: 200, list: 20_000, string: 64 * 1024, inputs: 20_000, ms: 30_000 };
const NIL: Val[] = [];
const T = Sym.of("T");

export class LispError extends Error {
  constructor(
    message: string,
    readonly line?: number,
  ) {
    super(message);
  }
}
/** Steps, time and depth limits: never caught by vl-catch-all-apply. */
export class LimitError extends LispError {}

// --- reader -------------------------------------------------------------------------------

type Tok = { t: string; line: number; str?: boolean };

function tokens(src: string): Tok[] {
  if (src.length > LIMITS.source) throw new LispError("프로그램이 너무 큽니다 (256 KB 이하)");
  const out: Tok[] = [];
  let line = 1;
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === "\n") line++;
    if (/\s/.test(c)) {
      i++;
    } else if (c === ";") {
      if (src[i + 1] === "|") {
        const end = src.indexOf("|;", i + 2);
        const stop = end < 0 ? src.length : end + 2;
        line += (src.slice(i, stop).match(/\n/g) ?? []).length;
        i = stop;
      } else {
        while (i < src.length && src[i] !== "\n") i++;
      }
    } else if (c === "(" || c === ")" || c === "'") {
      out.push({ t: c, line });
      i++;
    } else if (c === '"') {
      let s = "";
      const start = line;
      i++;
      while (i < src.length && src[i] !== '"') {
        if (src[i] === "\\" && i + 1 < src.length) {
          const e = src[++i];
          s += e === "n" ? "\n" : e === "t" ? "\t" : e;
        } else {
          if (src[i] === "\n") line++;
          s += src[i];
        }
        i++;
      }
      if (i >= src.length) throw new LispError("닫히지 않은 문자열", start);
      i++;
      out.push({ t: s, line: start, str: true });
    } else {
      let j = i;
      while (j < src.length && !/[\s()';"]/.test(src[j])) j++;
      out.push({ t: src.slice(i, j), line });
      i = j;
    }
  }
  return out;
}

const NUMBER = /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/;

/** Source -> top-level forms. */
export function read(src: string): Val[] {
  const toks = tokens(src);
  let p = 0;
  const one = (depth: number): Val => {
    if (depth > LIMITS.depth) throw new LispError("괄호가 너무 깊습니다", toks[p]?.line);
    const tk = toks[p++];
    if (tk.str) return tk.t;
    if (tk.t === "'") {
      if (p >= toks.length) throw new LispError("' 뒤에 값이 없습니다", tk.line);
      return [Sym.of("QUOTE"), one(depth + 1)];
    }
    if (tk.t === ")") throw new LispError("짝이 없는 )", tk.line);
    if (tk.t === "(") {
      const items: Val[] = [];
      while (true) {
        if (p >= toks.length) throw new LispError("닫히지 않은 (", tk.line);
        if (toks[p].t === ")" && !toks[p].str) {
          p++;
          return items;
        }
        if (toks[p].t === "." && !toks[p].str) {
          p++;
          const tail = one(depth + 1);
          if (toks[p]?.t !== ")") throw new LispError("점 쌍 뒤에는 )가 와야 합니다", tk.line);
          p++;
          if (items.length !== 1) throw new LispError("점 쌍은 (a . b) 꼴만 씁니다", tk.line);
          return { kind: "pair", car: items[0], cdr: tail };
        }
        items.push(one(depth + 1));
      }
    }
    if (NUMBER.test(tk.t)) return Number(tk.t);
    return tk.t.toUpperCase() === "NIL" ? NIL : Sym.of(tk.t);
  };
  const forms: Val[] = [];
  while (p < toks.length) forms.push(one(0));
  return forms;
}

// --- values -------------------------------------------------------------------------------

const isList = (v: Val): v is Val[] => Array.isArray(v);
const isPair = (v: Val): v is Pair => typeof v === "object" && !Array.isArray(v) && !(v instanceof Sym) && v.kind === "pair";
const truthy = (v: Val) => !(isList(v) && v.length === 0);
const bool = (b: boolean): Val => (b ? T : NIL);

export function show(v: Val): string {
  if (v === undefined || v === null) return "(없음)"; // a missing argument: the message, not a TypeError
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(8)));
  if (typeof v === "string") return JSON.stringify(v);
  if (v instanceof Sym) return v.name;
  if (isList(v)) return v.length ? `(${v.map(show).join(" ")})` : "nil";
  if (v.kind === "pair") return `(${show(v.car)} . ${show(v.cdr)})`;
  if (v.kind === "ss") return `<선택 집합 ${v.items.length}>`;
  if (v.kind === "ename") return `<객체 ${v.sel.handle}>`;
  if (v.kind === "vla") return `#<VLA-OBJECT ${v.obj.t === "ent" ? v.obj.handle : v.obj.t}>`;
  if (v.kind === "file") return `#<file "${v.name}">`;
  if (v.kind === "reactor") return `#<VLR-${v.type === "command" ? "Command" : "Object"}-Reactor>`;
  return `<함수 ${v.name}>`;
}

const num = (v: Val, fn: string): number => {
  if (typeof v !== "number" || !Number.isFinite(v)) throw new LispError(`${fn}: 숫자가 필요합니다 (${show(v)})`);
  return v;
};
const str = (v: Val, fn: string): string => {
  if (typeof v !== "string") throw new LispError(`${fn}: 문자열이 필요합니다 (${show(v)})`);
  return v;
};
const list = (v: Val, fn: string): Val[] => {
  if (!isList(v)) throw new LispError(`${fn}: 목록이 필요합니다 (${show(v)})`);
  return v;
};
const pt = (v: Val, fn: string): Pt => {
  const l = list(v, fn);
  if (l.length < 2) throw new LispError(`${fn}: 점 (x y)이 필요합니다 (${show(v)})`);
  return [num(l[0], fn), num(l[1], fn)];
};
const out = (v: number): number => {
  if (!Number.isFinite(v)) throw new LispError("계산 결과가 숫자가 아닙니다 (0으로 나누기 등)");
  return v;
};
const cap = <X>(xs: X[]): X[] => {
  if (xs.length > LIMITS.list) throw new LispError(`목록은 ${LIMITS.list.toLocaleString()}개까지입니다`);
  return xs;
};
const capStr = (s: string): string => {
  if (s.length > LIMITS.string) throw new LispError("문자열은 64 KB까지입니다");
  return s;
};

// --- host: what the screen offers ----------------------------------------------------------

export type InputKind = "point" | "dist" | "real" | "int" | "string" | "angle";
export type Host = {
  /** get* functions: null = the user cancelled (AutoLISP returns nil). */
  input?: (kind: InputKind, prompt: string, base?: Pt) => Promise<Pt | number | string | null>;
  entsel?: (prompt: string) => Promise<{ sel: Sel; at: Pt } | null>;
  /** ssget: the current selection ("I" / no mode) or a window pick; [] = nothing. */
  ssget?: (mode: string | null) => Promise<Sel[]>;
  layers?: string[];
  layer?: string;
  /** 3D screen: points keep z ("x,y,z") */
  point3?: boolean;
  /** entmake/entmod/entdel/vla-: the editable drawing as objects (a copy; changes() is applied). */
  model?: DrawingModel;
  /** open/read-line/write-line, load_dialog, findfile: files of the PC LISP library only. */
  files?: { read: (name: string) => Promise<string | null>; write: (name: string, text: string) => Promise<void> };
  /** start_dialog: the screen draws the DCL dialog; act() runs the tile's action and says when it ends. */
  dialog?: (d: DialogRun) => Promise<number>;
};

/** A DCL dialog while it is open: its tiles, their current state, and the action runner. */
export type DialogRun = {
  tile: Tile;
  state: () => DialogState;
  act: (key: string, value: string) => Promise<{ done: number | null; state: DialogState }>;
};
export type DialogState = { values: Record<string, string>; lists: Record<string, string[]>; disabled: string[] };

/** What stays between runs on one screen (AutoCAD keeps a drawing's LISP namespace): defined
 * functions and variables, and the reactors waiting for events. */
export type Session = { globals: Map<Sym, Val>; reactors: Reactor[] };
export const newSession = (): Session => ({ globals: new Map(), reactors: [] });

/** A run of command prompt inputs with the selection and current layer they use. */
export type Segment = { inputs: string[]; sel: Sel[]; layer?: string };
export type Outcome = { segments: Segment[]; log: string[]; commands: string[]; value: Val; changes: Changes | null };

// Maps, not objects: tile keys come from the program ("constructor", "__proto__" are just keys)
type Dlg = { tile: Tile; values: Map<string, string>; lists: Map<string, string[]>; disabled: Set<string>; actions: Map<string, string>; done: number | null; open: boolean; list?: { key: string; op: number; index: number } };
type Run = {
  host: Host;
  steps: number;
  deadline: number;
  segs: Segment[];
  inputs: number;
  log: string[];
  layer?: string;
  globals: Map<Sym, Val>;
  session: Session;
  dcl: Map<number, Map<string, Tile>>;
  dlg: Dlg | null;
  files: number;
  depth: number;
  kw: { bits: number; words: string[] } | null; // A5: initget's options for the next getkword
  loading: number; // A5: nesting of (load ...)
};
type Env = { vars: Map<Sym, Val>; parent: Env | null };

const fmt = (n: number) => String(Number(n.toFixed(6)));

/** One (command ...) call -> prompt inputs; a selection argument starts its own segment. */
function command(args: Val[], c: Run): Val {
  const sel = args.find((a) => typeof a === "object" && !isList(a) && !(a instanceof Sym) && (a.kind === "ss" || a.kind === "ename")) as SelSet | Ename | undefined;
  const items: string[] = [];
  let skipEnter = false;
  let undo = 0; // A5: arguments of an UNDO command still to skip
  for (const a of args) {
    // a routine is one undo step on the screen: (command "_.undo" "_begin") ... "_end" only groups what is grouped already
    if (typeof a === "string" && !items.length && /^[_.]*UNDO$/i.test(a)) {
      undo = 2;
      log(c, "undo는 한 번의 실행 취소로 묶이므로 무시했습니다");
      continue;
    }
    if (undo > 0 && ((typeof a === "string" && /^[_.]*(BE|BEGIN|E|END|G|GROUP|M|MARK|B|BACK|A|AUTO|C|CONTROL|ALL|N|NONE|O|ONE|ON|OFF|COMBINE|LAYER)$/i.test(a)) || typeof a === "number")) {
      undo--;
      continue;
    }
    undo = 0;
    if (typeof a === "number") items.push(fmt(a));
    else if (typeof a === "string") {
      if (skipEnter && a === "") {
        skipEnter = false; // the "" that ends object selection in AutoLISP
        continue;
      }
      items.push(items.length ? a : a.replace(/^[_.]+/, "")); // "_.LINE" -> "LINE"
    } else if (isList(a) && a.length >= 2 && a.every((x) => typeof x === "number")) items.push((c.host.point3 ? a.slice(0, 3) : a.slice(0, 2)).map((x) => fmt(x as number)).join(","));
    else if (isList(a) && a.length === 0) items.push("");
    else if (a === sel) skipEnter = true;
    else throw new LispError(`command: 쓸 수 없는 인수 ${show(a)}`);
  }
  if (sel && (sel.kind === "ss" ? sel.items : [sel.sel]).some((s) => s.handle.startsWith("~")))
    throw new LispError("command: 이번 실행에서 만든 객체는 command로 바꿀 수 없습니다 (vla-Move 같은 객체 함수를 쓰세요)");
  if (sel) c.segs.push({ inputs: [], sel: sel.kind === "ss" ? sel.items : [sel.sel], layer: c.layer });
  else if (!c.segs.length || c.segs[c.segs.length - 1].sel.length || c.segs[c.segs.length - 1].layer !== c.layer) c.segs.push({ inputs: [], sel: [], layer: c.layer });
  c.inputs += items.length;
  if (c.inputs > LIMITS.inputs) throw new LispError(`명령 입력은 ${LIMITS.inputs.toLocaleString()}개까지입니다`);
  c.segs[c.segs.length - 1].inputs.push(...items);
  return NIL;
}

function lookup(s: Sym, env: Env, c: Run): Val {
  for (let e: Env | null = env; e; e = e.parent) if (e.vars.has(s)) return e.vars.get(s)!;
  if (c.globals.has(s)) return c.globals.get(s)!;
  if (s.name.startsWith(":")) return s; // :vlax-true, :vlr-commandEnded ... evaluate to themselves
  if (s.name === "PI") return Math.PI;
  const b = BUILTINS.get(s.name) ?? vlaFunction(s.name);
  if (b) return b;
  return NIL; // AutoLISP: an unset symbol is nil
}

function assign(s: Sym, v: Val, env: Env, c: Run) {
  for (let e: Env | null = env; e; e = e.parent)
    if (e.vars.has(s)) {
      e.vars.set(s, v);
      return;
    }
  c.globals.set(s, v);
}

/** Every evaluation and every function call counts: built-in callbacks (mapcar, vl-sort, ...) too. */
function tick(c: Run, depth: number) {
  if (++c.steps > LIMITS.steps) throw new LimitError(`실행 단계가 ${LIMITS.steps.toLocaleString()}을 넘었습니다 (무한 반복?)`);
  if (depth > LIMITS.depth) throw new LimitError("함수 호출이 너무 깊습니다 (재귀?)");
  if ((c.steps & 1023) === 0 && Date.now() > c.deadline) throw new LimitError("실행 시간이 30초를 넘었습니다");
}

async function call(f: Val, args: Val[], env: Env, c: Run, depth: number): Promise<Val> {
  tick(c, depth);
  // AutoLISP takes a quoted lambda or a function's name wherever a function goes ('(lambda ...), 'sq)
  if (isList(f) && f[0] instanceof Sym && f[0].name === "LAMBDA") f = lambda("lambda", f[1], f.slice(2));
  else if (f instanceof Sym) f = lookup(f, env, c);
  if (typeof f !== "object" || isList(f) || f instanceof Sym || (f.kind !== "builtin" && f.kind !== "lambda")) throw new LispError(`함수가 아닙니다: ${show(f)}`);
  if (f.kind === "builtin") {
    const outer = c.depth;
    c.depth = depth; // callbacks a builtin makes (mapcar, vl-sort, actions) go deeper, never back to 1
    try {
      return await f.fn(args, c);
    } finally {
      c.depth = outer;
    }
  }
  if (args.length > f.params.length) throw new LispError(`${f.name}: 인수가 너무 많습니다`);
  if (args.length < f.params.length) throw new LispError(`${f.name}: 인수가 모자랍니다`);
  // AutoLISP scope is dynamic: the callee sees the caller's variables
  const vars = new Map<Sym, Val>();
  f.params.forEach((p, i) => vars.set(p, args[i]));
  f.locals.forEach((l) => vars.set(l, NIL));
  const inner: Env = { vars, parent: env };
  let r: Val = NIL;
  for (const x of f.body) r = await ev(x, inner, c, depth + 1);
  return r;
}

async function ev(x: Val, env: Env, c: Run, depth: number): Promise<Val> {
  tick(c, depth);
  if (x instanceof Sym) return x === T ? T : lookup(x, env, c);
  if (!isList(x) || x.length === 0) return x;
  const [head, ...rest] = x;
  if (head instanceof Sym) {
    switch (head.name) {
      case "QUOTE":
        return rest[0] ?? NIL;
      case "SETQ": {
        let v: Val = NIL;
        for (let i = 0; i < rest.length; i += 2) {
          const s = rest[i];
          if (!(s instanceof Sym) || s === T) throw new LispError(`setq: 변수 이름이 필요합니다 (${show(s)})`);
          v = await ev(rest[i + 1] ?? NIL, env, c, depth + 1);
          assign(s, v, env, c);
        }
        return v;
      }
      case "IF":
        return truthy(await ev(rest[0], env, c, depth + 1)) ? ev(rest[1] ?? NIL, env, c, depth + 1) : ev(rest[2] ?? NIL, env, c, depth + 1);
      case "COND":
        for (const clause of rest) {
          const cl = list(clause, "cond");
          const t = await ev(cl[0], env, c, depth + 1);
          if (truthy(t)) {
            let r = t;
            for (const b of cl.slice(1)) r = await ev(b, env, c, depth + 1);
            return r;
          }
        }
        return NIL;
      case "PROGN": {
        let r: Val = NIL;
        for (const b of rest) r = await ev(b, env, c, depth + 1);
        return r;
      }
      case "AND": {
        for (const b of rest) if (!truthy(await ev(b, env, c, depth + 1))) return NIL;
        return T;
      }
      case "OR": {
        for (const b of rest) if (truthy(await ev(b, env, c, depth + 1))) return T;
        return NIL;
      }
      case "WHILE": {
        let r: Val = NIL;
        while (truthy(await ev(rest[0], env, c, depth + 1))) for (const b of rest.slice(1)) r = await ev(b, env, c, depth + 1);
        return r;
      }
      case "REPEAT": {
        const n = num(await ev(rest[0], env, c, depth + 1), "repeat");
        let r: Val = NIL;
        for (let i = 0; i < Math.trunc(n); i++) for (const b of rest.slice(1)) r = await ev(b, env, c, depth + 1);
        return r;
      }
      case "FOREACH": {
        const v = rest[0];
        if (!(v instanceof Sym)) throw new LispError("foreach: 변수 이름이 필요합니다");
        const items = list(await ev(rest[1], env, c, depth + 1), "foreach");
        const inner: Env = { vars: new Map(), parent: env };
        let r: Val = NIL;
        for (const it of items) {
          inner.vars.set(v, it);
          for (const b of rest.slice(2)) r = await ev(b, inner, c, depth + 1);
        }
        return r;
      }
      case "VLAX-FOR": {
        const v = rest[0];
        if (!(v instanceof Sym)) throw new LispError("vlax-for: 변수 이름이 필요합니다");
        const coll = vla(await ev(rest[1], env, c, depth + 1), "vlax-for");
        const m = model(c, "vlax-for");
        const items: Val[] =
          coll.t === "space" ? m.all().map((e) => ({ kind: "vla", obj: { t: "ent", handle: e.handle } })) : coll.t === "layers" ? m.layers.map((l) => ({ kind: "vla", obj: { t: "layer", name: l.name } })) : [];
        if (coll.t !== "space" && coll.t !== "layers") throw new LispError("vlax-for: ModelSpace나 Layers가 필요합니다");
        const inner: Env = { vars: new Map(), parent: env };
        let r: Val = NIL;
        for (const it of items) {
          inner.vars.set(v, it);
          for (const b of rest.slice(2)) r = await ev(b, inner, c, depth + 1);
        }
        return r;
      }
      case "LAMBDA":
        return lambda("lambda", rest[0], rest.slice(1));
      // 2D automation A5: forms that need the caller's variables (cadcraft/AutoLISP core)
      case "FUNCTION": {
        const f = rest[0] ?? NIL;
        return isList(f) && f[0] instanceof Sym && f[0].name === "LAMBDA" ? lambda("lambda", f[1], f.slice(2)) : f;
      }
      case "SET": {
        const s = await ev(rest[0], env, c, depth + 1);
        if (!(s instanceof Sym) || s === T) throw new LispError(`set: 변수 이름이 필요합니다 (${show(s)})`);
        const v = await ev(rest[1] ?? NIL, env, c, depth + 1);
        assign(s, v, env, c);
        return v;
      }
      case "BOUNDP": {
        const s = await ev(rest[0], env, c, depth + 1);
        if (!(s instanceof Sym)) throw new LispError(`boundp: 기호가 필요합니다 (${show(s)})`);
        return bool(s === T || s.name === "PI" || truthy(lookup(s, env, c)));
      }
      case "VL-SYMBOL-VALUE": {
        const s = await ev(rest[0], env, c, depth + 1);
        if (!(s instanceof Sym)) throw new LispError(`vl-symbol-value: 기호가 필요합니다 (${show(s)})`);
        return s === T ? T : lookup(s, env, c);
      }
      case "EVAL": // data that is a form runs in this interpreter, under its limits: no JavaScript eval
        return ev(await ev(rest[0] ?? NIL, env, c, depth + 1), env, c, depth + 1);
      case "DEFUN": {
        const name = rest[0];
        if (!(name instanceof Sym)) throw new LispError("defun: 함수 이름이 필요합니다");
        if (BUILTINS.has(name.name) || SPECIAL.has(name.name)) throw new LispError(`defun: 내장 함수 ${name.name}는 다시 정의할 수 없습니다`);
        c.globals.set(name, lambda(name.name, rest[1], rest.slice(2)));
        return name;
      }
    }
  }
  const f = await ev(head, env, c, depth + 1);
  const args: Val[] = [];
  for (const a of rest) args.push(await ev(a, env, c, depth + 1));
  return call(f, args, env, c, depth);
}
const SPECIAL = new Set(["QUOTE", "SETQ", "IF", "COND", "PROGN", "AND", "OR", "WHILE", "REPEAT", "FOREACH", "LAMBDA", "DEFUN", "VLAX-FOR", "FUNCTION", "SET", "BOUNDP", "VL-SYMBOL-VALUE", "EVAL"]);

function lambda(name: string, spec: Val, body: Val[]): Lambda {
  const s = list(spec ?? NIL, name);
  const slash = s.findIndex((v) => v instanceof Sym && v.name === "/");
  const params = (slash < 0 ? s : s.slice(0, slash)) as Val[];
  const locals = slash < 0 ? [] : s.slice(slash + 1);
  for (const p of [...params, ...locals]) if (!(p instanceof Sym)) throw new LispError(`${name}: 인수 이름이 필요합니다 (${show(p)})`);
  return { kind: "lambda", name, params: params as Sym[], locals: locals as Sym[], body };
}

// --- builtins -----------------------------------------------------------------------------

const BUILTINS = new Map<string, Builtin>();
const def = (names: string, fn: Builtin["fn"]) => names.split(" ").forEach((n) => BUILTINS.set(n, { kind: "builtin", name: n, fn }));

const fold = (name: string, f: (a: number, b: number) => number, unit?: number) => (args: Val[]) => {
  const ns = args.map((a) => num(a, name));
  if (!ns.length) {
    if (unit === undefined) throw new LispError(`${name}: 인수가 필요합니다`);
    return unit;
  }
  return out(ns.slice(1).reduce(f, ns[0]));
};
def("+", fold("+", (a, b) => a + b, 0));
def("*", fold("*", (a, b) => a * b, 1));
def("-", (args) => (args.length === 1 ? -num(args[0], "-") : fold("-", (a, b) => a - b)(args)));
def("/", fold("/", (a, b) => a / b));
def("REM", fold("rem", (a, b) => a % b));
def("1+", (a) => num(a[0], "1+") + 1);
def("1-", (a) => num(a[0], "1-") - 1);
def("ABS", (a) => Math.abs(num(a[0], "abs")));
def("MIN", fold("min", Math.min));
def("MAX", fold("max", Math.max));
def("SQRT", (a) => out(Math.sqrt(num(a[0], "sqrt"))));
def("SIN", (a) => Math.sin(num(a[0], "sin")));
def("COS", (a) => Math.cos(num(a[0], "cos")));
def("ATAN", (a) => (a.length > 1 ? Math.atan2(num(a[0], "atan"), num(a[1], "atan")) : Math.atan(num(a[0], "atan"))));
def("EXPT", (a) => out(num(a[0], "expt") ** num(a[1], "expt")));
def("EXP", (a) => out(Math.exp(num(a[0], "exp"))));
def("LOG", (a) => out(Math.log(num(a[0], "log"))));
def("FIX", (a) => Math.trunc(num(a[0], "fix")));
def("FLOAT", (a) => num(a[0], "float"));
const cmp = (name: string, f: (a: number, b: number) => boolean) => (args: Val[]) => {
  for (let i = 1; i < args.length; i++) if (!f(num(args[i - 1], name), num(args[i], name))) return NIL;
  return T;
};
def("<", cmp("<", (a, b) => a < b));
def(">", cmp(">", (a, b) => a > b));
def("<=", cmp("<=", (a, b) => a <= b));
def(">=", cmp(">=", (a, b) => a >= b));
const same = (a: Val, b: Val): boolean =>
  typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-12 : isList(a) && isList(b) ? a.length === b.length && a.every((x, i) => same(x, b[i])) : isPair(a) && isPair(b) ? same(a.car, b.car) && same(a.cdr, b.cdr) : a === b;
def("=", (a) => bool(a.slice(1).every((x) => same(x, a[0]))));
def("/=", (a) => bool(!same(a[0], a[1])));
def("EQ EQUAL", (a) => bool(same(a[0], a[1])));
def("NOT NULL", (a) => bool(!truthy(a[0])));
def("ZEROP", (a) => bool(num(a[0], "zerop") === 0));
def("MINUSP", (a) => bool(num(a[0], "minusp") < 0));
def("NUMBERP", (a) => bool(typeof a[0] === "number"));
def("LISTP", (a) => bool(isList(a[0]) || isPair(a[0])));
def("ATOM", (a) => bool(!(isList(a[0]) && a[0].length) && !isPair(a[0])));

def("LIST", (a) => cap([...a]));
def("CONS", (a) => {
  if (!isList(a[1])) return { kind: "pair", car: a[0], cdr: a[1] };
  if (a[1].length >= LIMITS.list) cap(a[1].concat([NIL])); // refuse before copying a long list
  return [a[0], ...a[1]];
});
const car = (v: Val): Val => (isPair(v) ? v.car : list(v, "car")[0] ?? NIL);
const cdr = (v: Val): Val => (isPair(v) ? v.cdr : list(v, "cdr").slice(1));
def("CAR", (a) => car(a[0]));
def("CDR", (a) => cdr(a[0]));
def("CADR", (a) => car(cdr(a[0])));
def("CDDR", (a) => cdr(cdr(a[0])));
def("CADDR", (a) => car(cdr(cdr(a[0]))));
def("CAAR", (a) => car(car(a[0])));
def("CDAR", (a) => cdr(car(a[0])));
def("NTH", (a) => list(a[1], "nth")[num(a[0], "nth")] ?? NIL);
def("LENGTH", (a) => list(a[0], "length").length);
def("REVERSE", (a) => [...list(a[0], "reverse")].reverse());
def("APPEND", (a) => cap(a.flatMap((x) => list(x, "append"))));
def("LAST", (a) => list(a[0], "last").at(-1) ?? NIL);
def("MEMBER", (a) => {
  const l = list(a[1], "member");
  const i = l.findIndex((x) => same(x, a[0]));
  return i < 0 ? NIL : l.slice(i);
});
def("ASSOC", (a) => list(a[1], "assoc").find((x) => same(car(x), a[0])) ?? NIL);
def("SUBST", (a) => list(a[2], "subst").map((x) => (same(x, a[1]) ? a[0] : x)));
def("MAPCAR", async (a, c) => {
  const ls = a.slice(1).map((l) => list(l, "mapcar"));
  const n = Math.min(...ls.map((l) => l.length));
  const r: Val[] = [];
  for (let i = 0; i < n; i++) r.push(await call(a[0], ls.map((l) => l[i]), { vars: new Map(), parent: null }, c, c.depth + 1));
  return cap(r);
});
def("APPLY", (a, c) => call(a[0], list(a[1], "apply"), { vars: new Map(), parent: null }, c, c.depth + 1));

def("STRCAT", (a) => capStr(a.map((x) => str(x, "strcat")).join("")));
def("STRLEN", (a) => str(a[0], "strlen").length);
def("SUBSTR", (a) => {
  const s = str(a[0], "substr");
  const start = num(a[1], "substr") - 1;
  return a.length > 2 ? s.substr(start, num(a[2], "substr")) : s.slice(start);
});
def("STRCASE", (a) => (truthy(a[1] ?? NIL) ? str(a[0], "strcase").toLowerCase() : str(a[0], "strcase").toUpperCase()));
def("RTOS", (a) => num(a[0], "rtos").toFixed(Math.min(8, Math.max(0, a.length > 2 ? num(a[2], "rtos") : 4))));
def("ITOA", (a) => String(Math.trunc(num(a[0], "itoa"))));
def("ATOI", (a) => Math.trunc(Number.parseFloat(str(a[0], "atoi"))) || 0);
def("ATOF", (a) => Number.parseFloat(str(a[0], "atof")) || 0);
def("ANGTOS", (a) => ((num(a[0], "angtos") * 180) / Math.PI).toFixed(a.length > 2 ? num(a[2], "angtos") : 0));

def("POLAR", (a) => {
  const [x, y] = pt(a[0], "polar");
  const ang = num(a[1], "polar");
  const d = num(a[2], "polar");
  return [x + d * Math.cos(ang), y + d * Math.sin(ang)];
});
def("DISTANCE", (a) => {
  const [p, q] = [pt(a[0], "distance"), pt(a[1], "distance")];
  return Math.hypot(q[0] - p[0], q[1] - p[1]);
});
def("ANGLE", (a) => {
  const [p, q] = [pt(a[0], "angle"), pt(a[1], "angle")];
  const r = Math.atan2(q[1] - p[1], q[0] - p[0]);
  return r < 0 ? r + 2 * Math.PI : r;
});
def("INTERS", (a) => {
  const [p1, p2, p3, p4] = a.slice(0, 4).map((v) => pt(v, "inters"));
  const d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0]);
  if (Math.abs(d) < 1e-12) return NIL;
  const t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d;
  const u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d;
  const onSeg = a.length < 5 || truthy(a[4]);
  if (onSeg && (t < 0 || t > 1 || u < 0 || u > 1)) return NIL;
  return [p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])];
});

def("PRINC PRINT PRIN1 PROMPT ALERT TERPRI", (a, c) => {
  const f = a[1];
  if (a.length && f && typeof f === "object" && !isList(f) && !(f instanceof Sym) && f.kind === "file") {
    writeTo(f, typeof a[0] === "string" ? a[0] : show(a[0]));
    return a[0];
  }
  if (a.length) log(c, typeof a[0] === "string" ? a[0] : show(a[0]));
  return a[0] ?? NIL;
});
def("COMMAND", (a, c) => command(a, c));
def("SETVAR", (a, c) => {
  const name = str(a[0], "setvar").toUpperCase();
  if (name === "CLAYER") {
    const l = str(a[1], "setvar");
    if (c.host.layers && !c.host.layers.some((x) => x.toUpperCase() === l.toUpperCase())) throw new LispError(`setvar: 도면에 없는 레이어 ${l}`);
    if (c.host.model) {
      if (!c.host.model.layer(l)) throw new LispError(`setvar: 도면에 없는 레이어 ${l}`);
      c.host.model.current = c.host.model.layer(l)!.name;
    }
    c.layer = c.host.model?.current ?? c.host.layers?.find((x) => x.toUpperCase() === l.toUpperCase()) ?? l;
  } // other system variables (OSMODE, CMDECHO, ...) have no meaning here: accepted, ignored
  return a[1] ?? NIL;
});
def("GETVAR", (a, c) => (str(a[0], "getvar").toUpperCase() === "CLAYER" ? (c.layer ?? c.host.layer ?? "0") : NIL));

const log = (c: Run, line: string) => {
  if (c.log.length < 1000) c.log.push(line.slice(0, 2000)); // the rest is dropped: output, not state
};

/** The time the user takes to answer does not count against the run's time limit. */
async function waiting<X>(c: Run, f: () => Promise<X>): Promise<X> {
  const t0 = Date.now();
  try {
    return await f();
  } finally {
    c.deadline += Date.now() - t0;
  }
}

const input = (kind: InputKind) => async (a: Val[], c: Run) => {
  if (!c.host.input) throw new LispError("이 화면에서는 입력 함수(get…)를 쓸 수 없습니다");
  const base = a.length && isList(a[0]) ? pt(a[0], "get") : undefined;
  const prompt = a.find((x) => typeof x === "string") as string | undefined;
  const v = await waiting(c, () => c.host.input!(kind, prompt ?? "", base));
  if (v === null) return NIL;
  return Array.isArray(v) ? [v[0], v[1]] : v;
};
def("GETPOINT GETCORNER", input("point"));
def("GETDIST", input("dist"));
def("GETREAL", input("real"));
def("GETINT", input("int"));
def("GETSTRING", input("string"));
def("GETANGLE GETORIENT", input("angle"));
def("ENTSEL", async (a, c) => {
  if (!c.host.entsel) throw new LispError("이 화면에서는 객체 선택(entsel)을 쓸 수 없습니다");
  const r = await waiting(c, () => c.host.entsel!(typeof a[0] === "string" ? a[0] : "객체 선택:"));
  return r ? [{ kind: "ename", sel: r.sel }, [r.at[0], r.at[1]]] : NIL;
});
def("SSGET", async (a, c) => {
  const mode = typeof a[0] === "string" ? a[0].toUpperCase() : null;
  // a filter list ((0 . "LINE") (8 . "0")) comes last: (ssget "X" filter) or (ssget filter)
  const last = a.at(-1);
  const filter = last !== undefined && isList(last) && last.length && last.every(isGroup) ? groups(last, "ssget") : null;
  let items: Sel[];
  if (mode === "X" && c.host.model) items = c.host.model.all().map(toSel);
  else {
    if (!c.host.ssget) throw new LispError("이 화면에서는 선택 집합(ssget)을 쓸 수 없습니다");
    items = await waiting(c, () => c.host.ssget!(mode));
  }
  if (filter) {
    const m = c.host.model;
    items = items.filter((s) => matches(m?.get(s.handle) ?? { handle: s.handle, layer: s.layer, geom: s.geom }, filter));
  }
  return items.length ? { kind: "ss", items } : NIL;
});
const ss = (v: Val, fn: string): SelSet => {
  if (typeof v !== "object" || isList(v) || v instanceof Sym || v.kind !== "ss") throw new LispError(`${fn}: 선택 집합이 필요합니다`);
  return v;
};
def("SSLENGTH", (a) => ss(a[0], "sslength").items.length);
def("SSNAME", (a) => {
  const s = ss(a[0], "ssname").items[num(a[1], "ssname")];
  return s ? { kind: "ename", sel: s } : NIL;
});
def("ENTGET", (a, c) => {
  const e = a[0];
  if (typeof e !== "object" || isList(e) || e instanceof Sym || e.kind !== "ename") throw new LispError("entget: 객체 이름(entsel/ssname)이 필요합니다");
  const m = c.host.model;
  if (!m) return entget(e.sel);
  const cur = m.get(e.sel.handle);
  return cur ? [pair(-1, e), ...toGroups(cur).map(([code, v]) => grp(code, v))] : NIL; // deleted: nil
});

const pair = (code: number, v: Val): Pair => ({ kind: "pair", car: code, cdr: v });
const p3 = (code: number, p: Pt): Val[] => [code, p[0], p[1], 0];
/** The DXF group codes AutoLISP routines read most: type, layer, handle and the main geometry. */
function entget(s: Sel): Val[] {
  const g = s.geom as Record<string, unknown> & { type: string };
  const out: Val[] = [pair(0, g.type), pair(5, s.handle), pair(8, s.layer)];
  const P = (k: string) => g[k] as Pt | undefined;
  if (P("start")) out.push(p3(10, P("start")!));
  if (P("end")) out.push(p3(11, P("end")!));
  if (P("center")) out.push(p3(10, P("center")!));
  if (P("insert")) out.push(p3(10, P("insert")!));
  if (typeof g.radius === "number") out.push(pair(40, g.radius));
  if (typeof g.height === "number") out.push(pair(40, g.height));
  if (typeof g.value === "string") out.push(pair(1, g.value));
  if (typeof g.name === "string") out.push(pair(2, g.name));
  if (typeof g.start_angle === "number") out.push(pair(50, ((g.start_angle as number) * Math.PI) / 180));
  if (typeof g.end_angle === "number") out.push(pair(51, ((g.end_angle as number) * Math.PI) / 180));
  if (Array.isArray(g.points)) for (const p of g.points as number[][]) out.push(p3(10, [p[0], p[1]]));
  if (typeof g.closed === "boolean") out.push(pair(70, g.closed ? 1 : 0));
  return out;
}

// --- run ----------------------------------------------------------------------------------

/** A program run: its command segments (applied by the screen afterwards), printed lines, the
 * c: commands it defined and the last value. `call` runs one of those commands after loading. */
export async function runLisp(src: string, host: Host = {}, callCommand?: string, session: Session = newSession()): Promise<Outcome> {
  return guard(() => runForms(src, host, callCommand, session));
}

async function guard<X>(f: () => Promise<X>): Promise<X> {
  try {
    return await f();
  } catch (e) {
    // a list nested 100k deep overflows JavaScript's stack while printing or comparing it
    if (e instanceof RangeError) throw new LispError("목록이 너무 깊게 중첩되었습니다");
    if (e instanceof ModelError) throw new LispError(e.message);
    throw e;
  }
}

const newRun = (host: Host, session: Session): Run => ({
  host,
  steps: 0,
  deadline: Date.now() + LIMITS.ms,
  segs: [],
  inputs: 0,
  log: [],
  layer: host.model?.current ?? host.layer,
  globals: session.globals,
  session,
  dcl: new Map(),
  dlg: null,
  files: 0,
  depth: 0,
  kw: null,
  loading: 0,
});

const finish = (c: Run, value: Val, commands: string[] = []): Outcome => ({
  segments: c.segs.filter((s) => s.inputs.length),
  log: c.log,
  commands,
  value,
  changes: c.host.model ? c.host.model.changes() : null,
});

async function runForms(src: string, host: Host, callCommand: string | undefined, session: Session): Promise<Outcome> {
  const forms = read(src);
  const c = newRun(host, session);
  const env: Env = { vars: new Map(), parent: null };
  let value: Val = NIL;
  // calling one command loads the file's definitions only, not the drawing its top level does
  const defining = (f: Val) => isList(f) && f[0] instanceof Sym && (f[0].name === "DEFUN" || f[0].name === "SETQ");
  let commands: string[] = [];
  try {
    for (const f of forms) if (!callCommand || defining(f)) value = await ev(f, env, c, 0);
    commands = [...c.globals.entries()].filter(([s, v]) => s.name.startsWith("C:") && typeof v === "object" && !isList(v) && !(v instanceof Sym) && v.kind === "lambda").map(([s]) => s.name.slice(2));
    if (callCommand) {
      const f = c.globals.get(Sym.of(`C:${callCommand}`));
      if (!f) throw new LispError(`정의되지 않은 명령: ${callCommand}`);
      value = await call(f, [], env, c, 0);
    }
  } catch (e) {
    // A5: AutoLISP calls the routine's *error* function with the message; the run still ends in
    // that error (nothing is applied), so the handler can only tidy variables, as the screen keeps no system variables
    const h = c.globals.get(Sym.of("*ERROR*"));
    if (e instanceof LispError && !(e instanceof LimitError) && typeof h === "object" && !isList(h) && !(h instanceof Sym) && h.kind === "lambda") {
      try {
        await call(h, [e.message], env, c, 0);
      } catch {
        /* an error in the handler is not reported over the first one */
      }
    }
    throw e;
  }
  return finish(c, value, commands);
}

// --- 2D automation A1: objects, vla-, files, DCL, reactors (plan.md §20) ---------------------

const isGroup = (x: Val): boolean => (isPair(x) && typeof x.car === "number") || (isList(x) && x.length > 1 && typeof x[0] === "number");
/** AutoLISP group (code . value) or (10 x y z) -> [code, value] for the object model. */
function groups(l: Val[], fn: string): Group[] {
  return l.map((x) => {
    if (isPair(x) && typeof x.car === "number") return [x.car, fromVal(x.cdr)] as Group;
    if (isList(x) && typeof x[0] === "number") return [x[0], x.length === 2 && !isList(x[1]) ? fromVal(x[1]) : x.slice(1).map((v) => fromVal(v))] as Group;
    throw new LispError(`${fn}: 그룹 코드 목록이 필요합니다 (${show(x)})`);
  });
}
/** [code, value] -> (code . value), or (code x y z) for a point. */
const grp = (code: number, v: unknown): Val => (Array.isArray(v) && v.every((x) => typeof x === "number") ? [code, ...(v as number[])] : pair(code, toVal(v)));

function fromVal(v: Val): unknown {
  if (typeof v === "number" || typeof v === "string") return v;
  if (isList(v)) return v.map(fromVal);
  if (v instanceof Sym) return v.name;
  if (v.kind === "ename") return v.sel.handle;
  if (v.kind === "vla" && v.obj.t === "ent") return v.obj.handle;
  throw new LispError(`그룹 값으로 쓸 수 없습니다: ${show(v)}`);
}
function toVal(v: unknown): Val {
  if (typeof v === "number" || typeof v === "string") return v;
  if (typeof v === "boolean") return v ? T : NIL;
  if (Array.isArray(v)) return v.map(toVal);
  return NIL;
}
const toSel = (e: Ent): Sel => ({ handle: e.handle, layer: e.layer, geom: e.geom });
const enameOf = (e: Ent): Ename => ({ kind: "ename", sel: toSel(e) });
const model = (c: Run, fn: string): DrawingModel => {
  if (!c.host.model) throw new LispError(`이 화면에서는 ${fn}를 쓸 수 없습니다 (2D 작도 화면에서 실행하세요)`);
  return c.host.model;
};
const ename = (v: Val, fn: string): Ename => {
  if (typeof v !== "object" || isList(v) || v instanceof Sym || v.kind !== "ename") throw new LispError(`${fn}: 객체 이름(ename)이 필요합니다 (${show(v)})`);
  return v;
};
const quiet = (c: Run, fn: string, f: () => Val): Val => {
  try {
    return f();
  } catch (e) {
    if (!(e instanceof ModelError)) throw e;
    log(c, `${fn}: ${e.message}`); // AutoLISP returns nil here; the reason goes to the log
    return NIL;
  }
};

// entity list functions
const entmake = (a: Val[], c: Run, fn: string): Ent | null => {
  let made: Ent | null = null;
  quiet(c, fn, () => {
    const g = groups(list(a[0], fn), fn);
    const r = fromGroups(g);
    made = model(c, fn).add(r.geom, r.layer ?? c.layer ?? model(c, fn).current, { aci: r.aci, linetype: r.linetype, ...r.props });
    return T;
  });
  return made;
};
def("ENTMAKE", (a, c) => (entmake(a, c, "entmake") ? a[0] : NIL));
def("ENTMAKEX", (a, c) => {
  const e = entmake(a, c, "entmakex");
  return e ? enameOf(e) : NIL;
});
def("ENTMOD", (a, c) =>
  quiet(c, "entmod", () => {
    const l = list(a[0], "entmod");
    const who = l.find((x) => isPair(x) && x.car === -1) as Pair | undefined;
    if (!who) throw new ModelError("(-1 . 객체 이름)이 필요합니다");
    const m = model(c, "entmod");
    const old = m.get(ename(who.cdr, "entmod").sel.handle);
    if (!old) throw new ModelError("지워졌거나 없는 객체입니다");
    const r = fromGroups(groups(l.filter((x) => x !== who), "entmod"), old);
    m.set(old.handle, { geom: r.geom, ...(r.layer ? { layer: r.layer } : {}), ...(r.aci !== undefined ? { aci: r.aci } : {}), ...(r.linetype ? { linetype: r.linetype } : {}), ...r.props });
    return a[0];
  }),
);
def("ENTDEL", (a, c) => quiet(c, "entdel", () => (model(c, "entdel").remove(ename(a[0], "entdel").sel.handle), a[0])));
def("ENTUPD", (a) => a[0] ?? NIL);
def("ENTLAST", (_a, c) => {
  const e = model(c, "entlast").last();
  return e ? enameOf(e) : NIL;
});
def("ENTNEXT", (a, c) => {
  const all = model(c, "entnext").all();
  if (!a.length || !truthy(a[0])) return all[0] ? enameOf(all[0]) : NIL;
  const i = all.findIndex((e) => e.handle === ename(a[0], "entnext").sel.handle);
  return i >= 0 && all[i + 1] ? enameOf(all[i + 1]) : NIL;
});
def("HANDENT", (a, c) => {
  const e = model(c, "handent").get(str(a[0], "handent"));
  return e ? enameOf(e) : NIL;
});
def("SSADD", (a) => {
  if (!a.length) return { kind: "ss", items: [] };
  const e = ename(a[0], "ssadd");
  const base = a[1] !== undefined ? ss(a[1], "ssadd") : { kind: "ss" as const, items: [] as Sel[] };
  if (!base.items.some((s) => s.handle === e.sel.handle)) base.items.push(e.sel);
  return base;
});
def("SSDEL", (a) => {
  const s = ss(a[1], "ssdel");
  const h = ename(a[0], "ssdel").sel.handle;
  const i = s.items.findIndex((x) => x.handle === h);
  if (i < 0) return NIL;
  s.items.splice(i, 1);
  return s;
});
def("SSMEMB", (a) => (ss(a[1], "ssmemb").items.some((x) => x.handle === ename(a[0], "ssmemb").sel.handle) ? a[0] : NIL));
def("SSSETFIRST", () => NIL);
// W7b (plan §22.8): LAYER, LTYPE, STYLE, DIMSTYLE and APPID records as DXF group lists
const TBL_KIND: Record<string, TableKind | "layer"> = { LAYER: "layer", LTYPE: "linetype", STYLE: "textstyle", DIMSTYLE: "dimstyle", APPID: "regapp" };
const tblRecords = (m: DrawingModel, table: string): Val[] | null => {
  const kind = TBL_KIND[table.toUpperCase()];
  if (!kind) return null; // BLOCK, VIEW, UCS, VPORT: not kept as tables here
  if (kind === "layer")
    return m.layers.map((l) => {
      const flags = (l.frozen ? 1 : 0) | (l.locked ? 4 : 0);
      const out: Val[] = [pair(0, "LAYER"), pair(2, l.name), pair(70, flags), pair(62, (l.off ? -1 : 1) * (l.color ?? 7)), pair(6, l.linetype ?? "Continuous")];
      if (l.lwt !== undefined) out.push(pair(370, l.lwt));
      if (l.plot === false) out.push(pair(290, 0));
      return out;
    });
  return m.table(kind).map((r) => {
    if (kind === "linetype") {
      const p = r.pattern ?? [];
      return [pair(0, "LTYPE"), pair(2, r.name), pair(70, 0), pair(3, r.description ?? ""), pair(72, 65), pair(73, p.length), pair(40, p.reduce((s, v) => s + Math.abs(v), 0)), ...p.map((v) => pair(49, v))];
    }
    if (kind === "textstyle") return [pair(0, "STYLE"), pair(2, r.name), pair(70, 0), pair(40, r.height ?? 0), pair(41, r.width ?? 1), pair(50, r.oblique ?? 0), pair(3, r.font ?? "")];
    if (kind === "dimstyle") {
      const d = r.dim ?? {};
      const out: Val[] = [pair(0, "DIMSTYLE"), pair(2, r.name), pair(70, 0)];
      for (const [k, code] of [["dimscale", 40], ["dimasz", 41], ["dimexo", 42], ["dimexe", 44], ["dimtxt", 140], ["dimgap", 147], ["dimdec", 271]] as const) if (d[k] !== undefined) out.push(pair(code, d[k] as number));
      return out;
    }
    return [pair(0, "APPID"), pair(2, r.name), pair(70, 0)];
  });
};
def("TBLSEARCH", (a, c) => {
  const recs = tblRecords(model(c, "tblsearch"), str(a[0], "tblsearch"));
  const want = str(a[1], "tblsearch").toUpperCase();
  // each record is ((0 . kind) (2 . name) ...): the name is the cdr of the second pair
  return recs?.find((r) => isList(r) && String((r[1] as Pair).cdr).toUpperCase() === want) ?? NIL;
});
const tblPos = new WeakMap<DrawingModel, Map<string, number>>();
def("TBLNEXT", (a, c) => {
  const m = model(c, "tblnext");
  const table = str(a[0], "tblnext").toUpperCase();
  const recs = tblRecords(m, table);
  if (!recs) return NIL;
  const pos = tblPos.get(m) ?? new Map<string, number>();
  tblPos.set(m, pos);
  const i = a[1] !== undefined && truthy(a[1]) ? 0 : (pos.get(table) ?? 0);
  pos.set(table, i + 1);
  return recs[i] ?? NIL;
});
// regapp: the name when this call registered it, nil when it was already registered
def("REGAPP", (a, c) => {
  const m = model(c, "regapp");
  const n = str(a[0], "regapp");
  if (m.tableFind("regapp", n)) return NIL;
  try {
    m.tableAdd("regapp", n);
  } catch (e) {
    if (e instanceof ModelError) return NIL;
    throw e;
  }
  return n;
});
def("LAYOUTLIST", (_a, c) => model(c, "layoutlist").tables.layouts.slice() as Val);

// vla- / vlax- (ActiveX-style objects; variants and safearrays are plain lists here)
const vla = (v: Val, fn: string): Obj => {
  if (typeof v === "object" && !isList(v) && !(v instanceof Sym)) {
    if (v.kind === "vla") return v.obj;
    if (v.kind === "ename") return { t: "ent", handle: v.sel.handle };
  }
  throw new LispError(`${fn}: VLA 객체가 필요합니다 (${show(v)})`);
};
const TRUE = Sym.of(":VLAX-TRUE");
const FALSE = Sym.of(":VLAX-FALSE");
function toO(v: Val): OVal {
  if (typeof v === "number" || typeof v === "string") return v;
  if (v === T || v === TRUE) return true;
  if (v === FALSE) return false;
  if (isList(v)) return v.length ? v.map(toO) : false;
  if (v instanceof Sym) return v.name;
  if (v.kind === "vla") return v.obj;
  if (v.kind === "ename") return { t: "ent", handle: v.sel.handle };
  throw new LispError(`객체 함수에 쓸 수 없는 값: ${show(v)}`);
}
function fromO(v: OVal): Val {
  if (v === null) return NIL;
  if (typeof v === "boolean") return v ? TRUE : FALSE;
  if (typeof v === "number" || typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(fromO);
  if (isModelObj(v)) return { kind: "vla", obj: v };
  return NIL;
}
const propName = (v: Val, fn: string) => (v instanceof Sym ? v.name : str(v, fn));
const vget = (c: Run, o: Val, p: string, fn: string) => fromO(getProp(model(c, fn), vla(o, fn), p));
const vput = (c: Run, o: Val, p: string, v: Val, fn: string) => (putProp(model(c, fn), vla(o, fn), p, toO(v)), NIL);
const vcall = (c: Run, o: Val, m: string, args: Val[], fn: string) => fromO(invoke(model(c, fn), vla(o, fn), m, args.map(toO)));

/** vla-get-Xxx, vla-put-Xxx and vla-Method: any name, checked against the object at run time. */
const vlaCache = new Map<string, Builtin>();
function vlaFunction(name: string): Builtin | undefined {
  if (!name.startsWith("VLA-")) return undefined;
  let b = vlaCache.get(name);
  if (!b) {
    const rest = name.slice(4);
    const fn = name.toLowerCase();
    b = {
      kind: "builtin",
      name,
      fn: rest.startsWith("GET-") ? (a, c) => vget(c, a[0], rest.slice(4), fn) : rest.startsWith("PUT-") ? (a, c) => vput(c, a[0], rest.slice(4), a[1], fn) : (a, c) => vcall(c, a[0], rest, a.slice(1), fn),
    };
    vlaCache.set(name, b);
  }
  return b;
}
def("VL-LOAD-COM VL-LOAD-REACTORS", () => T);
def("VLAX-GET-ACAD-OBJECT", () => ({ kind: "vla", obj: { t: "app" } }));
def("VLAX-GET VLAX-GET-PROPERTY", (a, c) => vget(c, a[0], propName(a[1], "vlax-get"), "vlax-get"));
def("VLAX-PUT VLAX-PUT-PROPERTY", (a, c) => vput(c, a[0], propName(a[1], "vlax-put"), a[2], "vlax-put"));
def("VLAX-INVOKE VLAX-INVOKE-METHOD", (a, c) => vcall(c, a[0], propName(a[1], "vlax-invoke"), a.slice(2), "vlax-invoke"));
def("VLAX-PROPERTY-AVAILABLE-P", (a, c) => {
  try {
    getProp(model(c, "vlax-property-available-p"), vla(a[0], "vlax-property-available-p"), propName(a[1], "vlax-property-available-p"));
    return T;
  } catch {
    return NIL;
  }
});
def("VLAX-ENAME->VLA-OBJECT", (a) => ({ kind: "vla", obj: { t: "ent", handle: ename(a[0], "vlax-ename->vla-object").sel.handle } }));
def("VLAX-VLA-OBJECT->ENAME", (a, c) => {
  const o = vla(a[0], "vlax-vla-object->ename");
  const e = o.t === "ent" ? model(c, "vlax-vla-object->ename").get(o.handle) : undefined;
  return e ? enameOf(e) : NIL;
});
def("VLAX-ERASED-P", (a, c) => {
  const o = vla(a[0], "vlax-erased-p");
  return bool(o.t === "ent" && !model(c, "vlax-erased-p").get(o.handle));
});
def("VLAX-RELEASE-OBJECT VLAX-OBJECT-RELEASED-P", () => NIL);
def("VLAX-3D-POINT", (a) => (isList(a[0]) ? [...pt(a[0], "vlax-3d-point"), 0] : [num(a[0], "vlax-3d-point"), num(a[1], "vlax-3d-point"), a[2] !== undefined ? num(a[2], "vlax-3d-point") : 0]));
def("VLAX-MAKE-SAFEARRAY", (a) => {
  const bounds = a.slice(1).map((b) => (isPair(b) ? num(b.cdr, "vlax-make-safearray") - num(b.car, "vlax-make-safearray") + 1 : 0));
  return cap(Array.from({ length: Math.max(0, bounds[0] ?? 0) }, () => 0));
});
def("VLAX-SAFEARRAY-FILL", (a) => [...list(a[1], "vlax-safearray-fill")]);
def("VLAX-SAFEARRAY->LIST VLAX-MAKE-VARIANT VLAX-VARIANT-VALUE", (a) => a[0] ?? NIL);
def("VLAX-VARIANT-TYPE", (a) => (isList(a[0]) ? 8197 : typeof a[0] === "string" ? 8 : typeof a[0] === "number" ? 5 : 9));
const curve = (c: Run, v: Val, fn: string): Ent => {
  const o = vla(v, fn);
  const e = o.t === "ent" ? model(c, fn).get(o.handle) : undefined;
  if (!e) throw new LispError(`${fn}: 곡선 객체가 필요합니다`);
  return e;
};
/** Curve parameters as AutoCAD uses them: line = distance, circle/arc = angle, polyline = vertex. */
function curveInfo(e: Ent): { start: number; end: number; dist: (p: number) => number; at: (p: number) => Pt } | null {
  const g = e.geom;
  if (g.type === "LINE") {
    const len = Math.hypot(g.end[0] - g.start[0], g.end[1] - g.start[1]);
    return { start: 0, end: len, dist: (p) => p, at: (p) => [g.start[0] + ((g.end[0] - g.start[0]) * p) / (len || 1), g.start[1] + ((g.end[1] - g.start[1]) * p) / (len || 1)] };
  }
  if (g.type === "CIRCLE" || g.type === "ARC") {
    const a0 = g.type === "ARC" ? (g.start_angle * Math.PI) / 180 : 0;
    const sweep = g.type === "ARC" ? (((((g.end_angle - g.start_angle) % 360) + 360) % 360 || 360) * Math.PI) / 180 : 2 * Math.PI;
    return { start: a0, end: a0 + sweep, dist: (p) => g.radius * (p - a0), at: (p) => [g.center[0] + g.radius * Math.cos(p), g.center[1] + g.radius * Math.sin(p)] };
  }
  if (g.type === "LWPOLYLINE") {
    const pts = g.closed ? [...g.points, g.points[0]] : g.points;
    const lens = pts.slice(1).map((q, i) => Math.hypot(q[0] - pts[i][0], q[1] - pts[i][1])); // ponytail: chord length for bulged segments
    return {
      start: 0,
      end: lens.length,
      dist: (p) => lens.slice(0, Math.floor(p)).reduce((s, x) => s + x, 0) + (lens[Math.floor(p)] ?? 0) * (p - Math.floor(p)),
      at: (p) => {
        const i = Math.min(Math.floor(p), lens.length - 1);
        const t = p - i;
        return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t];
      },
    };
  }
  return null;
}
const info = (c: Run, v: Val, fn: string) => {
  const r = curveInfo(curve(c, v, fn));
  if (!r) throw new LispError(`${fn}: 선·원·호·폴리선만 됩니다`);
  return r;
};
def("VLAX-CURVE-GETSTARTPARAM", (a, c) => info(c, a[0], "vlax-curve-getStartParam").start);
def("VLAX-CURVE-GETENDPARAM", (a, c) => info(c, a[0], "vlax-curve-getEndParam").end);
def("VLAX-CURVE-GETDISTATPARAM", (a, c) => info(c, a[0], "vlax-curve-getDistAtParam").dist(num(a[1], "vlax-curve-getDistAtParam")));
def("VLAX-CURVE-GETPOINTATPARAM", (a, c) => [...info(c, a[0], "vlax-curve-getPointAtParam").at(num(a[1], "vlax-curve-getPointAtParam")), 0]);
def("VLAX-CURVE-GETSTARTPOINT", (a, c) => {
  const i = info(c, a[0], "vlax-curve-getStartPoint");
  return [...i.at(i.start), 0];
});
def("VLAX-CURVE-GETENDPOINT", (a, c) => {
  const i = info(c, a[0], "vlax-curve-getEndPoint");
  return [...i.at(i.end), 0];
});
def("VLAX-CURVE-GETAREA", (a, c) => {
  const v = getProp(model(c, "vlax-curve-getArea"), vla(a[0], "vlax-curve-getArea"), "Area");
  return typeof v === "number" ? v : 0;
});
def("VLAX-CURVE-ISCLOSED", (a, c) => {
  const g = curve(c, a[0], "vlax-curve-isClosed").geom;
  return bool(g.type === "CIRCLE" || (g.type === "LWPOLYLINE" && g.closed));
});

// vl- utilities
def("VL-STRING-SEARCH", (a) => {
  const i = str(a[1], "vl-string-search").indexOf(str(a[0], "vl-string-search"), a[2] !== undefined ? num(a[2], "vl-string-search") : 0);
  return i < 0 ? NIL : i;
});
def("VL-STRING-POSITION", (a) => {
  const i = str(a[1], "vl-string-position").indexOf(String.fromCharCode(num(a[0], "vl-string-position")), a[2] !== undefined && truthy(a[2]) ? num(a[2], "vl-string-position") : 0);
  return i < 0 ? NIL : i;
});
def("VL-STRING-SUBST", (a) => capStr(str(a[2], "vl-string-subst").replace(str(a[1], "vl-string-subst"), () => str(a[0], "vl-string-subst"))));
const trimSet = (chars: string) => new RegExp(`[${chars.replace(/[\]\\^-]/g, "\\$&")}]`);
const trim = (side: "both" | "left" | "right") => (a: Val[]) => {
  const re = trimSet(str(a[0], "vl-string-trim"));
  let t = str(a[1], "vl-string-trim");
  if (side !== "right") while (t && re.test(t[0])) t = t.slice(1);
  if (side !== "left") while (t && re.test(t[t.length - 1])) t = t.slice(0, -1);
  return t;
};
def("VL-STRING-TRIM", trim("both"));
def("VL-STRING-LEFT-TRIM", trim("left"));
def("VL-STRING-RIGHT-TRIM", trim("right"));
def("VL-STRING->LIST", (a) => cap([...str(a[0], "vl-string->list")].map((ch) => ch.codePointAt(0)!)));
def("VL-LIST->STRING", (a) => capStr(list(a[0], "vl-list->string").map((x) => String.fromCodePoint(Math.min(0x10ffff, Math.max(0, num(x, "vl-list->string"))))).join("")));
def("CHR", (a) => String.fromCodePoint(Math.min(0x10ffff, Math.max(0, Math.trunc(num(a[0], "chr"))))));
def("ASCII", (a) => str(a[0], "ascii").codePointAt(0) ?? 0);
def("VL-PRINC-TO-STRING", (a) => (typeof a[0] === "string" ? a[0] : show(a[0])));
def("VL-PRIN1-TO-STRING", (a) => show(a[0]));
def("VL-SYMBOL-NAME", (a) => (a[0] instanceof Sym ? a[0].name : str(a[0], "vl-symbol-name")));
def("VL-CONSP", (a) => bool((isList(a[0]) && a[0].length > 0) || isPair(a[0])));
def("VL-POSITION", (a) => {
  const i = list(a[1], "vl-position").findIndex((x) => same(x, a[0]));
  return i < 0 ? NIL : i;
});
def("VL-REMOVE", (a) => list(a[1], "vl-remove").filter((x) => !same(x, a[0])));
const pred = (name: string, f: (xs: Val[], hits: boolean[]) => Val) => async (a: Val[], c: Run) => {
  const xs = list(a[1], name);
  const hits: boolean[] = [];
  for (const x of xs) hits.push(truthy(await call(a[0], [x], { vars: new Map(), parent: null }, c, c.depth + 1)));
  return f(xs, hits);
};
def("VL-REMOVE-IF", pred("vl-remove-if", (xs, h) => xs.filter((_, i) => !h[i])));
def("VL-REMOVE-IF-NOT", pred("vl-remove-if-not", (xs, h) => xs.filter((_, i) => h[i])));
def("VL-SOME", pred("vl-some", (_xs, h) => bool(h.some(Boolean))));
def("VL-EVERY", pred("vl-every", (_xs, h) => bool(h.every(Boolean))));
def("VL-MEMBER-IF", pred("vl-member-if", (xs, h) => (h.indexOf(true) < 0 ? NIL : xs.slice(h.indexOf(true)))));
def("VL-SORT", async (a, c) => {
  const less = async (x: Val, y: Val) => truthy(await call(a[1], [x, y], { vars: new Map(), parent: null }, c, c.depth + 1));
  // merge sort: n log n comparisons (each is a LISP call and counts as a step); stable like vl-sort
  const sort = async (xs: Val[]): Promise<Val[]> => {
    if (xs.length < 2) return xs;
    const [l, r] = [await sort(xs.slice(0, xs.length >> 1)), await sort(xs.slice(xs.length >> 1))];
    const out: Val[] = [];
    while (l.length && r.length) out.push((await less(r[0], l[0])) ? r.shift()! : l.shift()!);
    return [...out, ...l, ...r];
  };
  return sort([...list(a[0], "vl-sort")]);
});
def("VL-FILENAME-BASE", (a) => str(a[0], "vl-filename-base").replace(/^.*[\\/]/, "").replace(/\.[^.]*$/, ""));
def("VL-FILENAME-EXTENSION", (a) => {
  const m = /(\.[^.\\/]*)$/.exec(str(a[0], "vl-filename-extension"));
  return m ? m[1] : NIL;
});
def("TYPE", (a) => {
  const v = a[0];
  if (typeof v === "number") return Sym.of(Number.isInteger(v) ? "INT" : "REAL");
  if (typeof v === "string") return Sym.of("STR");
  if (v instanceof Sym) return Sym.of("SYM");
  if (isList(v)) return v.length ? Sym.of("LIST") : NIL;
  return Sym.of({ pair: "LIST", ss: "PICKSET", ename: "ENAME", builtin: "SUBR", lambda: "USUBR", vla: "VLA-OBJECT", file: "FILE", reactor: "VLR-Command-Reactor" }[v.kind]);
});
// error handling: limit errors (steps, time, depth) are never caught
type Caught = { kind: "pair"; car: Val; cdr: Val } & { caught: true };
def("VL-CATCH-ALL-APPLY", async (a, c) => {
  try {
    return await call(a[0], list(a[1] ?? NIL, "vl-catch-all-apply"), { vars: new Map(), parent: null }, c, c.depth + 1);
  } catch (e) {
    if (!(e instanceof LispError || e instanceof ModelError) || e instanceof LimitError) throw e;
    return { kind: "pair", car: Sym.of("VL-CATCH-ALL-APPLY-ERROR"), cdr: e.message, caught: true } as Caught;
  }
});
const caught = (v: Val) => isPair(v) && v.car === Sym.of("VL-CATCH-ALL-APPLY-ERROR");
def("VL-CATCH-ALL-ERROR-P", (a) => bool(caught(a[0])));
def("VL-CATCH-ALL-ERROR-MESSAGE", (a) => (caught(a[0]) ? (a[0] as Pair).cdr : NIL));

// files: the PC LISP library (GET/PUT /api/lisp) — no paths, no other folders
const FILE_LIMITS = { open: 8, bytes: 64 * 1024 };
const fileName = (v: Val, fn: string) => {
  const n = str(v, fn).replace(/^.*[\\/]/, ""); // "C:/x/data.txt" -> "data.txt": the library has no folders
  if (!/^[\w가-힣][\w가-힣 .-]{0,59}$/.test(n)) throw new LispError(`${fn}: 라이브러리 파일 이름으로 쓸 수 없습니다 (${n})`);
  return n;
};
const files = (c: Run, fn: string) => {
  if (!c.host.files) throw new LispError(`이 화면에서는 파일(${fn})을 쓸 수 없습니다`);
  return c.host.files;
};
const fdesc = (v: Val, fn: string): FileDesc => {
  if (typeof v !== "object" || isList(v) || v instanceof Sym || v.kind !== "file") throw new LispError(`${fn}: 파일(open의 결과)이 필요합니다`);
  if (!v.open) throw new LispError(`${fn}: 닫힌 파일입니다`);
  if (fn.startsWith("read") && v.mode !== "r") throw new LispError(`${fn}: 쓰기용 파일은 읽을 수 없습니다`);
  return v;
};
function writeTo(f: FileDesc, text: string) {
  if (!f.open || f.mode === "r") throw new LispError("읽기용 파일에는 쓸 수 없습니다");
  if (f.text.length + text.length > FILE_LIMITS.bytes) throw new LispError("파일은 64 KB까지입니다");
  f.text += text;
}
def("OPEN", async (a, c) => {
  const name = fileName(a[0], "open");
  const mode = str(a[1], "open").toLowerCase();
  if (mode !== "r" && mode !== "w" && mode !== "a") throw new LispError('open: 모드는 "r", "w", "a"입니다');
  if (c.files >= FILE_LIMITS.open) throw new LispError(`open: 한 번에 ${FILE_LIMITS.open}개 파일까지 엽니다`);
  const old = mode === "w" ? "" : await files(c, "open").read(name);
  if (mode === "r" && old === null) return NIL; // AutoLISP: nil when the file is not there
  c.files++;
  return { kind: "file", name, mode, text: old ?? "", pos: 0, open: true };
});
def("CLOSE", async (a, c) => {
  const f = fdesc(a[0], "close");
  f.open = false;
  c.files--;
  if (f.mode !== "r") {
    try {
      await waiting(c, () => files(c, "close").write(f.name, f.text));
    } catch (e) {
      throw new LispError(`close: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return NIL;
});
def("READ-LINE", (a) => {
  const f = fdesc(a[0], "read-line");
  if (f.pos >= f.text.length) return NIL;
  const end = f.text.indexOf("\n", f.pos);
  const line = f.text.slice(f.pos, end < 0 ? undefined : end).replace(/\r$/, "");
  f.pos = end < 0 ? f.text.length : end + 1;
  return line;
});
def("READ-CHAR", (a) => {
  const f = fdesc(a[0], "read-char");
  if (f.pos >= f.text.length) return NIL;
  return f.text.charCodeAt(f.pos++);
});
def("WRITE-LINE", (a) => {
  writeTo(fdesc(a[1], "write-line"), `${str(a[0], "write-line")}\n`);
  return a[0];
});
def("WRITE-CHAR", (a) => {
  writeTo(fdesc(a[1], "write-char"), String.fromCharCode(num(a[0], "write-char")));
  return a[0];
});
def("FINDFILE", async (a, c) => {
  const name = fileName(a[0], "findfile");
  return (await files(c, "findfile").read(name)) === null ? NIL : name;
});

// DCL dialogs: load_dialog reads a .dcl file of the library; the screen draws start_dialog
def("LOAD_DIALOG", async (a, c) => {
  const name = fileName(a[0], "load_dialog");
  const text = (await files(c, "load_dialog").read(name)) ?? (name.toLowerCase().endsWith(".dcl") ? null : await files(c, "load_dialog").read(`${name}.dcl`));
  if (text === null) return -1; // AutoLISP: a negative id when the file is not found
  try {
    c.dcl.set(c.dcl.size + 1, parseDcl(text));
  } catch (e) {
    throw new LispError(`load_dialog: ${(e as Error).message}`);
  }
  return c.dcl.size;
});
def("UNLOAD_DIALOG", (a, c) => (c.dcl.delete(num(a[0], "unload_dialog")), NIL));
def("NEW_DIALOG", (a, c) => {
  if (c.dlg?.open) throw new LispError("new_dialog: 대화상자 안에서 다른 대화상자를 열 수 없습니다");
  const tile = c.dcl.get(num(a[1], "new_dialog"))?.get(str(a[0], "new_dialog").toLowerCase());
  if (!tile) return NIL;
  const values = new Map<string, string>();
  const lists = new Map<string, string[]>();
  const disabled = new Set<string>();
  const walk = (t: Tile) => {
    if (t.key) {
      values.set(t.key, t.value ?? "");
      if (t.list) lists.set(t.key, [...t.list]);
      if (t.attrs.is_enabled === false) disabled.add(t.key);
    }
    t.children.forEach(walk);
  };
  walk(tile);
  c.dlg = { tile, values, lists, disabled, actions: new Map(), done: null, open: false };
  return T;
});
const dlg = (c: Run, fn: string): Dlg => {
  if (!c.dlg) throw new LispError(`${fn}: 먼저 new_dialog로 대화상자를 여세요`);
  return c.dlg;
};
def("SET_TILE", (a, c) => {
  dlg(c, "set_tile").values.set(str(a[0], "set_tile"), capStr(str(a[1], "set_tile")));
  return a[1];
});
def("GET_TILE", (a, c) => dlg(c, "get_tile").values.get(str(a[0], "get_tile")) ?? NIL);
def("GET_ATTR", (a, c) => {
  const key = str(a[0], "get_attr");
  const attr = str(a[1], "get_attr").toLowerCase();
  let found: Tile | undefined;
  const walk = (t: Tile) => {
    if (t.key === key) found = t;
    t.children.forEach(walk);
  };
  walk(dlg(c, "get_attr").tile);
  const v = found && Object.hasOwn(found.attrs, attr) ? found.attrs[attr] : undefined;
  return v === undefined ? NIL : String(v);
});
def("MODE_TILE", (a, c) => {
  const d = dlg(c, "mode_tile");
  const key = str(a[0], "mode_tile");
  const mode = num(a[1], "mode_tile");
  if (mode === 1) d.disabled.add(key);
  else if (mode === 0) d.disabled.delete(key);
  return NIL; // 2 focus, 3 select, 4 flip highlight: nothing to do on the web
});
def("ACTION_TILE", (a, c) => {
  const d = dlg(c, "action_tile");
  if (d.actions.size >= 500) throw new LispError("action_tile: 동작은 500개까지입니다");
  d.actions.set(str(a[0], "action_tile"), str(a[1], "action_tile"));
  return T;
});
def("START_LIST", (a, c) => {
  const d = dlg(c, "start_list");
  d.list = { key: str(a[0], "start_list"), op: a[1] !== undefined ? num(a[1], "start_list") : 3, index: a[2] !== undefined ? num(a[2], "start_list") : 0 };
  if (d.list.op === 3 || !d.lists.has(d.list.key)) d.lists.set(d.list.key, []); // 3 = new list; 2 = append; 1 = change one item
  return d.list.key;
});
def("ADD_LIST", (a, c) => {
  const d = dlg(c, "add_list");
  if (!d.list) throw new LispError("add_list: 먼저 start_list를 부르세요");
  const items = d.lists.get(d.list.key)!;
  if (d.list.op === 1) items[d.list.index] = str(a[0], "add_list");
  else items.push(str(a[0], "add_list"));
  if (items.length > 2000) throw new LispError("목록은 2,000줄까지입니다");
  return a[0];
});
def("END_LIST", (_a, c) => {
  dlg(c, "end_list").list = undefined;
  return NIL;
});
def("DONE_DIALOG", (a, c) => {
  const d = dlg(c, "done_dialog");
  d.done = a[0] !== undefined ? num(a[0], "done_dialog") : 1;
  return [0, 0];
});
def("TERM_DIALOG", (_a, c) => {
  if (c.dlg) c.dlg.done = 0;
  return NIL;
});
def("START_DIALOG", async (_a, c) => {
  const d = dlg(c, "start_dialog");
  if (d.open) throw new LispError("start_dialog: 대화상자가 이미 열려 있습니다");
  if (!c.host.dialog) throw new LispError("이 화면에서는 대화상자를 쓸 수 없습니다");
  const state = (): DialogState => ({ values: Object.fromEntries(d.values), lists: Object.fromEntries([...d.lists].map(([k, v]) => [k, [...v]])), disabled: [...d.disabled] });
  // the user's thinking time is given back to the time limit; the actions' own time is not
  let mark = Date.now();
  const refund = () => {
    c.deadline += Date.now() - mark;
    mark = Date.now();
  };
  const depth = c.depth + 1;
  const run: DialogRun = {
    tile: d.tile,
    state,
    act: async (key, value) => {
      if (!d.open) return { done: d.done ?? 0, state: state() };
      refund();
      try {
        d.values.set(key, capStr(value));
        const code = d.actions.get(key);
        if (code !== undefined) {
          const env: Env = { vars: new Map<Sym, Val>([[Sym.of("$KEY"), key], [Sym.of("$VALUE"), value], [Sym.of("$REASON"), 1]]), parent: null };
          for (const f of read(code)) await ev(f, env, c, depth);
        } else if (key === "accept") d.done = 1;
        else if (key === "cancel") d.done = 0;
        return { done: d.done, state: state() };
      } finally {
        mark = Date.now();
      }
    },
  };
  d.open = true;
  try {
    return await c.host.dialog(run);
  } finally {
    refund();
    d.open = false;
    c.dlg = null;
  }
});

// reactors: they stay in the screen's session and run on its events (fireReactors)
const REACTOR_LIMIT = 50;
const reactor = (v: Val, fn: string): Reactor => {
  if (typeof v !== "object" || isList(v) || v instanceof Sym || v.kind !== "reactor") throw new LispError(`${fn}: 반응자가 필요합니다`);
  return v;
};
const callbacks = (v: Val, fn: string): [string, Val][] =>
  list(v, fn).map((x) => {
    if (!isPair(x) || !(x.car instanceof Sym)) throw new LispError(`${fn}: (:이벤트 . 함수) 목록이 필요합니다`);
    return [x.car.name, x.cdr];
  });
const register = (c: Run, r: Reactor): Reactor => {
  if (c.session.reactors.length >= REACTOR_LIMIT) c.session.reactors = c.session.reactors.filter((x) => x.active); // removed ones go
  if (c.session.reactors.length >= REACTOR_LIMIT) throw new LispError(`반응자는 ${REACTOR_LIMIT}개까지입니다`);
  c.session.reactors.push(r);
  return r;
};
def("VLR-COMMAND-REACTOR VLR-EDITOR-REACTOR", (a, c) => register(c, { kind: "reactor", type: "command", data: a[0] ?? NIL, owners: [], events: callbacks(a[1], "vlr-command-reactor"), active: true }));
def("VLR-OBJECT-REACTOR", (a, c) =>
  register(c, { kind: "reactor", type: "object", data: a[1] ?? NIL, owners: list(a[0], "vlr-object-reactor").map((o) => vla(o, "vlr-object-reactor")).filter((o) => o.t === "ent").map((o) => (o as { handle: string }).handle), events: callbacks(a[2], "vlr-object-reactor"), active: true }),
);
def("VLR-REMOVE", (a) => {
  reactor(a[0], "vlr-remove").active = false;
  return a[0];
});
def("VLR-ADD", (a) => {
  reactor(a[0], "vlr-add").active = true;
  return a[0];
});
def("VLR-REMOVE-ALL", (_a, c) => {
  c.session.reactors.forEach((r) => (r.active = false));
  return NIL;
});
def("VLR-REACTORS", (_a, c) => c.session.reactors.filter((r) => r.active));
def("VLR-DATA", (a) => reactor(a[0], "vlr-data").data);
def("VLR-OWNERS", (a) => reactor(a[0], "vlr-owners").owners.map((h) => ({ kind: "vla", obj: { t: "ent", handle: h } }) as Vla));
def("VLR-ADDED-P", (a) => bool(reactor(a[0], "vlr-added-p").active));

export type ReactorEvent = { kind: "command"; name: string; command: string } | { kind: "object"; name: string; handle: string };

/** Runs the session's reactors for one screen event, as one AX-LISP run (same limits). */
export async function fireReactors(session: Session, event: ReactorEvent, host: Host = {}): Promise<Outcome | null> {
  const hits = session.reactors.filter((r) => r.active && r.type === event.kind && (event.kind === "command" || r.owners.includes(event.handle)));
  const calls = hits.flatMap((r) => r.events.filter(([e]) => e === event.name.toUpperCase()).map(([, fn]) => [r, fn] as const));
  if (!calls.length) return null;
  return guard(async () => {
    const c = newRun(host, session);
    const env: Env = { vars: new Map(), parent: null };
    for (const [r, fn] of calls) {
      const args: Val[] = event.kind === "command" ? [r, [event.command]] : [{ kind: "vla", obj: { t: "ent", handle: event.handle } }, r, NIL];
      await call(fn, args, env, c, 1);
    }
    return finish(c, NIL);
  });
}


// --- 2D automation A5: the AutoLISP core a routine expects (compared with cadcraft's list) ------

const int32 = (v: Val, fn: string): number => num(v, fn) | 0;
def("GCD", (a) => {
  let x = Math.abs(int32(a[0], "gcd"));
  let y = Math.abs(int32(a[1], "gcd"));
  while (y) [x, y] = [y, x % y];
  return x;
});
def("LOGAND", (a) => a.reduce<number>((r, x) => r & int32(x, "logand"), -1));
def("LOGIOR", (a) => a.reduce<number>((r, x) => r | int32(x, "logior"), 0));
def("LSH", (a) => {
  const n = int32(a[0], "lsh");
  const k = int32(a[1] ?? 0, "lsh");
  if (k >= 0) return k > 31 ? 0 : n << k;
  return k < -31 ? 0 : (n >>> -k) | 0;
});
def("~", (a) => ~int32(a[0], "~"));
def("ACAD_STRLSORT", (a) => {
  const l = list(a[0], "acad_strlsort").map((x) => str(x, "acad_strlsort"));
  const key = (x: string) => x.toLowerCase();
  return cap(l.sort((p, q) => (key(p) < key(q) ? -1 : key(p) > key(q) ? 1 : p < q ? -1 : p > q ? 1 : 0)));
});
def("VL-LIST-LENGTH", (a) => {
  if (isList(a[0])) return a[0].length;
  let n = 0;
  for (let p: Val = a[0]; isPair(p); p = p.cdr) if (++n > LIMITS.list) return NIL;
  return n;
});
def("VL-SYMBOLP", (a) => bool(a[0] instanceof Sym || (isList(a[0]) && a[0].length === 0)));
def("WCMATCH", (a) => bool(wcmatch(str(a[0], "wcmatch"), str(a[1], "wcmatch"))));
def("VL-STRING-TRANSLATE", (a) => {
  const [from, to, text] = [str(a[0], "vl-string-translate"), str(a[1], "vl-string-translate"), str(a[2], "vl-string-translate")];
  if (from.length !== to.length) throw new LispError("vl-string-translate: 원본과 대상 문자 수가 같아야 합니다");
  return capStr([...text].map((ch) => (from.includes(ch) ? to[from.indexOf(ch)] : ch)).join(""));
});
def("VL-FILENAME-DIRECTORY", (a) => {
  const t = str(a[0], "vl-filename-directory").replace(/\\/g, "/");
  const i = t.lastIndexOf("/");
  return i < 0 ? "" : t.slice(0, i);
});
// there is no UCS yet: every point is in the world coordinate system (trans returns it as it is)
def("TRANS", (a) => {
  const p = list(a[0], "trans");
  if (p.length < 2 || p.some((x) => typeof x !== "number")) throw new LispError(`trans: 점이 필요합니다 (${show(a[0])})`);
  return [...p];
});
def("COMMAND-S", (a, c) => command(a, c));
def("VL-CMDF", (a, c) => {
  command(a, c);
  return T;
});
def("SSGETFIRST", () => [NIL, NIL]); // no pickfirst grips/selection to give back
def("GC REDRAW", () => NIL);
def("VER", () => "AX-LISP 2.2");
def("EXIT QUIT", () => {
  throw new LispError("quit / exit abort");
});
def("AUTOLOAD", () => NIL); // a c: command of a library file is found by its name, so nothing needs registering
def("READ", (a) => read(str(a[0], "read"))[0] ?? NIL);

/** (load "name"): a .lsp file of the PC library runs in the namespace of this run. */
def("LOAD", async (a, c) => {
  const given = fileName(str(a[0], "load"), "load");
  const name = /\.[A-Za-z0-9]+$/.test(given) ? given : `${given}.lsp`;
  if (c.loading >= 3) throw new LispError("load: 파일을 3단계보다 깊게 불러올 수 없습니다");
  const src = await files(c, "load").read(name);
  if (src === null) {
    if (a.length > 1) return a[1]; // (load "x" onfailure)
    throw new LispError(`load: 라이브러리에 없는 파일입니다 (${name})`);
  }
  c.loading++;
  try {
    let v: Val = NIL;
    const env: Env = { vars: new Map(), parent: null };
    for (const f of read(src)) v = await ev(f, env, c, c.depth + 1);
    return v;
  } finally {
    c.loading--;
  }
});

// initget + getkword: keywords of the form "Yes No" (the capitals are what may be typed short)
def("INITGET", (a, c) => {
  const bits = typeof a[0] === "number" ? Math.trunc(a[0]) : 0;
  const words = (typeof a[0] === "string" ? a[0] : typeof a[1] === "string" ? a[1] : "").split(/\s+/).filter(Boolean);
  const at = words.indexOf("_"); // the language-independent names after "_" are not needed here
  c.kw = { bits, words: (at < 0 ? words : words.slice(0, at)).slice(0, 100) };
  return NIL;
});
function keyword(input: string, words: string[]): string | null {
  const u = input.trim().toLowerCase();
  if (!u) return null;
  for (const w of words) {
    const need = /^[A-Z0-9]+/.exec(w)?.[0] ?? w; // "Yes" -> "Y"; an all-capital word must be typed in full
    const full = need.length === w.length ? w.length : need.length;
    if (w.toLowerCase() === u || (u.length >= full && w.toLowerCase().startsWith(u) && u.length >= need.length)) return w;
  }
  return null;
}
def("GETKWORD", async (a, c) => {
  if (!c.host.input) throw new LispError("이 화면에서는 입력 함수(get…)를 쓸 수 없습니다");
  const kw = c.kw;
  c.kw = null; // initget applies to one get… call
  const prompt = typeof a[0] === "string" ? a[0] : "";
  for (let tries = 0; tries < 5; tries++) {
    const v = await waiting(c, () => c.host.input!("string", prompt, undefined));
    if (v === null) return NIL;
    const text = String(v);
    if (text.trim() === "") {
      if (kw && kw.bits & 1) continue; // initget 1: an empty answer is not accepted
      return NIL;
    }
    const w = kw ? keyword(text, kw.words) : null;
    if (w) return w;
    log(c, `잘못된 옵션 키워드: ${text}`);
  }
  return NIL;
});

// distof / angtof: text back to a number (the reverse of rtos / angtos)
def("DISTOF", (a) => {
  const t = str(a[0], "distof").trim();
  const mode = a.length > 1 ? Math.trunc(num(a[1], "distof")) : 2;
  if (mode === 5) {
    const m = /^(-)?(?:(\d+)[\s-]+)?(\d+)\/(\d+)$|^(-)?(\d+(?:\.\d*)?)$/.exec(t);
    if (!m) return NIL;
    if (m[6] !== undefined) return (m[5] ? -1 : 1) * Number(m[6]);
    if (Number(m[4]) === 0) return NIL;
    return (m[1] ? -1 : 1) * (Number(m[2] ?? 0) + Number(m[3]) / Number(m[4]));
  }
  if (mode !== 1 && mode !== 2) return NIL; // engineering / architectural units are not supported
  return /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(t) ? Number(t) : NIL;
});
def("ANGTOF", (a) => {
  const t = str(a[0], "angtof").trim();
  const unit = a.length > 1 ? Math.trunc(num(a[1], "angtof")) : 0;
  const n = "[-+]?(?:\\d+\\.?\\d*|\\.\\d+)";
  if (unit === 0 || unit === 2 || unit === 3) {
    const m = new RegExp(`^(${n})(g|r)?$`, "i").exec(t);
    if (!m) return NIL;
    const v = Number(m[1]);
    const u = (m[2] ?? (unit === 2 ? "g" : unit === 3 ? "r" : "")).toLowerCase();
    return u === "g" ? (v * Math.PI) / 200 : u === "r" ? v : (v * Math.PI) / 180;
  }
  if (unit === 1) {
    const m = new RegExp(`^(${n})d(?:\\s*(\\d+(?:\\.\\d*)?)')?(?:\\s*(\\d+(?:\\.\\d*)?)")?$`, "i").exec(t);
    if (!m) return NIL;
    const deg = Math.abs(Number(m[1])) + Number(m[2] ?? 0) / 60 + Number(m[3] ?? 0) / 3600;
    return ((Number(m[1]) < 0 || m[1].startsWith("-") ? -deg : deg) * Math.PI) / 180;
  }
  return NIL; // grads / surveyor forms other than the ones above
});
