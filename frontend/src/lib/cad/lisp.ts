// Track 11: AX-LISP — a safe AutoLISP subset (plan.md §17). Its own reader and evaluator: no eval,
// network or OS; the drawing changes through (command ...) inputs, which the screen runs through the
// command prompt reducer afterwards, and (2D automation A1, plan.md §20) through entmake/entmod/
// entdel and vla- objects on a copy of the drawing (objmodel.ts) — all applied as one undo step,
// nothing on an error. Files are the PC LISP library only; DCL dialogs are drawn by the screen.
import type { Pt } from "./view";
import type { Sel } from "./tools";
import { parseDcl, type Tile } from "./dcl";
import { wcmatch } from "./wcmatch";
import { axHas, axMembers, classOf, isByRef } from "./axobj";
import { describe, DrawingModel, fromGroups, getProp, invoke, itemsOf, isModelObj, matches, ModelError, putProp, toGroups, utf8, type Changes, type Ent, type Group, type Obj, type OVal, type TableKind } from "./objmodel";

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
/** W9: a REAL whose value is a whole number (2.0). AutoLISP keeps INT and REAL apart — (/ 1 2) is 0,
 * (princ 2.0) prints 2.0 — and a JS number cannot tell 2 from 2.0; other reals are plain numbers. */
export class Flo {
  readonly kind = "real" as const;
  constructor(readonly v: number) {}
}
/** A REAL as a value: boxed when it is whole. */
export const real = (x: number): number | Flo => (Number.isInteger(x) ? new Flo(x) : x);
const isNum = (v: Val | undefined): v is number | Flo => typeof v === "number" || v instanceof Flo;
const isReal = (v: Val): boolean => v instanceof Flo || (typeof v === "number" && !Number.isInteger(v));
/** A number's value (INT or REAL). */
export const nv = (v: number | Flo): number => (v instanceof Flo ? v.v : v);
export type SelSet = { kind: "ss"; items: Sel[] };
/** obj (W2): what an ename names when it is not a drawing entity — a dictionary or XRecord (by owner
 * entity + path from the root), a table record, or an object entmakex made that dictadd places. */
export type ObjRef = { t: "dict"; owner?: string; path: string[] } | { t: "tbl"; table: string; name: string } | { t: "new"; type: "XRECORD" | "DICTIONARY"; tags: [number, unknown][] };
export type Ename = { kind: "ename"; sel: Sel; obj?: ObjRef };
type Builtin = { kind: "builtin"; name: string; fn: (args: Val[], c: Run) => Val | Promise<Val> };
type Lambda = { kind: "lambda"; name: string; params: Sym[]; locals: Sym[]; body: Val[]; source?: Val[] };
export type Vla = { kind: "vla"; obj: Obj };
export type FileDesc = { kind: "file"; name: string; mode: "r" | "w" | "a"; text: string; pos: number; open: boolean };
/** type: command / object, and (W1) acdb, docmanager, dwg, dxf, insert, linker, lisp, miscellaneous, mouse, sysvar, toolbar, undo, wblock, window, xref, deepclone */
export type Reactor = { kind: "reactor"; type: string; data: Val; owners: string[]; events: [string, Val][]; active: boolean; persistent?: boolean };
export type Val = number | Flo | string | Sym | Val[] | Pair | SelSet | Ename | Builtin | Lambda | Vla | FileDesc | Reactor;

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
    if (NUMBER.test(tk.t)) return /[.eE]/.test(tk.t) ? real(Number(tk.t)) : Number(tk.t); // W9: 2.0 is a REAL
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

/** princ's form: strings without quotes, also inside lists (prin1 / print quote them). */
export function plain(v: Val): string {
  if (typeof v === "string") return v;
  if (isList(v)) return v.length ? `(${v.map(plain).join(" ")})` : "nil";
  if (isPair(v)) return `(${plain(v.car)} . ${plain(v.cdr)})`;
  return show(v);
}

export function show(v: Val): string {
  if (v === undefined || v === null) return "(없음)"; // a missing argument: the message, not a TypeError
  if (v instanceof Flo) return `${v.v}.0`;
  if (typeof v === "number") {
    if (Number.isInteger(v)) return String(v);
    const t = String(Number(v.toFixed(8)));
    return /[.e]/.test(t) ? t : `${t}.0`; // a REAL rounded to a whole number still prints as one (6e-17 -> 0.0)
  }
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
  if (v instanceof Flo) return v.v;
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
  /** open/read-line/write-line, load_dialog, findfile: files of the PC LISP library only.
   * W3: list/remove (data files only) for vl-directory-files, vl-file-delete/rename/copy. */
  files?: {
    read: (name: string) => Promise<string | null>;
    write: (name: string, text: string) => Promise<void>;
    list?: () => Promise<{ name: string; modified?: string | null }[]>;
    remove?: (name: string) => Promise<boolean>;
  };
  /** W3: getenv/setenv/getcfg/setcfg — this PC's settings (kept by the screen); session-only without it. */
  settings?: { get: (key: string) => string | null; set: (key: string, value: string) => void };
  /** W2: what is saved in the drawing beyond the editable geometry — an object's DXF groups (XDATA,
   * extension dictionary) and dictionary entries (GET …/entities/{handle}, …/dictionaries). */
  objects?: {
    entity: (handle: string) => Promise<{ type: string; tags: [number, unknown][] } | null>;
    dict: (path: string[], owner?: string) => Promise<{ type: string; handle: string; entries?: { name: string; type: string | null; handle: string }[]; tags?: [number, unknown][] } | null>;
  };
  /** W1: grdraw/grvecs/grclear — temporary vectors on the screen ([] clears). */
  draw?: (vectors: { from: Pt; to: Pt; color: number }[]) => void;
  /** start_dialog: the screen draws the DCL dialog; act() runs the tile's action and says when it ends. */
  dialog?: (d: DialogRun) => Promise<number>;
};

/** A DCL dialog while it is open: its tiles, their current state, and the action runner. */
export type DialogRun = {
  tile: Tile;
  state: () => DialogState;
  act: (key: string, value: string) => Promise<{ done: number | null; state: DialogState }>;
};
export type DialogState = { values: Record<string, string>; lists: Record<string, string[]>; disabled: string[]; images?: Record<string, ImageOp[]> };

/** What stays between runs on one screen (AutoCAD keeps a drawing's LISP namespace): defined
 * functions and variables, and the reactors waiting for events. */
export type Session = {
  globals: Map<Sym, Val>;
  reactors: Reactor[];
  /** W1: vl-bb-ref/set (shared by every run of the screen), trace, settings without a host, error modes */
  bb?: Map<string, Val>;
  traced?: Set<string>;
  settings?: Map<string, string>;
  errorModes?: string[];
  lastError?: string;
  dbmod?: number[];
  lastLayerState?: string;
  dictPos?: Map<string, number>; // dictnext's position per dictionary
};
export const newSession = (): Session => ({ globals: new Map(), reactors: [], bb: new Map(), traced: new Set(), settings: new Map(), errorModes: [], dbmod: [] });

/** A run of command prompt inputs with the selection and current layer they use. */
export type Segment = { inputs: string[]; sel: Sel[]; layer?: string };
export type Outcome = { segments: Segment[]; log: string[]; commands: string[]; value: Val; changes: Changes | null };

// Maps, not objects: tile keys come from the program ("constructor", "__proto__" are just keys)
type Dlg = { tile: Tile; values: Map<string, string>; lists: Map<string, string[]>; disabled: Set<string>; actions: Map<string, string>; done: number | null; open: boolean; list?: { key: string; op: number; index: number }; images?: Map<string, ImageOp[]>; image?: string; clientData?: Map<string, string> };
/** W1: what a DCL image tile shows (fill_image / vector_image / slide_image), in tile pixels. */
export type ImageOp = { op: "fill"; x: number; y: number; w: number; h: number; color: number } | { op: "vector"; x1: number; y1: number; x2: number; y2: number; color: number } | { op: "slide"; x: number; y: number; w: number; h: number; name: string };
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
  hostCalls: number; // W3 review M2: file / settings requests of this run
  objectReads: number; // W2 review: drawing reads of this run (cached per revision)
  env?: Env; // the variables of the call in progress
  serverWaited: number;
  listCache?: { name: string; modified?: string | null }[];
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
    if (undo > 0 && ((typeof a === "string" && /^[_.]*(BE|BEGIN|E|END|G|GROUP|M|MARK|B|BACK|A|AUTO|C|CONTROL|ALL|N|NONE|O|ONE|ON|OFF|COMBINE|LAYER)$/i.test(a)) || isNum(a))) {
      undo--;
      continue;
    }
    undo = 0;
    if (isNum(a)) items.push(fmt(nv(a)));
    else if (typeof a === "string") {
      if (skipEnter && a === "") {
        skipEnter = false; // the "" that ends object selection in AutoLISP
        continue;
      }
      items.push(items.length ? a : a.replace(/^[_.]+/, "")); // "_.LINE" -> "LINE"
    } else if (isList(a) && a.length >= 2 && a.every((x) => isNum(x))) items.push((c.host.point3 ? a.slice(0, 3) : a.slice(0, 2)).map((x) => fmt(nv(x as number | Flo))).join(","));
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
  if (s.name in VB_TYPES) return VB_TYPES[s.name]; // vlax-vbDouble ... (variant / safearray types)
  const b = BUILTINS.get(s.name) ?? vlaFunction(s.name);
  if (b) return b;
  if (SANDBOXED.test(s.name)) return refused(s.name);
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
    const [outer, outerEnv] = [c.depth, c.env];
    c.depth = depth; // callbacks a builtin makes (mapcar, vl-sort, actions) go deeper, never back to 1
    c.env = env; // W4: the caller's variables (ByRef results of vla- methods)
    try {
      return await f.fn(args, c);
    } finally {
      c.depth = outer;
      c.env = outerEnv;
    }
  }
  const traced = c.session.traced?.has(f.name.toUpperCase());
  if (traced) log(c, `Entering (${f.name}${args.map((x) => ` ${show(x)}`).join("")})`);
  if (args.length > f.params.length) throw new LispError(`${f.name}: 인수가 너무 많습니다`);
  if (args.length < f.params.length) throw new LispError(`${f.name}: 인수가 모자랍니다`);
  // AutoLISP scope is dynamic: the callee sees the caller's variables
  const vars = new Map<Sym, Val>();
  f.params.forEach((p, i) => vars.set(p, args[i]));
  f.locals.forEach((l) => vars.set(l, NIL));
  const inner: Env = { vars, parent: env };
  let r: Val = NIL;
  for (const x of f.body) r = await ev(x, inner, c, depth + 1);
  if (traced) log(c, `Result:  ${show(r)}`);
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
        const all = itemsOf(m, coll);
        if (!all) throw new LispError("vlax-for: 컬렉션(ModelSpace, Layers, Blocks ...)이 필요합니다");
        const items: Val[] = all.map((o) => fromO(o));
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
      case "DEFUN-Q": {
        // W1: a function kept as its list (args body...) so defun-q-list-ref/set can read and change it
        const name = rest[0];
        if (!(name instanceof Sym)) throw new LispError("defun-q: 함수 이름이 필요합니다");
        if (BUILTINS.has(name.name) || SPECIAL.has(name.name)) throw new LispError(`defun-q: 내장 함수 ${name.name}는 다시 정의할 수 없습니다`);
        c.globals.set(name, lambda(name.name, rest[1], rest.slice(2), [rest[1] ?? NIL, ...rest.slice(2)]));
        return name;
      }
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
/** plan §22.5: left out on purpose (other programs, ARX/VBA loading, the registry, network, AutoCAD's
 * own menus and help) — they fail with this message instead of "not a function". The same list as
 * tools/coverage/lisp_coverage.py (EXCLUDED). */
const SANDBOXED =
  /^(STARTAPP|ARX.*|ADS|MENUCMD|MENU.*|VL-REGISTRY-.*|VLAX-(CREATE|GET|GET-OR-CREATE)-OBJECT|VLAX-IMPORT-TYPE-LIBRARY|VLAX-TYPEINFO-.*|VL-VBA.*|VLISP-COMPILE|VL-LOAD-ALL|VLAX-ADD-CMD|VL-DOC-.*|VL-EXIT-.*|ACET-REG-.*|ACET-INI-.*|ACET-FILE-(CHDIR|CWD|RMDIR)|TEXTSCR|GRAPHSCR|HELP|SETFUNHELP|AUTOARXLOAD|VL-ARX-IMPORT|VLAX-(MACHINE-|USER-)?PRODUCT-KEY|SHOWHTMLMODALWINDOW|ACET-SYS-(COMMAND|SPAWN|TERM|PROCID|WAIT|FOREGROUND)|ACAD_HELPDLG|ACET-HELP(-TRAP)?|TEXTPAGE)$/;
const refusedCache = new Map<string, Builtin>();
const refused = (name: string): Builtin => {
  let b = refusedCache.get(name);
  if (!b)
    refusedCache.set(
      name,
      (b = {
        kind: "builtin",
        name,
        fn: () => {
          throw new LispError(`${name.toLowerCase()}: 보안상 AX-LISP에서 쓸 수 없는 함수입니다 (외부 프로그램·ARX·VBA·레지스트리·네트워크·AutoCAD 메뉴·도움말)`);
        },
      }),
    );
  return b;
};
const VB_TYPES: Record<string, number> = {
  "VLAX-VBEMPTY": 0, "VLAX-VBNULL": 1, "VLAX-VBINTEGER": 2, "VLAX-VBLONG": 3, "VLAX-VBSINGLE": 4, "VLAX-VBDOUBLE": 5, "VLAX-VBCURRENCY": 6, "VLAX-VBDATE": 7, "VLAX-VBSTRING": 8,
  "VLAX-VBOBJECT": 9, "VLAX-VBERROR": 10, "VLAX-VBBOOLEAN": 11, "VLAX-VBVARIANT": 12, "VLAX-VBDATAOBJECT": 13, "VLAX-VBDECIMAL": 14, "VLAX-VBBYTE": 17, "VLAX-VBARRAY": 8192,
};
const SPECIAL = new Set(["QUOTE", "SETQ", "IF", "COND", "PROGN", "AND", "OR", "WHILE", "REPEAT", "FOREACH", "LAMBDA", "DEFUN", "DEFUN-Q", "VLAX-FOR", "FUNCTION", "SET", "BOUNDP", "VL-SYMBOL-VALUE", "EVAL"]);

function lambda(name: string, spec: Val, body: Val[], source?: Val[]): Lambda {
  const s = list(spec ?? NIL, name);
  const slash = s.findIndex((v) => v instanceof Sym && v.name === "/");
  const params = (slash < 0 ? s : s.slice(0, slash)) as Val[];
  const locals = slash < 0 ? [] : s.slice(slash + 1);
  for (const p of [...params, ...locals]) if (!(p instanceof Sym)) throw new LispError(`${name}: 인수 이름이 필요합니다 (${show(p)})`);
  return { kind: "lambda", name, params: params as Sym[], locals: locals as Sym[], body, ...(source ? { source } : {}) };
}

// --- builtins -----------------------------------------------------------------------------

const BUILTINS = new Map<string, Builtin>();
const def = (names: string, fn: Builtin["fn"]) => names.split(" ").forEach((n) => BUILTINS.set(n, { kind: "builtin", name: n, fn }));
/** Several names, each with a body made for its own name. */
const defEach = (names: string, make: (name: string) => Builtin["fn"]) => names.split(" ").forEach((n) => BUILTINS.set(n, { kind: "builtin", name: n, fn: make(n) }));

// W9: INT op INT is an INT (/ truncates, as AutoLISP); a REAL anywhere makes a REAL
const fold = (name: string, f: (a: number, b: number) => number, unit?: number, intOp?: (a: number, b: number) => number) => (args: Val[]) => {
  const ns = args.map((a) => num(a, name));
  if (!ns.length) {
    if (unit === undefined) throw new LispError(`${name}: 인수가 필요합니다`);
    return unit;
  }
  // (a, b) only: reduce also passes the index and the array (Math.max(a, b, i, array) is NaN)
  const g = intOp ?? f;
  if (args.some(isReal)) return real(out(ns.slice(1).reduce((x, y) => f(x, y), ns[0])));
  return out(ns.slice(1).reduce((x, y) => g(x, y), ns[0]));
};
const intDiv = (a: number, b: number) => {
  if (b === 0) throw new LispError("/: 0으로 나눌 수 없습니다");
  return Math.trunc(a / b);
};
const keep = (v: Val, x: number): Val => (isReal(v) ? real(x) : x); // the argument's type
def("+", fold("+", (a, b) => a + b, 0));
def("*", fold("*", (a, b) => a * b, 1));
def("-", (args) => (args.length === 1 ? keep(args[0], -num(args[0], "-")) : fold("-", (a, b) => a - b)(args)));
def("/", fold("/", (a, b) => a / b, undefined, intDiv));
def("REM", fold("rem", (a, b) => a % b, undefined, (a, b) => (b === 0 ? intDiv(a, b) : a % b)));
def("1+", (a) => keep(a[0], num(a[0], "1+") + 1));
def("1-", (a) => keep(a[0], num(a[0], "1-") - 1));
def("ABS", (a) => keep(a[0], Math.abs(num(a[0], "abs"))));
def("MIN", fold("min", Math.min));
def("MAX", fold("max", Math.max));
def("SQRT", (a) => real(out(Math.sqrt(num(a[0], "sqrt")))));
def("SIN", (a) => real(Math.sin(num(a[0], "sin"))));
def("COS", (a) => real(Math.cos(num(a[0], "cos"))));
def("ATAN", (a) => real(a.length > 1 ? Math.atan2(num(a[0], "atan"), num(a[1], "atan")) : Math.atan(num(a[0], "atan"))));
def("EXPT", (a) => {
  const x = out(num(a[0], "expt") ** num(a[1], "expt"));
  return isReal(a[0]) || isReal(a[1]) || num(a[1], "expt") < 0 ? real(x) : x;
});
def("EXP", (a) => real(out(Math.exp(num(a[0], "exp")))));
def("LOG", (a) => real(out(Math.log(num(a[0], "log")))));
def("FIX", (a) => Math.trunc(num(a[0], "fix")));
def("FLOAT", (a) => real(num(a[0], "float")));
const cmp = (name: string, f: (a: number, b: number) => boolean) => (args: Val[]) => {
  for (let i = 1; i < args.length; i++) if (!f(num(args[i - 1], name), num(args[i], name))) return NIL;
  return T;
};
def("<", cmp("<", (a, b) => a < b));
def(">", cmp(">", (a, b) => a > b));
def("<=", cmp("<=", (a, b) => a <= b));
def(">=", cmp(">=", (a, b) => a >= b));
const same = (a: Val, b: Val): boolean =>
  isNum(a) && isNum(b) ? Math.abs(nv(a) - nv(b)) < 1e-12 : isList(a) && isList(b) ? a.length === b.length && a.every((x, i) => same(x, b[i])) : isPair(a) && isPair(b) ? same(a.car, b.car) && same(a.cdr, b.cdr) : a === b;
def("=", (a) => bool(a.slice(1).every((x) => same(x, a[0]))));
def("/=", (a) => bool(!same(a[0], a[1])));
def("EQ EQUAL", (a) => bool(same(a[0], a[1])));
def("NOT NULL", (a) => bool(!truthy(a[0])));
def("ZEROP", (a) => bool(num(a[0], "zerop") === 0));
def("MINUSP", (a) => bool(num(a[0], "minusp") < 0));
def("NUMBERP", (a) => bool(isNum(a[0])));
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
def("ATOF", (a) => real(Number.parseFloat(str(a[0], "atof")) || 0));
def("ANGTOS", (a) => ((num(a[0], "angtos") * 180) / Math.PI).toFixed(a.length > 2 ? num(a[2], "angtos") : 0));

def("POLAR", (a) => {
  const [x, y] = pt(a[0], "polar");
  const ang = num(a[1], "polar");
  const d = num(a[2], "polar");
  const p = list(a[0], "polar");
  const xy = [real(x + d * Math.cos(ang)), real(y + d * Math.sin(ang))];
  return p.length > 2 ? [...xy, real(num(p[2], "polar"))] : xy; // a 3D point keeps its Z
});
def("DISTANCE", (a) => {
  const [p, q] = [pt(a[0], "distance"), pt(a[1], "distance")];
  return real(Math.hypot(q[0] - p[0], q[1] - p[1]));
});
def("ANGLE", (a) => {
  const [p, q] = [pt(a[0], "angle"), pt(a[1], "angle")];
  const r = Math.atan2(q[1] - p[1], q[0] - p[0]);
  return real(r < 0 ? r + 2 * Math.PI : r);
});
def("INTERS", (a) => {
  const [p1, p2, p3, p4] = a.slice(0, 4).map((v) => pt(v, "inters"));
  const d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0]);
  if (Math.abs(d) < 1e-12) return NIL;
  const t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d;
  const u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d;
  const onSeg = a.length < 5 || truthy(a[4]);
  if (onSeg && (t < 0 || t > 1 || u < 0 || u > 1)) return NIL;
  const xy = [real(p1[0] + t * (p2[0] - p1[0])), real(p1[1] + t * (p2[1] - p1[1]))];
  return list(a[0], "inters").length > 2 ? [...xy, real(0)] : xy;
});

defEach("PRINC PRINT PRIN1 PROMPT ALERT TERPRI", (name) => (a, c) => {
  const f = a[1];
  // W9: princ prints strings plainly at every depth; prin1 / print keep the quotes inside lists
  const text = (v: Val) => (typeof v === "string" ? v : name === "PRIN1" || name === "PRINT" ? show(v) : plain(v));
  if (a.length && f && typeof f === "object" && !isList(f) && !(f instanceof Sym) && f.kind === "file") {
    writeTo(f, text(a[0]));
    return a[0];
  }
  if (a.length) log(c, text(a[0]));
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
  if (Array.isArray(v)) return [real(v[0]), real(v[1]), real(0)]; // a point: REALs, 3D (W9)
  return kind === "int" || typeof v === "string" ? v : real(v);
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
def("ENTGET", async (a, c) => {
  const e = a[0];
  if (typeof e !== "object" || isList(e) || e instanceof Sym || e.kind !== "ename") throw new LispError("entget: 객체 이름(entsel/ssname)이 필요합니다");
  if (e.obj) return objGroups(c, e); // W2: dictionaries, XRecords, table records
  const m = c.host.model;
  if (!m) return entget(e.sel);
  const cur = m.get(e.sel.handle);
  if (!cur) return NIL; // deleted
  const out: Val[] = [pair(-1, e), ...toGroups(cur).map(([code, v]) => grp(code, v))];
  const apps = a[1] !== undefined && truthy(a[1]) ? list(a[1], "entget").map((x) => str(x, "entget")) : null;
  const xdict = await extDict(c, cur.handle);
  if (xdict) out.splice(1, 0, pair(102, "{ACAD_XDICTIONARY"), pair(360, xdict), pair(102, "}"));
  if (apps) {
    const xd = await xdataOf(c, cur);
    const pick = Object.entries(xd).filter(([app, g]) => g.length && apps.some((p) => wcmatch(app.toUpperCase(), p.toUpperCase())));
    if (pick.length) out.push([-3, ...pick.map(([app, g]) => [app, ...g.map(([code, v]) => grp(code, v))])]);
  }
  return out;
});

const pair = (code: number, v: Val): Pair => ({ kind: "pair", car: code, cdr: v });
const p3 = (code: number, p: Pt): Val[] => [code, real(p[0]), real(p[1]), real(0)];
/** The DXF group codes AutoLISP routines read most: type, layer, handle and the main geometry. */
function entget(s: Sel): Val[] {
  const g = s.geom as Record<string, unknown> & { type: string };
  const out: Val[] = [pair(0, g.type), pair(5, s.handle), pair(8, s.layer)];
  const P = (k: string) => g[k] as Pt | undefined;
  if (P("start")) out.push(p3(10, P("start")!));
  if (P("end")) out.push(p3(11, P("end")!));
  if (P("center")) out.push(p3(10, P("center")!));
  if (P("insert")) out.push(p3(10, P("insert")!));
  if (typeof g.radius === "number") out.push(pair(40, real(g.radius)));
  if (typeof g.height === "number") out.push(pair(40, real(g.height)));
  if (typeof g.value === "string") out.push(pair(1, g.value));
  if (typeof g.name === "string") out.push(pair(2, g.name));
  if (typeof g.start_angle === "number") out.push(pair(50, real(((g.start_angle as number) * Math.PI) / 180)));
  if (typeof g.end_angle === "number") out.push(pair(51, real(((g.end_angle as number) * Math.PI) / 180)));
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
  hostCalls: 0,
  objectReads: 0,
  serverWaited: 0,
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
    if (e instanceof Error) c.session.lastError = e.message;
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
/** DXF group codes whose values are REALs (coordinates, sizes, angles, scales). */
const realCode = (c: number) => (c >= 10 && c <= 59) || (c >= 110 && c <= 149) || (c >= 210 && c <= 239) || (c >= 460 && c <= 469) || (c >= 1010 && c <= 1059);
/** AutoLISP group (code . value) or (10 x y z) -> [code, value] for the object model. */
function groups(l: Val[], fn: string): Group[] {
  return l.map((x) => {
    if (isPair(x) && typeof x.car === "number") return [x.car, fromVal(x.cdr)] as Group;
    if (isList(x) && typeof x[0] === "number") return [x[0], x.length === 2 && !isList(x[1]) ? fromVal(x[1]) : x.slice(1).map((v) => fromVal(v))] as Group;
    throw new LispError(`${fn}: 그룹 코드 목록이 필요합니다 (${show(x)})`);
  });
}
/** [code, value] -> (code . value), or (code x y z) for a point. */
const grp = (code: number, v: unknown): Val =>
  Array.isArray(v) && v.every((x) => typeof x === "number") ? [code, ...(v as number[]).map((x) => (realCode(code) ? real(x) : x))] : pair(code, realCode(code) && typeof v === "number" ? real(v) : toVal(v));

function fromVal(v: Val): unknown {
  if (v instanceof Flo) return v.v;
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
    const [plain, xd] = splitXdata(list(a[0], fn), c, fn);
    const g = groups(plain, fn);
    const r = fromGroups(g);
    made = model(c, fn).add(r.geom, r.layer ?? c.layer ?? model(c, fn).current, { aci: r.aci, linetype: r.linetype, ...r.props, ...(xd ? { xd: checkXdataSize(xd) } : {}) });
    return T;
  });
  return made;
};
/** W2: (0 . "XRECORD") / (0 . "DICTIONARY") from entmake(x): an object dictadd places in a dictionary. */
const newObject = (a: Val[], fn: string): Ename | null => {
  const l = isList(a[0]) ? a[0] : [];
  const type = (l.find((x) => isPair(x) && x.car === 0) as Pair | undefined)?.cdr;
  if (type !== "XRECORD" && type !== "DICTIONARY") return null;
  const tags = groups(l.filter((x) => !(isPair(x) && (x.car === 0 || x.car === 100 || x.car === 5 || x.car === 330 || x.car === 280 || x.car === 281))), fn);
  return { kind: "ename", sel: { handle: `~${type === "XRECORD" ? "X" : "D"}${++newObjects}`, layer: "0", geom: { type } as unknown as Sel["geom"] }, obj: { t: "new", type, tags } };
};
let newObjects = 0;
def("ENTMAKE", (a, c) => (newObject(a, "entmake") || entmake(a, c, "entmake") ? a[0] : NIL));
def("ENTMAKEX", (a, c) => {
  const o = newObject(a, "entmakex");
  if (o) return o;
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
    const [plain, xd] = splitXdata(l.filter((x) => x !== who), c, "entmod");
    const r = fromGroups(groups(plain, "entmod"), old);
    m.set(old.handle, { geom: r.geom, ...(r.layer ? { layer: r.layer } : {}), ...(r.aci !== undefined ? { aci: r.aci } : {}), ...(r.linetype ? { linetype: r.linetype } : {}), ...r.props, ...(xd ? { xd: checkXdataSize({ ...(old.xd ?? {}), ...xd }) } : {}) });
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
  if (v instanceof Flo) return v.v;
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
// W9: ActiveX properties are Doubles (lengths, points, angles) except these Integer / Long ones
const INT_PROPS = new Set(
  "COLOR COUNT OBJECTID OWNERID LINEWEIGHT ATTACHMENTPOINT ACTIVESPACE TABORDER PLOTTYPE MODE ALIGNMENT HORIZONTALALIGNMENT VERTICALALIGNMENT DRAWINGDIRECTION TYPE ARROWHEADTYPE ARROWHEAD1TYPE ARROWHEAD2TYPE PRIMARYUNITSPRECISION ALTUNITSPRECISION UNITSFORMAT ALTUNITSFORMAT TEXTCOLOR DIMENSIONLINECOLOR EXTENSIONLINECOLOR PATTERNTYPE HATCHSTYLE NUMBEROFLOOPS NUMBEROFVERTICES NUMBEROFFITPOINTS NUMBEROFCONTROLPOINTS DEGREE LOCALEID STANDARDSCALE PAPERUNITS PLOTROTATION TOLERANCEDISPLAY TOLERANCEPRECISION FRACTIONFORMAT ANGLEFORMAT TEXTMOVEMENT FIT TEXTINSIDEALIGN TEXTOUTSIDEALIGN BLOCKSCALING UNITS INSUNITS ENTITYTRANSPARENCY".split(" "),
);
const asReal = (v: Val): Val => (typeof v === "number" ? real(v) : isList(v) && v.every((x) => typeof x === "number") ? v.map((x) => real(x as number)) : v);
const vget = (c: Run, o: Val, p: string, fn: string) => {
  const v = fromO(getProp(model(c, fn), vla(o, fn), p));
  return INT_PROPS.has(p.toUpperCase()) ? v : asReal(v);
};
const vput = (c: Run, o: Val, p: string, v: Val, fn: string) => (putProp(model(c, fn), vla(o, fn), p, toO(v)), NIL);
/** W4: a method's ByRef results go to the quoted symbols given for them ((vla-GetBoundingBox o 'mn 'mx)). */
const vcall = (c: Run, o: Val, m: string, args: Val[], fn: string) => {
  const r = invoke(model(c, fn), vla(o, fn), m, args.map((a) => (a instanceof Sym ? a.name : toO(a))));
  if (!isByRef(r)) return fromO(r);
  for (const [i, v] of Object.entries(r.out)) {
    const s = args[Number(i)];
    if (s instanceof Sym && s !== T) assign(s, fromO(v), c.env ?? { vars: new Map(), parent: null }, c);
  }
  return fromO(r.ret);
};

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
def("VLAX-SAFEARRAY->LIST VLAX-MAKE-VARIANT VLAX-VARIANT-VALUE", (a) => a[0] ?? NIL);
def("VLAX-VARIANT-TYPE", (a) => (isList(a[0]) ? 8197 : typeof a[0] === "string" ? 8 : isNum(a[0]) ? (isReal(a[0]) ? 5 : 3) : 9));
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
  return typeof v === "number" ? real(v) : real(0);
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
  if (isNum(v)) return Sym.of(isReal(v) ? "REAL" : "INT");
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
  if (++c.hostCalls > HOST_CALLS) throw new LispError(`${fn}: 한 번 실행에서 파일 요청은 ${HOST_CALLS}번까지입니다`);
  return c.host.files;
};
const HOST_CALLS = 300;
const DATA_FILE = /\.(txt|csv|dat|log|dcl|las)$/i; // what the server lets a program write or delete
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
  const state = (): DialogState => ({ values: Object.fromEntries(d.values), lists: Object.fromEntries([...d.lists].map(([k, v]) => [k, [...v]])), disabled: [...d.disabled], ...(d.images?.size ? { images: Object.fromEntries([...d.images].map(([k, v]) => [k, [...v]])) } : {}) });
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
let reacting: [Reactor, string] | null = null; // vlr-current-reaction-name
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

export type ReactorEvent = { kind: "command"; name: string; command: string } | { kind: "object"; name: string; handle: string } | { kind: "other"; type: string; name: string; args: Val[] };

/** Runs the session's reactors for one screen event, as one AX-LISP run (same limits). */
export async function fireReactors(session: Session, event: ReactorEvent, host: Host = {}): Promise<Outcome | null> {
  const type = event.kind === "other" ? event.type : event.kind;
  const hits = session.reactors.filter((r) => r.active && r.type === type && (event.kind !== "object" || r.owners.includes(event.handle)));
  const calls = hits.flatMap((r) => r.events.filter(([e]) => e === event.name.toUpperCase()).map(([, fn]) => [r, fn] as const));
  if (!calls.length) return null;
  return guard(async () => {
    const c = newRun(host, session);
    const env: Env = { vars: new Map(), parent: null };
    for (const [r, fn] of calls) {
      const args: Val[] = event.kind === "command" ? [r, [event.command]] : event.kind === "object" ? [{ kind: "vla", obj: { t: "ent", handle: event.handle } }, r, NIL] : [r, event.args];
      reacting = [r, event.name];
      try {
        await call(fn, args, env, c, 1);
      } catch (e) {
        r.active = false; // W3 review M4: a failing reactor does not run again on every event
        throw e;
      } finally {
        reacting = null;
      }
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
  if (p.length < 2 || p.some((x) => !isNum(x))) throw new LispError(`trans: 점이 필요합니다 (${show(a[0])})`);
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
  const bits = isNum(a[0]) ? Math.trunc(nv(a[0])) : 0;
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

// --- W1 · W3 (plan §22.4): the rest of the AutoLISP core, files and settings -------------------

const nil = (v: Val | undefined) => v === undefined || !truthy(v);

// error modes and the database-modified stack (*push-error-using-command* ...): kept, nothing else to switch
def("*PUSH-ERROR-USING-COMMAND* *PUSH-ERROR-USING-STACK*", (_a, c) => {
  (c.session.errorModes ??= []).push("x");
  return T;
});
def("*POP-ERROR-MODE*", (_a, c) => {
  c.session.errorModes?.pop();
  return T;
});
def("ACAD-PUSH-DBMOD", (_a, c) => {
  (c.session.dbmod ??= []).push(c.host.model?.changes().created.length ?? 0);
  return T;
});
def("ACAD-POP-DBMOD", (_a, c) => (c.session.dbmod?.length ? (c.session.dbmod.pop(), T) : NIL));
def("ACDIMENABLEUPDATE INITCOMMANDVERSION INITDIA SETVIEW TABLET", () => NIL); // no setting of the screen to change
// memory: JavaScript manages it; the numbers keep old routines that print them working
def("ALLOC", (a) => (num(a[0], "alloc"), 514));
def("EXPAND", (a) => Math.max(0, Math.trunc(num(a[0], "expand"))));
def("MEM", (_a, c) => (log(c, "; AX-LISP: 메모리는 자동으로 관리됩니다"), NIL));

def("ATOMS-FAMILY", (a, c) => {
  const asText = num(a[0], "atoms-family") === 1;
  const names = [...BUILTINS.keys(), ...SPECIAL, ...[...c.globals.keys()].map((s) => s.name)];
  const known = new Set(names);
  const as = (n: string): Val => (asText ? n : Sym.of(n));
  if (a[1] !== undefined && truthy(a[1])) return list(a[1], "atoms-family").map((x) => {
    const n = (x instanceof Sym ? x.name : str(x, "atoms-family")).toUpperCase();
    return known.has(n) ? as(n) : NIL;
  });
  return cap([...known].map(as));
});
def("BOOLE", (a) => {
  const op = int32(a[0], "boole") & 15;
  const ints = a.slice(1).map((x) => int32(x, "boole"));
  return ints.slice(1).reduce((r, x) => {
    let v = 0;
    for (let bit = 0; bit < 32; bit++) {
      const i = (r >>> bit) & 1;
      const j = (x >>> bit) & 1;
      if ((op >> (3 - (i * 2 + j))) & 1) v |= 1 << bit; // op bits: 8 = 0/0, 4 = 0/1, 2 = 1/0, 1 = 1/1
    }
    return v | 0;
  }, ints[0] ?? 0);
});

// units (acad.unt's common entries): length, angle, area, volume, mass, time
const UNITS: Record<string, [string, number]> = {
  mm: ["L", 0.001], millimeter: ["L", 0.001], millimeters: ["L", 0.001], cm: ["L", 0.01], centimeter: ["L", 0.01], centimeters: ["L", 0.01],
  m: ["L", 1], meter: ["L", 1], meters: ["L", 1], km: ["L", 1000], kilometer: ["L", 1000], kilometers: ["L", 1000],
  in: ["L", 0.0254], inch: ["L", 0.0254], inches: ["L", 0.0254], ft: ["L", 0.3048], foot: ["L", 0.3048], feet: ["L", 0.3048],
  yd: ["L", 0.9144], yard: ["L", 0.9144], yards: ["L", 0.9144], mi: ["L", 1609.344], mile: ["L", 1609.344], miles: ["L", 1609.344],
  radian: ["A", 1], radians: ["A", 1], degree: ["A", Math.PI / 180], degrees: ["A", Math.PI / 180], grad: ["A", Math.PI / 200], grads: ["A", Math.PI / 200],
  sq_mm: ["L2", 1e-6], sq_cm: ["L2", 1e-4], sq_m: ["L2", 1], sq_km: ["L2", 1e6], sq_in: ["L2", 0.0254 ** 2], sq_ft: ["L2", 0.3048 ** 2], acre: ["L2", 4046.8564224], hectare: ["L2", 1e4],
  cu_mm: ["L3", 1e-9], cu_cm: ["L3", 1e-6], cu_m: ["L3", 1], liter: ["L3", 1e-3], liters: ["L3", 1e-3], cu_in: ["L3", 0.0254 ** 3], cu_ft: ["L3", 0.3048 ** 3], gallon: ["L3", 0.003785411784],
  g: ["M", 0.001], gram: ["M", 0.001], grams: ["M", 0.001], kg: ["M", 1], kilogram: ["M", 1], kilograms: ["M", 1], ton: ["M", 1000], tonne: ["M", 1000], lb: ["M", 0.45359237], pound: ["M", 0.45359237],
  second: ["T", 1], seconds: ["T", 1], minute: ["T", 60], minutes: ["T", 60], hour: ["T", 3600], hours: ["T", 3600], day: ["T", 86400], days: ["T", 86400],
};
def("CVUNIT", (a) => {
  const unit = (v: Val) => UNITS[str(v, "cvunit").trim().toLowerCase().replace(/\s+/g, "_").replace(/^square_/, "sq_").replace(/^cubic_/, "cu_")];
  const [from, to] = [unit(a[1]), unit(a[2])];
  if (!from || !to || from[0] !== to[0]) return NIL; // unknown or not the same kind of unit
  const k = from[1] / to[1];
  return isList(a[0]) ? a[0].map((x) => num(x, "cvunit") * k) : out(num(a[0], "cvunit") * k);
});

// defun-q: the function as its list (arguments body...)
const lambdaOf = (v: Val, fn: string): Lambda => {
  if (typeof v !== "object" || isList(v) || v instanceof Sym || v.kind !== "lambda") throw new LispError(`${fn}: defun-q로 정의한 함수가 필요합니다`);
  return v;
};
def("DEFUN-Q-LIST-REF", (a, c) => {
  const s = a[0];
  if (!(s instanceof Sym)) throw new LispError("defun-q-list-ref: 함수 이름(기호)이 필요합니다");
  const f = c.globals.get(s);
  return f === undefined ? NIL : (lambdaOf(f, "defun-q-list-ref").source ?? NIL);
});
def("DEFUN-Q-LIST-SET", (a, c) => {
  const s = a[0];
  if (!(s instanceof Sym) || BUILTINS.has(s.name) || SPECIAL.has(s.name)) throw new LispError("defun-q-list-set: 함수 이름(기호)이 필요합니다");
  const l = list(a[1], "defun-q-list-set");
  c.globals.set(s, lambda(s.name, l[0] ?? NIL, l.slice(1), [...l]));
  return s;
});
defEach("TRACE UNTRACE", (name) => (a, c) => {
  const set = (c.session.traced ??= new Set());
  for (const x of a) {
    // (trace fn) passes the function's value: its name is what calls are traced by
    const n = x instanceof Sym ? x.name : typeof x === "object" && !isList(x) && x.kind === "lambda" ? x.name.toUpperCase() : null;
    if (n && name === "TRACE") set.add(n);
    else if (n) set.delete(n);
  }
  return a.at(-1) ?? NIL;
});

// blackboard and namespaces: one screen = one document
def("VL-BB-SET", (a, c) => {
  const s = a[0];
  if (!(s instanceof Sym)) throw new LispError("vl-bb-set: 기호가 필요합니다");
  const bb = (c.session.bb ??= new Map());
  if (!bb.has(s.name) && bb.size >= 1000) throw new LispError("vl-bb-set: 값은 1,000개까지입니다");
  bb.set(s.name, a[1] ?? NIL);
  return a[1] ?? NIL;
});
def("VL-BB-REF", (a, c) => {
  const s = a[0];
  if (!(s instanceof Sym)) throw new LispError("vl-bb-ref: 기호가 필요합니다");
  return c.session.bb?.get(s.name) ?? NIL;
});
def("VL-PROPAGATE", (a) => {
  if (!(a[0] instanceof Sym)) throw new LispError("vl-propagate: 기호가 필요합니다");
  return NIL; // a single document: its value is already everywhere it can be
});
const exported = new Set<string>();
def("VL-ACAD-DEFUN", (a) => {
  if (!(a[0] instanceof Sym)) throw new LispError("vl-acad-defun: 기호가 필요합니다");
  exported.add(a[0].name);
  return a[0];
});
def("VL-ACAD-UNDEFUN", (a) => (a[0] instanceof Sym && exported.delete(a[0].name) ? T : NIL));
// VLX applications: none can be loaded here (vlisp-compile is not offered), so none is ever loaded
def("VL-LIST-LOADED-VLX VL-GET-RESOURCE VL-LIST-EXPORTED-FUNCTIONS VL-VLX-LOADED-P", () => NIL);
def("VL-UNLOAD-VLX", (a) => {
  throw new LispError(`vl-unload-vlx: 불러온 VLX가 아닙니다 (${show(a[0])})`);
});

// list and string utilities
def("VL-LIST*", (a) => {
  if (!a.length) throw new LispError("vl-list*: 인수가 필요합니다");
  let r: Val = a[a.length - 1]; // (vl-list* 1 2 '(3)) -> (1 2 3); (vl-list* 1 2) -> (1 . 2)
  for (let i = a.length - 2; i >= 0; i--) r = isList(r) ? cap([a[i], ...r]) : { kind: "pair", car: a[i], cdr: r };
  return r;
});
def("VL-MEMBER-IF-NOT", pred("vl-member-if-not", (xs, h) => (h.indexOf(false) < 0 ? NIL : xs.slice(h.indexOf(false)))));
def("VL-SORT-I", async (a, c) => {
  const xs = list(a[0], "vl-sort-i");
  const less = async (x: number, y: number) => truthy(await call(a[1], [xs[x], xs[y]], { vars: new Map(), parent: null }, c, c.depth + 1));
  const sort = async (is: number[]): Promise<number[]> => {
    if (is.length < 2) return is;
    const [l, r] = [await sort(is.slice(0, is.length >> 1)), await sort(is.slice(is.length >> 1))];
    const o: number[] = [];
    while (l.length && r.length) o.push((await less(r[0], l[0])) ? r.shift()! : l.shift()!);
    return [...o, ...l, ...r];
  };
  return sort(xs.map((_, i) => i));
});
def("VL-STRING-ELT", (a) => {
  const s = str(a[0], "vl-string-elt");
  const i = num(a[1], "vl-string-elt");
  if (i < 0 || i >= s.length) throw new LispError("vl-string-elt: 위치가 문자열 밖입니다");
  return s.charCodeAt(i);
});
def("VL-STRING-MISMATCH", (a) => {
  const fold = a[4] !== undefined && truthy(a[4]);
  const [s1, s2] = [str(a[0], "vl-string-mismatch"), str(a[1], "vl-string-mismatch")].map((x) => (fold ? x.toUpperCase() : x));
  const p1 = a[2] !== undefined ? num(a[2], "vl-string-mismatch") : 0;
  const p2 = a[3] !== undefined ? num(a[3], "vl-string-mismatch") : 0;
  let n = 0;
  while (p1 + n < s1.length && p2 + n < s2.length && s1[p1 + n] === s2[p2 + n]) n++;
  return n;
});
def("SNVALID", (a) => {
  const n = str(a[0], "snvalid");
  const allowPipe = a[1] !== undefined && num(a[1], "snvalid") === 1;
  if (!n || n.length > 255 || /^\s|\s$/.test(n)) return NIL;
  return bool(!/[<>/\\":;?*,=`\x00-\x1f]/.test(n) && (allowPipe || !n.includes("|")));
});
def("GETCNAME", (a) => {
  const n = str(a[0], "getcname").trim();
  if (!/^[_.]*[A-Za-z][\w-]*$/.test(n)) return NIL;
  return n.startsWith("_") ? n.replace(/^[_.]+/, "").toUpperCase() : `_${n.replace(/^[.]+/, "").toUpperCase()}`; // English and local names are the same here
});
def("TEXTBOX", (a) => {
  const l = list(a[0], "textbox");
  const get = (code: number) => (l.find((x) => isPair(x) && x.car === code) as Pair | undefined)?.cdr;
  const text = String(get(1) ?? "");
  const h40 = get(40);
  const w41 = get(41);
  const h = isNum(h40) ? nv(h40) : 2.5;
  const w = isNum(w41) ? nv(w41) : 1;
  const lines = text.split(/\\P|\n/);
  const longest = Math.max(...lines.map((x) => x.length));
  return [[0, -0.2 * h, 0], [longest * 0.75 * h * w, h * (1 + 1.6 * (lines.length - 1)), 0]]; // ponytail: average glyph width, not the font's metrics
});
def("SSNAMEX", (a) => {
  const s = ss(a[0], "ssnamex");
  const one = (x: Sel): Val => [0, { kind: "ename", sel: x }, 0];
  if (a[1] !== undefined) {
    const x = s.items[num(a[1], "ssnamex")];
    return x ? [one(x)] : NIL;
  }
  return s.items.map(one);
});
def("VPORTS", () => [[1, [0, 0], [1, 1]]]); // one model space viewport
def("XDSIZE", (a) => {
  // bytes AutoCAD counts for an extended data list ((-3 ("APP" (1000 . "x") ...)))
  let n = 0;
  const walk = (v: Val) => {
    if (isPair(v) && typeof v.car === "number") {
      const code = v.car;
      n += typeof v.cdr === "string" ? v.cdr.length + 3 : code >= 1010 && code <= 1013 ? 26 : code === 1071 ? 6 : code === 1070 ? 4 : 10;
    } else if (typeof v === "string") n += v.length + 3;
    else if (isList(v)) v.forEach(walk);
  };
  const l = list(a[0], "xdsize");
  const xd = l.find((x) => isList(x) && x[0] === -3) as Val[] | undefined;
  (xd ? xd.slice(1) : l).forEach(walk);
  return n;
});

const aci = (n: number) => Math.min(256, Math.max(0, Math.trunc(Math.abs(n)))); // -1 = highlighted: same colour
// temporary graphics and input on the drawing (grread: a click or a typed point)
def("GRCLEAR", (_a, c) => (c.host.draw?.([]), NIL));
def("GRTEXT", () => NIL);
def("GRDRAW", (a, c) => {
  const [from, to] = [pt(a[0], "grdraw"), pt(a[1], "grdraw")];
  c.host.draw?.([{ from, to, color: aci(num(a[2], "grdraw")) }]);
  return NIL;
});
def("GRVECS", (a, c) => {
  const l = list(a[0], "grvecs");
  const v: { from: Pt; to: Pt; color: number }[] = [];
  let color = 7;
  for (let i = 0; i < l.length; i++) {
    const li = l[i];
    if (isNum(li)) color = aci(nv(li));
    else if (i + 1 < l.length) v.push({ from: pt(l[i], "grvecs"), to: pt(l[++i], "grvecs"), color });
  }
  c.host.draw?.(v.slice(0, 10_000));
  return NIL;
});
def("GRREAD", async (_a, c) => {
  if (!c.host.input) throw new LispError("이 화면에서는 입력 함수(grread)를 쓸 수 없습니다");
  const p = await waiting(c, () => c.host.input!("point", "", undefined));
  return p === null || !Array.isArray(p) ? NIL : [3, [p[0], p[1]]];
});

// colours: the colour number (1-255) or "R,G,B" typed at the prompt
const askColor = async (c: Run, fn: string, prompt: string, trueColor: boolean): Promise<Val> => {
  if (!c.host.input) throw new LispError(`이 화면에서는 ${fn}를 쓸 수 없습니다`);
  const v = await waiting(c, () => c.host.input!("string", prompt, undefined));
  if (v === null) return NIL;
  const t = String(v).trim();
  const rgb = /^(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})$/.exec(t);
  if (rgb && trueColor) {
    const [r, g, b] = rgb.slice(1).map(Number);
    if ([r, g, b].some((x) => x > 255)) return NIL;
    return [pair(62, 7), pair(420, (r << 16) | (g << 8) | b)];
  }
  const n = /^\d{1,3}$/.test(t) ? Number(t) : /^bylayer$/i.test(t) ? 256 : /^byblock$/i.test(t) ? 0 : NaN;
  if (!(n >= 0 && n <= 256)) return NIL;
  return trueColor ? [pair(62, n)] : n;
};
def("ACAD_COLORDLG", (a, c) => askColor(c, "acad_colordlg", "색 번호 (1–255):", false));
def("ACAD_TRUECOLORDLG ACAD_TRUECOLORCLI", (a, c) => askColor(c, "acad_truecolordlg", typeof a[2] === "string" ? a[2] : "색 (번호 또는 R,G,B):", true));

// DCL image tiles: what to draw is kept with the dialog; the screen paints it
defEach("DIMX_TILE DIMY_TILE", (name) => {
  const [attr, fallback] = name === "DIMX_TILE" ? ["width", 160] : ["height", 80];
  return (a, c) => {
    const key = str(a[0], name.toLowerCase());
    let found: Tile | undefined;
    const walk = (t: Tile) => {
      if (t.key === key) found = t;
      t.children.forEach(walk);
    };
    walk(dlg(c, name.toLowerCase()).tile);
    const v = Number(found?.attrs[attr]);
    return Number.isFinite(v) && v > 0 ? Math.round(v * (attr === "width" ? 8 : 16)) : fallback; // character cells -> pixels
  };
});
def("START_IMAGE", (a, c) => {
  const d = dlg(c, "start_image");
  d.image = str(a[0], "start_image");
  const images = (d.images ??= new Map());
  if (!images.has(d.image) && images.size >= 50) throw new LispError("start_image: 그림 타일은 50개까지입니다");
  images.set(d.image, []);
  return d.image;
});
const imageOps = (c: Run, fn: string): ImageOp[] => {
  const d = dlg(c, fn);
  if (!d.image) throw new LispError(`${fn}: 먼저 start_image를 부르세요`);
  const ops = d.images!.get(d.image)!;
  if (ops.length >= 5000) throw new LispError(`${fn}: 그림 요소는 5,000개까지입니다`);
  return ops;
};
def("FILL_IMAGE", (a, c) => {
  const [x, y, w, h, color] = a.slice(0, 5).map((v) => num(v, "fill_image"));
  imageOps(c, "fill_image").push({ op: "fill", x, y, w, h, color });
  return a[4];
});
def("VECTOR_IMAGE", (a, c) => {
  const [x1, y1, x2, y2, color] = a.slice(0, 5).map((v) => num(v, "vector_image"));
  imageOps(c, "vector_image").push({ op: "vector", x1, y1, x2, y2, color });
  return a[4];
});
def("SLIDE_IMAGE", (a, c) => {
  const [x, y, w, h] = a.slice(0, 4).map((v) => num(v, "slide_image"));
  imageOps(c, "slide_image").push({ op: "slide", x, y, w, h, name: fileName(a[4], "slide_image") });
  return a[4];
});
def("END_IMAGE", (_a, c) => {
  dlg(c, "end_image").image = undefined;
  return NIL;
});
def("CLIENT_DATA_TILE", (a, c) => {
  (dlg(c, "client_data_tile").clientData ??= new Map()).set(str(a[0], "client_data_tile"), str(a[1], "client_data_tile"));
  return NIL;
});

// safearrays and variants: a safearray is a list here (one dimension, lower bound 0)
const SAFE = new WeakMap<Val[], { type: number; lo: number[]; hi: number[] }>();
def("VLAX-MAKE-SAFEARRAY", (a) => {
  const type = num(a[0], "vlax-make-safearray");
  const dims = a.slice(1).map((b) => (isPair(b) ? [num(b.car, "vlax-make-safearray"), num(b.cdr, "vlax-make-safearray")] : [0, -1]));
  if (dims.length > 3 || dims.some(([lo, hi]) => !Number.isInteger(lo) || !Number.isInteger(hi))) throw new LispError("vlax-make-safearray: 정수 경계, 3차원까지입니다");
  const size = dims.reduce((n, [lo, hi]) => n * Math.max(0, hi - lo + 1), 1);
  if (size > LIMITS.list) throw new LispError(`vlax-make-safearray: 원소는 ${LIMITS.list.toLocaleString()}개까지입니다`); // checked before anything is made
  const arr = cap(Array.from({ length: dims.length ? size : 0 }, (): Val => (type === 8 ? "" : 0)));
  SAFE.set(arr, { type, lo: dims.map((d) => d[0]), hi: dims.map((d) => d[1]) });
  return arr;
});
const safe = (v: Val, fn: string) => {
  const l = list(v, fn);
  return { l, info: SAFE.get(l) ?? { type: typeof l[0] === "string" ? 8 : 5, lo: [0], hi: [l.length - 1] } };
};
const cell = (info: { lo: number[]; hi: number[] }, idx: number[], fn: string) => {
  if (idx.length !== info.lo.length) throw new LispError(`${fn}: 차원 수가 맞지 않습니다`);
  let k = 0;
  for (let d = 0; d < idx.length; d++) {
    if (!Number.isInteger(idx[d]) || idx[d] < info.lo[d] || idx[d] > info.hi[d]) throw new LispError(`${fn}: 범위 밖입니다`);
    k = k * (info.hi[d] - info.lo[d] + 1) + (idx[d] - info.lo[d]);
  }
  return k;
};
def("VLAX-SAFEARRAY-GET-DIM", (a) => safe(a[0], "vlax-safearray-get-dim").info.lo.length);
def("VLAX-SAFEARRAY-GET-L-BOUND", (a) => safe(a[0], "vlax-safearray-get-l-bound").info.lo[num(a[1], "vlax-safearray-get-l-bound") - 1] ?? NIL);
def("VLAX-SAFEARRAY-GET-U-BOUND", (a) => safe(a[0], "vlax-safearray-get-u-bound").info.hi[num(a[1], "vlax-safearray-get-u-bound") - 1] ?? NIL);
def("VLAX-SAFEARRAY-TYPE", (a) => safe(a[0], "vlax-safearray-type").info.type);
def("VLAX-SAFEARRAY-GET-ELEMENT", (a) => {
  const { l, info } = safe(a[0], "vlax-safearray-get-element");
  return l[cell(info, a.slice(1).map((x) => num(x, "vlax-safearray-get-element")), "vlax-safearray-get-element")] ?? NIL;
});
def("VLAX-SAFEARRAY-PUT-ELEMENT", (a) => {
  const { l, info } = safe(a[0], "vlax-safearray-put-element");
  l[cell(info, a.slice(1, -1).map((x) => num(x, "vlax-safearray-put-element")), "vlax-safearray-put-element")] = a[a.length - 1];
  return a[a.length - 1];
});
def("VLAX-SAFEARRAY-FILL", (a) => {
  const { l } = safe(a[0], "vlax-safearray-fill");
  const items = list(a[1], "vlax-safearray-fill").flat() as Val[];
  for (let i = 0; i < Math.min(l.length, items.length); i++) l[i] = items[i];
  return l;
});
def("VLAX-TMATRIX", (a) => {
  const rows = list(a[0], "vlax-tmatrix").map((r) => list(r, "vlax-tmatrix").map((x) => num(x, "vlax-tmatrix")));
  if (rows.length !== 4 || rows.some((r) => r.length !== 4)) throw new LispError("vlax-tmatrix: 4×4 행렬이 필요합니다");
  return rows;
});
// variant types: 0 empty, 1 null, 2 integer, 3 long, 4 single, 5 double, 8 string, 11 boolean, 8192 + type = array
def("VLAX-VARIANT-CHANGE-TYPE", (a) => {
  const v = a[0];
  const t = num(a[1], "vlax-variant-change-type");
  if (t === 8) return typeof v === "string" ? v : show(v);
  if (t === 2 || t === 3) return isNum(v) ? Math.round(nv(v)) : typeof v === "string" && NUMBER.test(v.trim()) ? Math.round(Number(v)) : NIL;
  if (t === 4 || t === 5) return isNum(v) ? real(nv(v)) : typeof v === "string" && NUMBER.test(v.trim()) ? real(Number(v)) : NIL;
  if (t === 11) return truthy(v) && v !== 0 && v !== FALSE ? TRUE : FALSE;
  if (t === 0 || t === 1) return NIL;
  return v ?? NIL;
});
def("VLAX-MAP-COLLECTION", async (a, c) => {
  const coll = vla(a[0], "vlax-map-collection");
  const m = model(c, "vlax-map-collection");
  const items: Val[] = (itemsOf(m, coll) ?? (() => { throw new LispError("vlax-map-collection: 컬렉션이 필요합니다"); })()).map((o) => fromO(o));
  for (const it of items) await call(a[1], [it], { vars: new Map(), parent: null }, c, c.depth + 1);
  return a[0];
});
def("VLAX-READ-ENABLED-P", (a, c) => {
  const o = vla(a[0], "vlax-read-enabled-p");
  return bool(o.t !== "ent" || !!model(c, "vlax-read-enabled-p").get(o.handle));
});
def("VLAX-WRITE-ENABLED-P", (a, c) => {
  const o = vla(a[0], "vlax-write-enabled-p");
  const m = model(c, "vlax-write-enabled-p");
  const e = o.t === "ent" ? m.get(o.handle) : undefined;
  return bool(o.t !== "ent" || (!!e && !m.layer(e.layer)?.locked));
});
def("VLAX-REMOVE-CMD", () => NIL); // vlax-add-cmd is not offered: no command to remove

// curves: the rest of vlax-curve-* (parameters as AutoCAD uses them, see curveInfo)
/** The parameter of the curve point nearest to p (exact for lines, arcs and polyline segments). */
const nearestParam = (e: Ent, i: NonNullable<ReturnType<typeof curveInfo>>, p: Pt): number => {
  const g = e.geom;
  const onSeg = (a: Pt, b: Pt) => {
    const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
    const l2 = dx * dx + dy * dy;
    return l2 ? Math.min(1, Math.max(0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
  };
  if (g.type === "LINE") return onSeg(g.start, g.end) * i.end;
  if (g.type === "CIRCLE" || g.type === "ARC") {
    let t = Math.atan2(p[1] - g.center[1], p[0] - g.center[0]);
    while (t < i.start) t += 2 * Math.PI;
    while (t > i.start + 2 * Math.PI) t -= 2 * Math.PI;
    if (t <= i.end) return t;
    const d = (x: number) => Math.hypot(i.at(x)[0] - p[0], i.at(x)[1] - p[1]);
    return d(i.start) <= d(i.end) ? i.start : i.end; // past the arc's ends: the nearer end
  }
  let best = 0;
  let bd = Infinity;
  for (let k = 0; k < i.end; k++) {
    const t = k + onSeg(i.at(k), i.at(k + 1));
    const q = i.at(t);
    const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (d < bd) [bd, best] = [d, t];
  }
  return best;
};
const paramAtDist = (i: NonNullable<ReturnType<typeof curveInfo>>, d: number): number | null => {
  if (d < 0 || d > i.dist(i.end) + 1e-9) return null;
  let [lo, hi] = [i.start, i.end];
  for (let it = 0; it < 80; it++) {
    const m = (lo + hi) / 2;
    if (i.dist(m) < d) lo = m;
    else hi = m;
  }
  return (lo + hi) / 2;
};
const deriv = (i: NonNullable<ReturnType<typeof curveInfo>>, t: number, order: 1 | 2): Val => {
  const h = Math.max(1e-6, (i.end - i.start) * 1e-6);
  const f = (x: number) => i.at(Math.min(i.end, Math.max(i.start, x)));
  const [a, b, m] = [f(t - h), f(t + h), f(t)];
  return order === 1 ? [(b[0] - a[0]) / (2 * h), (b[1] - a[1]) / (2 * h), 0] : [(b[0] - 2 * m[0] + a[0]) / (h * h), (b[1] - 2 * m[1] + a[1]) / (h * h), 0];
};
def("VLAX-CURVE-GETCLOSESTPOINTTO VLAX-CURVE-GETCLOSESTPOINTTOPROJECTION", (a, c) => {
  const i = info(c, a[0], "vlax-curve-getClosestPointTo");
  const p = pt(a[1], "vlax-curve-getClosestPointTo");
  return [...i.at(nearestParam(curve(c, a[0], "vlax-curve"), i, p)), 0];
});
def("VLAX-CURVE-GETPARAMATPOINT", (a, c) => {
  const i = info(c, a[0], "vlax-curve-getParamAtPoint");
  const p = pt(a[1], "vlax-curve-getParamAtPoint");
  const t = nearestParam(curve(c, a[0], "vlax-curve"), i, p);
  const q = i.at(t);
  return Math.hypot(q[0] - p[0], q[1] - p[1]) < 1e-6 ? t : NIL; // AutoCAD: nil off the curve
});
def("VLAX-CURVE-GETDISTATPOINT", (a, c) => {
  const i = info(c, a[0], "vlax-curve-getDistAtPoint");
  const p = pt(a[1], "vlax-curve-getDistAtPoint");
  const t = nearestParam(curve(c, a[0], "vlax-curve"), i, p);
  const q = i.at(t);
  return Math.hypot(q[0] - p[0], q[1] - p[1]) < 1e-6 ? i.dist(t) : NIL;
});
def("VLAX-CURVE-GETPARAMATDIST", (a, c) => paramAtDist(info(c, a[0], "vlax-curve-getParamAtDist"), num(a[1], "vlax-curve-getParamAtDist")) ?? NIL);
def("VLAX-CURVE-GETPOINTATDIST", (a, c) => {
  const i = info(c, a[0], "vlax-curve-getPointAtDist");
  const t = paramAtDist(i, num(a[1], "vlax-curve-getPointAtDist"));
  return t === null ? NIL : [...i.at(t), 0];
});
def("VLAX-CURVE-GETFIRSTDERIV", (a, c) => deriv(info(c, a[0], "vlax-curve-getFirstDeriv"), num(a[1], "vlax-curve-getFirstDeriv"), 1));
def("VLAX-CURVE-GETSECONDDERIV", (a, c) => deriv(info(c, a[0], "vlax-curve-getSecondDeriv"), num(a[1], "vlax-curve-getSecondDeriv"), 2));
def("VLAX-CURVE-ISPERIODIC", (a, c) => bool(curve(c, a[0], "vlax-curve-isPeriodic").geom.type === "CIRCLE"));
def("VLAX-CURVE-ISPLANAR", (a, c) => (curve(c, a[0], "vlax-curve-isPlanar"), T)); // every curve here is 2D

// reactors: every AutoCAD reactor type; the screen fires the events it has (commands, objects, ...)
const REACTOR_TYPES: Record<string, string> = {
  "VLR-ACDB-REACTOR": "acdb", "VLR-DOCMANAGER-REACTOR": "docmanager", "VLR-DWG-REACTOR": "dwg", "VLR-DXF-REACTOR": "dxf", "VLR-INSERT-REACTOR": "insert", "VLR-LINKER-REACTOR": "linker", "VLR-LISP-REACTOR": "lisp",
  "VLR-MISCELLANEOUS-REACTOR": "miscellaneous", "VLR-MOUSE-REACTOR": "mouse", "VLR-SYSVAR-REACTOR": "sysvar", "VLR-TOOLBAR-REACTOR": "toolbar", "VLR-UNDO-REACTOR": "undo", "VLR-WBLOCK-REACTOR": "wblock", "VLR-WINDOW-REACTOR": "window", "VLR-XREF-REACTOR": "xref", "VLR-DEEPCLONE-REACTOR": "deepclone",
};
defEach(
  "VLR-ACDB-REACTOR VLR-DOCMANAGER-REACTOR VLR-DWG-REACTOR VLR-DXF-REACTOR VLR-INSERT-REACTOR VLR-LINKER-REACTOR VLR-LISP-REACTOR VLR-MISCELLANEOUS-REACTOR VLR-MOUSE-REACTOR VLR-SYSVAR-REACTOR VLR-TOOLBAR-REACTOR VLR-UNDO-REACTOR VLR-WBLOCK-REACTOR VLR-WINDOW-REACTOR VLR-XREF-REACTOR VLR-DEEPCLONE-REACTOR",
  (name) => (a, c) => register(c, { kind: "reactor", type: REACTOR_TYPES[name], data: a[0] ?? NIL, owners: [], events: callbacks(a[1], name.toLowerCase()), active: true }),
);
const REACTIONS: Record<string, string[]> = {
  command: ["COMMANDWILLSTART", "COMMANDENDED", "COMMANDCANCELLED", "COMMANDFAILED", "UNKNOWNCOMMAND", "LISPWILLSTART", "LISPENDED", "LISPCANCELLED"],
  object: ["CANCELLED", "COPIED", "ERASED", "UNERASED", "GOODBYE", "OPENEDFORMODIFY", "MODIFIED", "SUBOBJMODIFIED", "MODIFYUNDONE", "MODIFIEDXDATA", "UNAPPENDED", "REAPPENDED", "OBJECTCLOSED"],
  acdb: ["OBJECTAPPENDED", "OBJECTUNAPPENDED", "OBJECTREAPPENDED", "OBJECTOPENEDFORMODIFY", "OBJECTMODIFIED", "OBJECTERASED", "OBJECTUNERASED"],
  lisp: ["LISPWILLSTART", "LISPENDED", "LISPCANCELLED"],
  sysvar: ["SYSVARWILLCHANGE", "SYSVARCHANGED"],
  dwg: ["BEGINCLOSE", "DATABASECONSTRUCTED", "DATABASETOBEDESTROYED", "BEGINDWGOPEN", "ENDDWGOPEN", "DWGFILEOPENED", "BEGINSAVE", "SAVECOMPLETE"],
  docmanager: ["DOCUMENTCREATED", "DOCUMENTTOBEDESTROYED", "DOCUMENTLOCKMODEWILLCHANGE", "DOCUMENTLOCKMODECHANGEVETOED", "DOCUMENTLOCKMODECHANGED", "DOCUMENTBECAMECURRENT", "DOCUMENTTOBEACTIVATED", "DOCUMENTTOBEDEACTIVATED"],
  undo: ["UNDOSUBCOMMANDAUTO", "UNDOSUBCOMMANDCONTROL", "UNDOSUBCOMMANDBEGIN", "UNDOSUBCOMMANDEND", "UNDOSUBCOMMANDMARK", "UNDOSUBCOMMANDBACK", "UNDOSUBCOMMANDNUMBER"],
  mouse: ["BEGINDOUBLECLICK", "BEGINRIGHTCLICK"],
  window: ["DOCFRAMEMOVEDORRESIZED", "MAINFRAMEMOVEDORRESIZED"],
  miscellaneous: ["PICKFIRSTMODIFIED", "LAYOUTSWITCHED"],
  insert: ["BEGININSERT", "BEGININSERTM", "OTHERINSERT", "ENDINSERT", "ABORTINSERT"],
  wblock: ["WBLOCKNOTICE", "BEGINWBLOCKPT", "BEGINWBLOCKID", "BEGINWBLOCK", "OTHERWBLOCK", "ABORTWBLOCK", "ENDWBLOCK", "BEGINWBLOCKOBJECTS"],
  xref: ["BEGINATTACH", "OTHERATTACH", "ABORTATTACH", "ENDATTACH", "REDIRECTED", "COMANDEERED", "BEGINRESTORE", "ABORTRESTORE", "ENDRESTORE", "XREFSUBCOMMANDBINDITEM", "XREFSUBCOMMANDATTACHITEM", "XREFSUBCOMMANDOVERLAYITEM", "XREFSUBCOMMANDDETACHITEM", "XREFSUBCOMMANDPATHITEM", "XREFSUBCOMMANDRELOADITEM", "XREFSUBCOMMANDUNLOADITEM"],
  dxf: ["BEGINDXFIN", "ABORTDXFIN", "DXFINCOMPLETE", "BEGINDXFOUT", "ABORTDXFOUT", "DXFOUTCOMPLETE"],
  deepclone: ["BEGINDEEPCLONE", "BEGINDEEPCLONETRANSLATION", "ABORTDEEPCLONE", "ENDDEEPCLONE"],
  linker: ["RXAPPLOADED", "RXAPPUNLOADED"],
  toolbar: ["TOOLBARBITMAPSIZEWILLCHANGE", "TOOLBARBITMAPSIZECHANGED"],
};
const TYPE_NAME = (t: string) => Sym.of(`:VLR-${t.toUpperCase()}-REACTOR`);
def("VLR-TYPES", () => Object.keys(REACTIONS).map(TYPE_NAME));
def("VLR-TYPE", (a) => TYPE_NAME(reactor(a[0], "vlr-type").type));
def("VLR-REACTION-NAMES VLR-REACTION-NAME", (a) => {
  const t = a[0] instanceof Sym ? /^:VLR-(.+)-REACTOR$/.exec(a[0].name)?.[1]?.toLowerCase() : undefined;
  return (REACTIONS[t ?? ""] ?? []).map((n) => Sym.of(`:VLR-${n}`));
});
def("VLR-REACTIONS", (a) => reactor(a[0], "vlr-reactions").events.map(([e, f]) => ({ kind: "pair", car: Sym.of(e), cdr: f }) as Pair));
def("VLR-REACTION-SET", (a) => {
  const r = reactor(a[0], "vlr-reaction-set");
  if (!(a[1] instanceof Sym)) throw new LispError("vlr-reaction-set: 이벤트 기호가 필요합니다");
  const name = a[1].name;
  r.events = [...r.events.filter(([e]) => e !== name), [name, a[2]]];
  return a[2];
});
def("VLR-DATA-SET", (a) => {
  reactor(a[0], "vlr-data-set").data = a[1] ?? NIL;
  return a[1] ?? NIL;
});
def("VLR-OWNER-ADD", (a) => {
  const r = reactor(a[0], "vlr-owner-add");
  const o = vla(a[1], "vlr-owner-add");
  if (o.t === "ent" && !r.owners.includes(o.handle)) r.owners.push(o.handle);
  return a[1];
});
def("VLR-OWNER-REMOVE", (a) => {
  const r = reactor(a[0], "vlr-owner-remove");
  const o = vla(a[1], "vlr-owner-remove");
  r.owners = r.owners.filter((h) => o.t !== "ent" || h !== o.handle);
  return a[1];
});
def("VLR-NOTIFICATION", () => Sym.of(":ACTIVE-DOCUMENT-ONLY"));
def("VLR-SET-NOTIFICATION", (a) => (reactor(a[0], "vlr-set-notification"), a[0]));
def("VLR-PERS", (a) => {
  reactor(a[0], "vlr-pers").persistent = true; // kept for the screen's session (a drawing reopened starts afresh)
  return a[0];
});
def("VLR-PERS-RELEASE", (a) => {
  reactor(a[0], "vlr-pers-release").persistent = false;
  return a[0];
});
def("VLR-PERS-P", (a) => (reactor(a[0], "vlr-pers-p").persistent ? a[0] : NIL));
def("VLR-PERS-LIST", (_a, c) => c.session.reactors.filter((r) => r.persistent && r.active));
def("VLR-CURRENT-REACTION-NAME", () => (reacting ? Sym.of(reacting[1].startsWith(":") ? reacting[1] : `:VLR-${reacting[1]}`) : NIL));
def("VLR-BEEP-REACTION", () => NIL);
def("VLR-TRACE-REACTION", (a, c) => {
  log(c, `; reaction: ${a.map(show).join(" ")}`);
  return NIL;
});

// W3: settings — getenv/setenv (environment), getcfg/setcfg ("AppData/..."): this PC's settings
const settings = (c: Run) => ({
  get: (k: string) => (c.host.settings ? c.host.settings.get(k) : (c.session.settings?.get(k) ?? null)),
  set: (k: string, v: string) => {
    if (k.length > 256 || v.length > 4096) throw new LispError("설정 이름은 256자, 값은 4,096자까지입니다");
    if (++c.hostCalls > HOST_CALLS) throw new LispError(`한 번 실행에서 설정 요청은 ${HOST_CALLS}번까지입니다`);
    try {
      if (c.host.settings) c.host.settings.set(k, v);
      else {
        const m = (c.session.settings ??= new Map());
        if (!m.has(k) && m.size >= 100) throw new Error("설정은 100개까지입니다");
        m.set(k, v);
      }
    } catch (e) {
      throw new LispError(e instanceof Error ? e.message : String(e));
    }
  },
});
def("GETENV", (a, c) => settings(c).get(`env:${str(a[0], "getenv").toUpperCase()}`) ?? NIL);
def("SETENV", (a, c) => {
  settings(c).set(`env:${str(a[0], "setenv").toUpperCase()}`, str(a[1], "setenv"));
  return a[1];
});
const cfgKey = (v: Val, fn: string) => {
  const k = str(v, fn);
  if (!/^AppData\//i.test(k)) throw new LispError(`${fn}: "AppData/..." 이름만 쓸 수 있습니다`);
  return `cfg:${k.toUpperCase()}`;
};
def("GETCFG", (a, c) => settings(c).get(cfgKey(a[0], "getcfg")) ?? "");
def("SETCFG", (a, c) => {
  settings(c).set(cfgKey(a[0], "setcfg"), str(a[1], "setcfg"));
  return a[1];
});

// W3: files — the PC LISP library is the one folder; a program writes data files only (server rule)
const listing = async (c: Run, fn: string) => {
  if (c.listCache) return c.listCache; // one listing per run; writes of this run update it
  const f = files(c, fn);
  if (!f.list) throw new LispError(`이 화면에서는 파일 목록(${fn})을 쓸 수 없습니다`);
  c.listCache = await waiting(c, () => f.list!());
  return c.listCache;
};
const isLibraryDir = (v: Val) => v === undefined || nil(v) || /^(\.|\.\/|)$/.test(str(v, "dir").trim());
def("FINDTRUSTEDFILE", async (a, c) => {
  const name = fileName(a[0], "findtrustedfile");
  return (await files(c, "findtrustedfile").read(name)) === null ? NIL : name;
});
def("VL-FILE-DIRECTORY-P", (a) => bool(isLibraryDir(a[0])));
def("VL-MKDIR ACET-FILE-MKDIR", () => NIL); // the library has no folders
def("VL-DIRECTORY-FILES", async (a, c) => {
  if (!isLibraryDir(a[0])) return NIL;
  const pattern = a[1] !== undefined && !nil(a[1]) ? str(a[1], "vl-directory-files") : "*.*";
  const what = a[2] !== undefined ? num(a[2], "vl-directory-files") : 0;
  if (what === -1) return [".", ".."]; // directories only: the library folder itself
  const names = (await listing(c, "vl-directory-files")).map((f) => f.name).filter((n) => wcmatch(n.toUpperCase(), pattern.toUpperCase().replace(/\*\.\*/g, "*")));
  return names.length ? names : NIL;
});
def("ACET-FILE-DIR", async (a, c) => {
  const pattern = a[0] !== undefined ? str(a[0], "acet-file-dir").replace(/^.*[\\/]/, "") : "*";
  const names = (await listing(c, "acet-file-dir")).map((f) => f.name).filter((n) => wcmatch(n.toUpperCase(), pattern.toUpperCase().replace(/\*\.\*/g, "*")));
  return names.length ? names : NIL;
});
const sizeOf = (text: string) => new TextEncoder().encode(text).length;
def("VL-FILE-SIZE", async (a, c) => {
  if (isLibraryDir(a[0])) return 0;
  const t = await files(c, "vl-file-size").read(fileName(a[0], "vl-file-size"));
  return t === null ? NIL : sizeOf(t);
});
def("VL-FILE-SYSTIME", async (a, c) => {
  const n = fileName(a[0], "vl-file-systime").toLowerCase();
  const f = (await listing(c, "vl-file-systime")).find((x) => x.name.toLowerCase() === n);
  if (!f?.modified) return NIL;
  const d = new Date(f.modified);
  if (Number.isNaN(d.getTime())) return NIL;
  return [d.getFullYear(), d.getMonth() + 1, d.getDay(), d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds(), d.getMilliseconds()];
});
const copyFile = async (c: Run, fn: string, from: Val, to: Val, append: boolean): Promise<number | null> => {
  const [src, dst] = [fileName(from, fn), fileName(to, fn)];
  const text = await files(c, fn).read(src);
  if (text === null) return null;
  const old = await files(c, fn).read(dst);
  if (old !== null && !append) return null; // vl-file-copy does not overwrite
  const body = (append && old !== null ? old : "") + text;
  if (body.length > FILE_LIMITS.bytes) throw new LispError(`${fn}: 파일은 64 KB까지입니다`);
  try {
    await waiting(c, () => files(c, fn).write(dst, body));
  } catch (e) {
    throw new LispError(`${fn}: ${e instanceof Error ? e.message : String(e)}`);
  }
  c.listCache = undefined;
  return sizeOf(body);
};
def("VL-FILE-COPY", async (a, c) => (await copyFile(c, "vl-file-copy", a[0], a[1], a[2] !== undefined && truthy(a[2]))) ?? NIL);
def("ACET-FILE-COPY", async (a, c) => ((await copyFile(c, "acet-file-copy", a[0], a[1], false)) === null ? NIL : T));
const removeFile = async (c: Run, fn: string, v: Val): Promise<boolean> => {
  const f = files(c, fn);
  if (!f.remove) throw new LispError(`이 화면에서는 파일 삭제(${fn})를 쓸 수 없습니다`);
  const name = fileName(v, fn);
  try {
    c.listCache = undefined;
    return await waiting(c, () => f.remove!(name));
  } catch (e) {
    throw new LispError(`${fn}: ${e instanceof Error ? e.message : String(e)}`);
  }
};
def("VL-FILE-DELETE", async (a, c) => bool(await removeFile(c, "vl-file-delete", a[0])));
def("ACET-FILE-REMOVE", async (a, c) => bool(await removeFile(c, "acet-file-remove", a[0])));
const moveFile = async (c: Run, fn: string, from: Val, to: Val) => {
  if (!DATA_FILE.test(fileName(from, fn))) throw new LispError(`${fn}: 데이터 파일(.txt·.csv·.dat·.log·.dcl·.las)만 옮길 수 있습니다`); // never half-moved
  if ((await files(c, fn).read(fileName(to, fn))) !== null) return false; // the new name is taken
  if ((await copyFile(c, fn, from, to, false)) === null) return false;
  return removeFile(c, fn, from);
};
def("VL-FILE-RENAME", async (a, c) => bool(await moveFile(c, "vl-file-rename", a[0], a[1])));
def("ACET-FILE-MOVE", async (a, c) => bool(await moveFile(c, "acet-file-move", a[0], a[1])));
def("ACET-FILE-ATTR", async (a, c) => {
  if (a.length > 1) return NIL; // protection bits cannot be set on the library
  return (await files(c, "acet-file-attr").read(fileName(a[0], "acet-file-attr"))) === null ? NIL : 0;
});
def("VL-FILENAME-MKTEMP", async (a, c) => {
  const pattern = a[0] !== undefined && !nil(a[0]) ? str(a[0], "vl-filename-mktemp").replace(/^.*[\\/]/, "") : "$VL~~";
  const ext = a[2] !== undefined && !nil(a[2]) ? str(a[2], "vl-filename-mktemp") : /\.[^.]*$/.exec(pattern)?.[0] ?? ".tmp";
  const base = pattern.replace(/\.[^.]*$/, "").replace(/[^\w가-힣 .-]/g, "_") || "TMP";
  const taken = new Set((await listing(c, "vl-filename-mktemp")).map((f) => f.name.toLowerCase()));
  for (let i = 1; i < 1000; i++) {
    const n = `${base}${String(i).padStart(3, "0")}${ext}`;
    if (!taken.has(n.toLowerCase())) return n;
  }
  return NIL;
});
def("GETFILED", async (a, c) => {
  if (!c.host.input) throw new LispError("이 화면에서는 getfiled를 쓸 수 없습니다");
  const title = str(a[0], "getfiled");
  const ext = a[2] !== undefined ? str(a[2], "getfiled").replace(/^\./, "") : "";
  const flags = a[3] !== undefined ? num(a[3], "getfiled") : 0;
  const v = await waiting(c, () => c.host.input!("string", `${title} — LISP 라이브러리 파일 이름${ext ? ` (.${ext})` : ""}:`, undefined));
  if (v === null || !String(v).trim()) return NIL;
  let n = fileName(String(v).trim(), "getfiled");
  if (ext && ext !== "*" && !/\.[^.]+$/.test(n)) n = `${n}.${ext}`;
  if (!(flags & 1) && (await files(c, "getfiled").read(n)) === null) return NIL; // an existing file was asked for
  return n;
});
def("ACET-UI-PICKDIR", () => "."); // the library is the only folder

// W1: osnap — the nearest object snap of the asked kinds around a point (model entities only)
def("OSNAP", (a, c) => {
  const p = pt(a[0], "osnap");
  const modes = str(a[1], "osnap").toUpperCase().split(/[,\s]+/).filter(Boolean);
  const m = model(c, "osnap");
  const want = (k: string) => modes.some((x) => k.startsWith(x.replace(/^_/, "").slice(0, 3)));
  let best: Pt | null = null;
  let bd = Infinity;
  const consider = (q: Pt) => {
    const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (d < bd) [bd, best] = [d, q];
  };
  for (const e of m.all()) {
    const i = curveInfo(e);
    if (!i) continue;
    if (want("ENDPOINT")) [i.start, i.end].forEach((t) => consider(i.at(t)));
    if (want("MIDPOINT")) {
      const t = paramAtDist(i, i.dist(i.end) / 2);
      if (t !== null) consider(i.at(t));
    }
    if ((want("CENTER") || want("QUADRANT")) && (e.geom.type === "CIRCLE" || e.geom.type === "ARC")) {
      const g = e.geom;
      if (want("CENTER")) consider([g.center[0], g.center[1]]);
      if (want("QUADRANT")) for (let k = 0; k < 4; k++) consider([g.center[0] + g.radius * Math.cos((k * Math.PI) / 2), g.center[1] + g.radius * Math.sin((k * Math.PI) / 2)]);
    }
    if (want("NEAREST")) consider(i.at(nearestParam(e, i, p)));
  }
  return best ? [...(best as Pt), 0] : NIL;
});

// Express Tools: strings, system, user interface, drag and extents
def("ACET-LOAD-EXPRESSTOOLS", () => T);
def("ACET-STR-EQUAL", (a) => bool(str(a[0], "acet-str-equal").toUpperCase() === str(a[1], "acet-str-equal").toUpperCase()));
def("ACET-STR-COLLATE", (a) => {
  const [x, y] = [str(a[0], "acet-str-collate"), str(a[1], "acet-str-collate")];
  return x.localeCompare(y, undefined, { sensitivity: "accent" });
});
def("ACET-STR-FIND", (a) => {
  const [pat, s] = [str(a[0], "acet-str-find"), str(a[1], "acet-str-find")];
  const fold = a[2] !== undefined && truthy(a[2]);
  const i = (fold ? s.toUpperCase() : s).indexOf(fold ? pat.toUpperCase() : pat);
  return i < 0 ? NIL : i + 1; // 1-based
});
def("ACET-STR-REPLACE", (a) => {
  const [from, to, s] = [str(a[0], "acet-str-replace"), str(a[1], "acet-str-replace"), str(a[2], "acet-str-replace")];
  if (!from) return s;
  const fold = a[3] !== undefined && truthy(a[3]);
  const re = new RegExp(from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), fold ? "gi" : "g");
  const hits = (s.match(re) ?? []).length; // the result's size is known before it is built
  if (s.length + hits * (to.length - from.length) > LIMITS.string) throw new LispError("문자열은 64 KB까지입니다");
  return s.replace(re, () => to);
});
def("ACET-STR-WCMATCH", (a) => bool(wcmatch(str(a[0], "acet-str-wcmatch").toUpperCase(), str(a[1], "acet-str-wcmatch").toUpperCase())));
def("ACET-STR-FORMAT", (a) => capStr(str(a[0], "acet-str-format").replace(/%(\d)/g, (_m, d: string) => {
  const v = a[Number(d)];
  return v === undefined ? "" : typeof v === "string" ? v : show(v);
})));
def("ACET-SYS-BEEP", () => NIL);
def("ACET-SYS-KEYSTATE", () => 0); // no key is held while a program runs
def("ACET-SYS-LASTERR", (_a, c) => c.session.lastError ?? NIL);
def("ACET-SYS-SLEEP", async (a, c) => {
  const ms = Math.min(5000, Math.max(0, num(a[0], "acet-sys-sleep")));
  if (Date.now() + ms > c.deadline) throw new LimitError("실행 시간이 30초를 넘었습니다");
  await new Promise((r) => setTimeout(r, ms));
  return T;
});
def("ACET-UI-MESSAGE", async (a, c) => {
  const msg = str(a[0], "acet-ui-message");
  const buttons = a[2] !== undefined ? num(a[2], "acet-ui-message") & 15 : 0; // 0 OK, 1 OK/Cancel, 4 Yes/No, 3 Yes/No/Cancel
  log(c, msg);
  if (buttons === 0 || !c.host.input) return 1; // IDOK
  const words = buttons === 1 ? ["OK", "Cancel"] : buttons === 3 ? ["Yes", "No", "Cancel"] : ["Yes", "No"];
  const v = await waiting(c, () => c.host.input!("string", `${msg} [${words.join("/")}]`, undefined));
  const w = v === null ? "Cancel" : (keyword(String(v), words) ?? "Cancel");
  return { OK: 1, Cancel: 2, Yes: 6, No: 7 }[w] ?? 2;
});
def("ACET-UI-STATUS", (a, c) => {
  if (a[0] !== undefined && typeof a[0] === "string") log(c, a[0]);
  return a[0] ?? NIL;
});
def("ACET-UI-PROGRESS", (a, c) => {
  if (typeof a[0] === "string") log(c, `${a[0]}${a[1] !== undefined ? ` (0/${show(a[1])})` : ""}`);
  return T;
});
def("ACET-UI-TXTED", async (a, c) => {
  if (!c.host.input) throw new LispError("이 화면에서는 acet-ui-txted를 쓸 수 없습니다");
  const v = await waiting(c, () => c.host.input!("string", typeof a[1] === "string" ? a[1] : "문자:", undefined));
  return v === null ? NIL : capStr(String(v));
});
def("ACET-SS-DRAG-MOVE", async (a, c) => {
  ss(a[0], "acet-ss-drag-move");
  if (!c.host.input) throw new LispError("이 화면에서는 acet-ss-drag-move를 쓸 수 없습니다");
  const v = await waiting(c, () => c.host.input!("point", typeof a[2] === "string" ? a[2] : "두 번째 점:", pt(a[1], "acet-ss-drag-move")));
  return Array.isArray(v) ? [v[0], v[1], 0] : NIL;
});
def("ACET-SS-DRAG-ROTATE", async (a, c) => {
  ss(a[0], "acet-ss-drag-rotate");
  if (!c.host.input) throw new LispError("이 화면에서는 acet-ss-drag-rotate를 쓸 수 없습니다");
  const v = await waiting(c, () => c.host.input!("angle", typeof a[2] === "string" ? a[2] : "회전 각도:", pt(a[1], "acet-ss-drag-rotate")));
  return typeof v === "number" ? real(v) : NIL;
});
def("ACET-SS-DRAG-SCALE", async (a, c) => {
  ss(a[0], "acet-ss-drag-scale");
  if (!c.host.input) throw new LispError("이 화면에서는 acet-ss-drag-scale를 쓸 수 없습니다");
  const v = await waiting(c, () => c.host.input!("dist", typeof a[2] === "string" ? a[2] : "축척 비율:", pt(a[1], "acet-ss-drag-scale")));
  return typeof v === "number" ? real(v) : NIL;
});
def("ACET-ENT-GEOMEXTENTS", (a, c) => {
  const m = model(c, "acet-ent-geomextents");
  const items = typeof a[0] === "object" && !isList(a[0]) && !(a[0] instanceof Sym) && a[0].kind === "ss" ? a[0].items.map((s) => s.handle) : [ename(a[0], "acet-ent-geomextents").sel.handle];
  const xs: number[] = [];
  const ys: number[] = [];
  for (const h of items) {
    const e = m.get(h);
    if (!e) continue;
    for (const p of entityPoints(e)) {
      xs.push(p[0]);
      ys.push(p[1]);
    }
  }
  return xs.length ? [[Math.min(...xs), Math.min(...ys), 0], [Math.max(...xs), Math.max(...ys), 0]] : NIL;
});
/** Points enough for an extents box: curve samples, a text's insertion point. */
function entityPoints(e: Ent): Pt[] {
  const i = curveInfo(e);
  if (i) return Array.from({ length: 65 }, (_v, k) => i.at(i.start + ((i.end - i.start) * k) / 64));
  const g = e.geom as unknown as Record<string, unknown>;
  for (const k of ["insert", "center", "position", "start"]) if (Array.isArray(g[k])) return [g[k] as Pt];
  return [];
}
// model space <-> paper space distances: the viewport's scale is given, or 1 without a viewport
defEach("ACET-MS-TO-PS ACET-PS-TO-MS", (name) => (a) => {
  const scale = a[1] !== undefined && isNum(a[1]) ? nv(a[1]) : 1;
  const f = name === "ACET-MS-TO-PS" ? scale : 1 / scale;
  return isList(a[0]) ? a[0].map((x) => num(x, name.toLowerCase()) * f) : num(a[0], name.toLowerCase()) * f;
});
// layer previous: the screen keeps no layer history across runs, so tracking marks only
let layerpMode = true;
def("ACET-LAYERP-MODE", (a) => {
  if (a.length) layerpMode = truthy(a[0]);
  return bool(layerpMode);
});
def("ACET-LAYERP-MARK", (a) => bool(a.length ? truthy(a[0]) : true));

// --- W2 (plan §22.4): XDATA, dictionaries, XRecords, LDATA, layer states, nested selection ------

const XDATA_MAX = 16_383;
/** (-3 ("APP" (1000 . "x") ...)) out of an entity list: the plain groups and the XDATA by application. */
function splitXdata(l: Val[], c: Run, fn: string): [Val[], Record<string, [number, unknown][]> | null] {
  const at = l.findIndex((x) => isList(x) && x[0] === -3);
  if (at < 0) return [l, null];
  const xd: Record<string, [number, unknown][]> = Object.create(null);
  for (const app of (l[at] as Val[]).slice(1)) {
    const al = list(app, fn);
    const name = str(al[0], fn);
    if (/^(ACAD|ACDB|EZDXF)/i.test(name)) throw new ModelError(`AutoCAD·시스템 응용 프로그램의 확장 데이터는 바꿀 수 없습니다: ${name}`);
    if (!model(c, fn).tableFind("regapp", name)) throw new ModelError(`등록되지 않은 응용 프로그램입니다 (regapp으로 먼저 등록하세요): ${name}`);
    const g = groups(al.slice(1), fn) as [number, unknown][];
    for (const [code, v] of g) if (code < 1000 || code > 1071 || code === 1001 || (typeof v === "string" && (v.length > 255 || /[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(v)))) throw new ModelError(`확장 데이터 그룹 ${code}: 값이 맞지 않습니다`);
    xd[name] = g;
  }
  return [l.filter((_, i) => i !== at), xd];
}
// bytes as the server counts them (UTF-8 text)
const xdataSize = (xd: Record<string, [number, unknown][]>) => Object.entries(xd).reduce((n, [app, g]) => n + utf8(app) + 3 + g.reduce((m, [code, v]) => m + (typeof v === "string" ? utf8(v) + 3 : Array.isArray(v) ? 26 : code === 1071 ? 6 : code === 1070 ? 4 : 10), 0), 0);
const checkXdataSize = (xd: Record<string, [number, unknown][]>) => {
  if (xdataSize(xd) > XDATA_MAX) throw new ModelError("확장 데이터는 객체당 16 KB까지입니다");
  return xd;
};

/** Server reads (an object's groups, a dictionary entry) are asked once per saved revision: the screen
 * keeps one objects host per revision, and a revision never changes (W2 review H1). */
const reads = new WeakMap<object, Map<string, Promise<unknown>>>();
const READS_PER_RUN = 100;
const SERVER_WAIT_MS = 60_000; // waiting for the server given back to the time limit, per run
function once<X>(c: Run, key: string, f: () => Promise<X>): Promise<X> {
  const host = c.host.objects!;
  let m = reads.get(host);
  if (!m) reads.set(host, (m = new Map()));
  if (!m.has(key)) {
    if (++c.objectReads > READS_PER_RUN) throw new LispError(`한 번 실행에서 도면 조회는 ${READS_PER_RUN}번까지입니다`);
    if (m.size > 5000) m.clear();
    const t0 = Date.now();
    m.set(
      key,
      f().finally(() => {
        const give = Math.min(Date.now() - t0, Math.max(0, SERVER_WAIT_MS - c.serverWaited));
        c.serverWaited += give;
        c.deadline += give;
      }),
    );
  }
  return m.get(key) as Promise<X>;
}
const saved = (h: string) => /^[0-9A-F]{1,16}$/i.test(h); // not "~1" (made in this run) nor "new-3" (not yet saved)
const serverTags = async (c: Run, handle: string) => (saved(handle) && c.host.objects ? once(c, `e:${handle}`, () => c.host.objects!.entity(handle)) : null);
/** XDATA groups by application: the drawing's, with this run's changes over them. */
async function xdataOf(c: Run, e: Ent): Promise<Record<string, [number, unknown][]>> {
  // W4: the drawing sends every entity's XDATA with it; only one whose XDATA did not fit (xdq) is asked for
  if (!e.xdq) return { ...(e.xd ?? {}) };
  const out: Record<string, [number, unknown][]> = Object.create(null);
  const got = await serverTags(c, e.handle);
  let app: string | null = null;
  for (const [code, v] of got?.tags ?? []) {
    if (code === 1001) out[(app = String(v))] = [];
    else if (app && code >= 1000) {
      const g = out[app];
      if (code >= 1020 && code <= 1033) {
        const last = g[g.length - 1]; // 1010 x, 1020 y, 1030 z come apart from the server
        if (last && Array.isArray(last[1])) (last[1] as number[]).push(Number(v));
      } else g.push([code, code >= 1010 && code <= 1013 ? [Number(v)] : v]);
    }
  }
  return { ...out, ...(e.xd ?? {}) };
}
const dictEname = (owner: string | undefined, path: string[], handle: string, type: string): Ename => ({ kind: "ename", sel: { handle, layer: "0", geom: { type } as unknown as Sel["geom"] }, obj: { t: "dict", owner, path } });
type DictNode = { type: string; handle: string; entries: string[]; tags: [number, unknown][] };
/** A dictionary entry as it is now: the drawing's, with this run's (and earlier unsaved runs') changes. */
const DICT_KEY = /^[^\x00-\x1f\x7f-\x9f\u2028\u2029]{1,255}$/;
async function dictRead(c: Run, owner: string | undefined, path: string[], fn: string): Promise<DictNode | null> {
  const m = model(c, fn);
  if (path.length > 8 || path.some((k) => !DICT_KEY.test(k))) return null; // a key the drawing cannot have
  const view = m.dictView(owner, path); // W6: the drawing sends its dictionaries; ask only when it did not
  if (view !== undefined) return view;
  const local = m.dictLocal(owner, path);
  if (local?.deleted) return null;
  if (local?.tags) return { type: "XRECORD", handle: `~${DrawingModel.dictKey(owner, path)}`, entries: [], tags: local.tags };
  const server = c.host.objects && (owner === undefined || saved(owner)) ? await once(c, `d:${DrawingModel.dictKey(owner, path)}`, () => c.host.objects!.dict(path, owner)) : null;
  const kids = m.dictChildren(owner, path);
  if (!server && !local?.dict && !kids.some((k) => !k.deleted)) return null;
  if (server && server.type !== "DICTIONARY") return { type: server.type, handle: server.handle, entries: [], tags: (server.tags ?? []) as [number, unknown][] };
  const names = new Set((server?.entries ?? []).map((x) => x.name));
  for (const k of kids) {
    if (k.deleted) names.delete(k.name);
    else names.add(k.name);
  }
  return { type: "DICTIONARY", handle: server?.handle ?? `~${DrawingModel.dictKey(owner, path)}`, entries: [...names], tags: [] };
}
async function extDict(c: Run, handle: string): Promise<Ename | null> {
  const local = model(c, "entget").dictChildren(handle, []).some((k) => !k.deleted);
  if (!local && !model(c, "entget").get(handle)?.xdict) return null; // W4: the drawing says it has none
  const got = local ? null : await serverTags(c, handle);
  const h = got?.tags.find(([code], i, t) => code === 360 && t[i - 1]?.[1] === "{ACAD_XDICTIONARY")?.[1];
  return local || h ? dictEname(handle, [], String(h ?? `~${handle}`), "DICTIONARY") : null;
}
const dictArg = (v: Val, fn: string): { owner?: string; path: string[] } => {
  const e = ename(v, fn);
  if (e.obj?.t !== "dict") throw new LispError(`${fn}: 사전(namedobjdict·dictsearch의 결과)이 필요합니다`);
  return e.obj;
};
async function objGroups(c: Run, e: Ename): Promise<Val> {
  const o = e.obj!;
  if (o.t === "tbl") {
    const rec = tblRecords(model(c, "entget"), o.table)?.find((r) => isList(r) && String((r[1] as Pair).cdr).toUpperCase() === o.name.toUpperCase());
    return rec ? [pair(-1, e), ...(rec as Val[])] : NIL;
  }
  if (o.t === "new") return [pair(-1, e), pair(0, o.type), ...o.tags.map(([code, v]) => grp(code, v))];
  const n = await dictRead(c, o.owner, o.path, "entget");
  if (!n) return NIL;
  if (n.type === "XRECORD") return [pair(-1, e), pair(0, "XRECORD"), pair(5, n.handle), pair(100, "AcDbXrecord"), ...n.tags.map(([code, v]) => grp(code, v))];
  if (n.type !== "DICTIONARY") return [pair(-1, e), pair(0, n.type), pair(5, n.handle)];
  const out: Val[] = [pair(-1, e), pair(0, "DICTIONARY"), pair(5, n.handle), pair(100, "AcDbDictionary"), pair(281, 1)];
  for (const name of n.entries) out.push(pair(3, name), pair(350, dictEname(o.owner, [...o.path, name], `~${DrawingModel.dictKey(o.owner, [...o.path, name])}`, "XRECORD")));
  return out;
}
async function entryGroups(c: Run, owner: string | undefined, path: string[], fn: string): Promise<Val> {
  const n = await dictRead(c, owner, path, fn);
  return n ? objGroups(c, dictEname(owner, path, n.handle, n.type)) : NIL;
}

def("NAMEDOBJDICT", async (_a, c) => {
  const n = await dictRead(c, undefined, [], "namedobjdict");
  return dictEname(undefined, [], n?.handle ?? "~NOD", "DICTIONARY");
});
def("DICTSEARCH", async (a, c) => {
  const d = dictArg(a[0], "dictsearch");
  const key = str(a[1], "dictsearch");
  const n = await dictRead(c, d.owner, d.path, "dictsearch");
  const name = n?.entries.find((x) => x.toUpperCase() === key.toUpperCase());
  if (!name) return NIL;
  if (a[2] !== undefined && truthy(a[2])) dictPos(c).set(posKey(d), n!.entries.indexOf(name) + 1);
  return entryGroups(c, d.owner, [...d.path, name], "dictsearch");
});
const dictPos = (c: Run) => (c.session.dictPos ??= new Map());
const posKey = (d: { owner?: string; path: string[] }) => DrawingModel.dictKey(d.owner, d.path);
def("DICTNEXT", async (a, c) => {
  const d = dictArg(a[0], "dictnext");
  const n = await dictRead(c, d.owner, d.path, "dictnext");
  if (!n) return NIL;
  const i = a[1] !== undefined && truthy(a[1]) ? 0 : (dictPos(c).get(posKey(d)) ?? 0);
  dictPos(c).set(posKey(d), i + 1);
  return i < n.entries.length ? entryGroups(c, d.owner, [...d.path, n.entries[i]], "dictnext") : NIL;
});
def("DICTADD", async (a, c) => {
  const d = dictArg(a[0], "dictadd");
  const key = str(a[1], "dictadd");
  const o = ename(a[2], "dictadd");
  const m = model(c, "dictadd");
  const n = await dictRead(c, d.owner, d.path, "dictadd");
  if (n?.entries.some((x) => x.toUpperCase() === key.toUpperCase())) return NIL; // the key is taken
  return quiet(c, "dictadd", () => {
    const path = [...d.path, key];
    if (o.obj?.t === "new" && o.obj.type === "XRECORD") m.dictSet(d.owner, path, o.obj.tags);
    else if (o.obj?.t === "new") m.dictMake(d.owner, path);
    else throw new ModelError("entmakex로 만든 XRECORD나 DICTIONARY만 넣을 수 있습니다");
    const placed = dictEname(d.owner, path, o.sel.handle, o.obj.type);
    o.obj = placed.obj; // the same ename now names the entry
    return o;
  });
});
def("DICTREMOVE", async (a, c) => {
  const d = dictArg(a[0], "dictremove");
  const key = str(a[1], "dictremove");
  const n = await dictRead(c, d.owner, d.path, "dictremove");
  const name = n?.entries.find((x) => x.toUpperCase() === key.toUpperCase());
  if (!name) return NIL;
  const node = await dictRead(c, d.owner, [...d.path, name], "dictremove");
  return quiet(c, "dictremove", () => {
    model(c, "dictremove").dictDelete(d.owner, [...d.path, name]);
    return dictEname(d.owner, [...d.path, name], node?.handle ?? "~", node?.type ?? "XRECORD");
  });
});
def("DICTRENAME", async (a, c) => {
  const d = dictArg(a[0], "dictrename");
  const [from, to] = [str(a[1], "dictrename"), str(a[2], "dictrename")];
  const n = await dictRead(c, d.owner, d.path, "dictrename");
  const name = n?.entries.find((x) => x.toUpperCase() === from.toUpperCase());
  if (!name || n!.entries.some((x) => x.toUpperCase() === to.toUpperCase())) return NIL;
  const node = await dictRead(c, d.owner, [...d.path, name], "dictrename");
  if (node?.type !== "XRECORD") return NIL; // ponytail: XRecords only; a dictionary is renamed by moving its entries
  return quiet(c, "dictrename", () => {
    const m = model(c, "dictrename");
    m.dictRoom(2);
    m.dictSet(d.owner, [...d.path, to], node.tags);
    m.dictDelete(d.owner, [...d.path, name]);
    return to;
  });
});
def("TBLOBJNAME", (a, c) => {
  const table = str(a[0], "tblobjname").toUpperCase();
  const want = str(a[1], "tblobjname");
  const rec = tblRecords(model(c, "tblobjname"), table)?.find((r) => isList(r) && String((r[1] as Pair).cdr).toUpperCase() === want.toUpperCase());
  if (!rec) return NIL;
  const name = String(((rec as Val[])[1] as Pair).cdr);
  return { kind: "ename", sel: { handle: `~${table}:${name}`, layer: "0", geom: { type: table } as unknown as Sel["geom"] }, obj: { t: "tbl", table, name } };
});
def("XDROOM", async (a, c) => {
  const e = model(c, "xdroom").get(ename(a[0], "xdroom").sel.handle);
  return e ? XDATA_MAX - xdataSize(await xdataOf(c, e)) : NIL;
});

// LDATA: LISP values kept in the drawing (an XRecord of the printed value; read back with read)
const LDATA_DICT = "AXCAD_LDATA";
const ldataWhere = (v: Val, fn: string): { owner?: string; path: string[] } => {
  if (typeof v === "string") {
    if (!v) throw new LispError(`${fn}: 사전 이름이 필요합니다`);
    return { path: [v] };
  }
  const o = vla(v, fn);
  if (o.t !== "ent") throw new LispError(`${fn}: 사전 이름이나 객체가 필요합니다`);
  return { owner: o.handle, path: [LDATA_DICT] };
};
/** Numbers, strings, symbols and lists of them; at most 20,000 values, 100 levels deep (a shared
 * sub-list counts each time it appears, so a value never grows past what it prints). */
const storable = (v: Val): boolean => {
  let budget = 20_000;
  const ok = (x: Val, depth: number): boolean => {
    if (--budget < 0 || depth > 100) return false;
    if (x instanceof Flo) return true;
    if (typeof x === "number") return Number.isFinite(x);
    if (typeof x === "string" || x instanceof Sym) return true;
    if (isList(x)) return x.every((y) => ok(y, depth + 1));
    return isPair(x) && ok(x.car, depth + 1) && ok(x.cdr, depth + 1);
  };
  return ok(v, 0);
};
def("VLAX-LDATA-TEST", (a) => bool(storable(a[0])));
def("VLAX-LDATA-PUT", (a, c) => {
  const w = ldataWhere(a[0], "vlax-ldata-put");
  const key = str(a[1], "vlax-ldata-put");
  if (!storable(a[2])) throw new LispError("vlax-ldata-put: 숫자·문자열·기호·목록만 저장할 수 있습니다");
  const text = show(a[2]);
  if (utf8(text) > 60_000) throw new LispError("vlax-ldata-put: 값은 60 KB까지입니다");
  const chars = [...text]; // split on characters, never inside a surrogate pair
  const tags: [number, unknown][] = [];
  for (let i = 0; i < chars.length || i === 0; i += 600) tags.push([1, chars.slice(i, i + 600).join("")]); // 600 characters <= 2,048 bytes
  return quiet(c, "vlax-ldata-put", () => (model(c, "vlax-ldata-put").dictSet(w.owner, [...w.path, key], tags), a[2]));
});
const ldataValue = (n: DictNode | null): Val => {
  if (!n || n.type !== "XRECORD") return NIL;
  const text = n.tags.filter(([code]) => code === 1).map(([, v]) => String(v)).join("");
  try {
    return read(text)[0] ?? NIL;
  } catch {
    return NIL; // not a value AX-LISP wrote
  }
};
def("VLAX-LDATA-GET", async (a, c) => {
  const w = ldataWhere(a[0], "vlax-ldata-get");
  const v = ldataValue(await dictRead(c, w.owner, [...w.path, str(a[1], "vlax-ldata-get")], "vlax-ldata-get"));
  return truthy(v) || a[2] === undefined ? v : a[2]; // (vlax-ldata-get dict key default)
});
def("VLAX-LDATA-DELETE", async (a, c) => {
  const w = ldataWhere(a[0], "vlax-ldata-delete");
  const path = [...w.path, str(a[1], "vlax-ldata-delete")];
  if (!(await dictRead(c, w.owner, path, "vlax-ldata-delete"))) return NIL;
  return quiet(c, "vlax-ldata-delete", () => (model(c, "vlax-ldata-delete").dictDelete(w.owner, path), T));
});
def("VLAX-LDATA-LIST", async (a, c) => {
  const w = ldataWhere(a[0], "vlax-ldata-list");
  const n = await dictRead(c, w.owner, w.path, "vlax-ldata-list");
  const out: Val[] = [];
  for (const key of n?.entries ?? []) {
    const v = ldataValue(await dictRead(c, w.owner, [...w.path, key], "vlax-ldata-list"));
    out.push(isList(v) ? [key, ...v] : { kind: "pair", car: key, cdr: v }); // (key . value) as cons builds it
  }
  return out;
});

// layer states: kept in the drawing as XRecords under AXCAD_LAYERSTATES (one per state)
const LS_DICT = "AXCAD_LAYERSTATES";
type LsLayer = { name: string; flags: number; color: number; linetype: string; lwt: number };
const lsRead = async (c: Run, name: string, fn: string): Promise<{ desc: string; mask: number; layers: LsLayer[] } | null> => {
  const n = await dictRead(c, undefined, [LS_DICT, name], fn);
  if (!n || n.type !== "XRECORD") return null;
  const r = { desc: "", mask: 0, layers: [] as LsLayer[] };
  for (const [code, v] of n.tags) {
    if (code === 1) r.desc = String(v);
    else if (code === 90) r.mask = Number(v);
    else if (code === 8) r.layers.push({ name: String(v), flags: 0, color: 7, linetype: "Continuous", lwt: -3 });
    else if (r.layers.length) {
      const l = r.layers[r.layers.length - 1];
      if (code === 70) l.flags = Number(v);
      else if (code === 62) l.color = Number(v);
      else if (code === 6) l.linetype = String(v);
      else if (code === 92) l.lwt = Number(v); // lineweight (an XRecord takes 90-99, not 370)
    }
  }
  return r;
};
const lsTags = (desc: string, mask: number, layers: LsLayer[]): [number, unknown][] => [
  [1, desc],
  [90, mask],
  ...layers.flatMap((l): [number, unknown][] => [[8, l.name], [70, l.flags], [62, l.color], [6, l.linetype], [92, l.lwt]]),
];
const lsOf = (m: DrawingModel, names?: string[]): LsLayer[] =>
  m.layers.filter((l) => !names || names.some((n) => n.toUpperCase() === l.name.toUpperCase())).map((l) => ({ name: l.name, flags: (l.off ? 1 : 0) | (l.frozen ? 2 : 0) | (l.locked ? 4 : 0) | (l.plot === false ? 8 : 0), color: l.color ?? 7, linetype: l.linetype ?? "Continuous", lwt: l.lwt ?? -3 }));
const lsName = (v: Val, fn: string) => {
  const n = str(v, fn);
  if (!/^[^\x00-\x1f\x7f<>/\\":;?*|=`]{1,255}$/.test(n)) throw new LispError(`${fn}: 레이어 상태 이름으로 쓸 수 없습니다 (${n})`);
  return n;
};
def("LAYERSTATE-SAVE", (a, c) => {
  const name = lsName(a[0], "layerstate-save");
  const mask = a[1] !== undefined ? Math.trunc(num(a[1], "layerstate-save")) & 0xffff : 0xffff;
  const m = model(c, "layerstate-save");
  return quiet(c, "layerstate-save", () => (m.dictSet(undefined, [LS_DICT, name], lsTags("", mask, lsOf(m))), T));
});
def("LAYERSTATE-HAS", async (a, c) => bool(!!(await lsRead(c, lsName(a[0], "layerstate-has"), "layerstate-has"))));
def("LAYERSTATE-GETNAMES", async (_a, c) => (await dictRead(c, undefined, [LS_DICT], "layerstate-getnames"))?.entries ?? NIL);
def("LAYERSTATE-DELETE", async (a, c) => {
  const name = lsName(a[0], "layerstate-delete");
  if (!(await lsRead(c, name, "layerstate-delete"))) return NIL;
  return quiet(c, "layerstate-delete", () => (model(c, "layerstate-delete").dictDelete(undefined, [LS_DICT, name]), T));
});
def("LAYERSTATE-RENAME", async (a, c) => {
  const [from, to] = [lsName(a[0], "layerstate-rename"), lsName(a[1], "layerstate-rename")];
  const s = await lsRead(c, from, "layerstate-rename");
  if (!s || (await lsRead(c, to, "layerstate-rename"))) return NIL;
  return quiet(c, "layerstate-rename", () => {
    const m = model(c, "layerstate-rename");
    m.dictRoom(2);
    m.dictSet(undefined, [LS_DICT, to], lsTags(s.desc, s.mask, s.layers));
    m.dictDelete(undefined, [LS_DICT, from]);
    return T;
  });
});
def("LAYERSTATE-GETLAYERS", async (a, c) => {
  const s = await lsRead(c, lsName(a[0], "layerstate-getlayers"), "layerstate-getlayers");
  if (!s) return NIL;
  const inState = new Set(s.layers.map((l) => l.name.toUpperCase()));
  const invert = a[1] !== undefined && truthy(a[1]);
  return model(c, "layerstate-getlayers").layers.map((l) => l.name).filter((n) => inState.has(n.toUpperCase()) !== invert);
});
const layerNames = (v: Val, fn: string) => list(v, fn).map((x) => (isList(x) ? str(x[0], fn) : str(x, fn)));
def("LAYERSTATE-ADDLAYERS", async (a, c) => {
  const name = lsName(a[0], "layerstate-addlayers");
  const s = await lsRead(c, name, "layerstate-addlayers");
  if (!s) return NIL;
  const m = model(c, "layerstate-addlayers");
  const add = lsOf(m, layerNames(a[1], "layerstate-addlayers"));
  const keep = s.layers.filter((l) => !add.some((x) => x.name.toUpperCase() === l.name.toUpperCase()));
  return quiet(c, "layerstate-addlayers", () => (m.dictSet(undefined, [LS_DICT, name], lsTags(s.desc, s.mask, [...keep, ...add])), T));
});
def("LAYERSTATE-REMOVELAYERS", async (a, c) => {
  const name = lsName(a[0], "layerstate-removelayers");
  const s = await lsRead(c, name, "layerstate-removelayers");
  if (!s) return NIL;
  const drop = new Set(layerNames(a[1], "layerstate-removelayers").map((n) => n.toUpperCase()));
  return quiet(c, "layerstate-removelayers", () => (model(c, "layerstate-removelayers").dictSet(undefined, [LS_DICT, name], lsTags(s.desc, s.mask, s.layers.filter((l) => !drop.has(l.name.toUpperCase())))), T));
});
def("LAYERSTATE-RESTORE", async (a, c) => {
  const name = lsName(a[0], "layerstate-restore");
  const s = await lsRead(c, name, "layerstate-restore");
  if (!s) return NIL;
  const m = model(c, "layerstate-restore");
  const mask = s.mask || 0xffff;
  return quiet(c, "layerstate-restore", () => {
    for (const l of s.layers) {
      if (!m.layer(l.name)) continue; // a layer gone since the state was saved
      m.layerSet(l.name, {
        ...(mask & 1 ? { off: !!(l.flags & 1) } : {}),
        ...(mask & 2 ? { frozen: !!(l.flags & 2) } : {}),
        ...(mask & 4 ? { locked: !!(l.flags & 4) } : {}),
        ...(mask & 8 ? { plot: !(l.flags & 8) } : {}),
        ...(mask & 32 ? { color: l.color } : {}),
        ...(mask & 64 && m.tableFind("linetype", l.linetype) ? { linetype: l.linetype } : {}),
        ...(mask & 128 ? { lineweight: l.lwt } : {}),
      });
    }
    c.session.lastLayerState = name;
    return name;
  });
});
def("LAYERSTATE-GETLASTRESTORED", (_a, c) => c.session.lastLayerState ?? "");
def("LAYERSTATE-COMPARE", async (a, c) => {
  const s = await lsRead(c, lsName(a[0], "layerstate-compare"), "layerstate-compare");
  if (!s) return NIL;
  const now = new Map(lsOf(model(c, "layerstate-compare")).map((l) => [l.name.toUpperCase(), l]));
  const differ = s.layers.filter((l) => JSON.stringify({ ...l, name: "" }) !== JSON.stringify({ ...(now.get(l.name.toUpperCase()) ?? {}), name: "" })).map((l) => l.name);
  return differ.length ? differ : NIL; // nil: the drawing matches the state
});
def("LAYERSTATE-EXPORT", async (a, c) => {
  const name = lsName(a[0], "layerstate-export");
  const s = await lsRead(c, name, "layerstate-export");
  if (!s) return NIL;
  const file = fileName(a[1], "layerstate-export");
  const text = [`AXCAD-LAYERSTATE\t${name}\t${s.mask}\t${s.desc}`, ...s.layers.map((l) => [l.name, l.flags, l.color, l.linetype, l.lwt].join("\t"))].join("\n");
  try {
    await waiting(c, () => files(c, "layerstate-export").write(file, `${text}\n`));
  } catch (e) {
    throw new LispError(`layerstate-export: ${e instanceof Error ? e.message : String(e)}`);
  }
  return T;
});
def("LAYERSTATE-IMPORT", async (a, c) => {
  const text = await files(c, "layerstate-import").read(fileName(a[0], "layerstate-import"));
  if (text === null) return NIL;
  const [head, ...rows] = text.split(/\r?\n/).filter(Boolean);
  const h = head?.split("\t") ?? [];
  if (h[0] !== "AXCAD-LAYERSTATE") throw new LispError("layerstate-import: AX-CAD가 내보낸 레이어 상태 파일이 아닙니다");
  const name = lsName(h[1] ?? "", "layerstate-import");
  if (rows.length > 1900) throw new LispError("layerstate-import: 레이어는 1,900개까지입니다");
  const int = (v: string, lo: number, hi: number) => {
    const n = Number(v);
    if (!Number.isInteger(n) || n < lo || n > hi) throw new LispError("layerstate-import: 파일의 값이 맞지 않습니다");
    return n;
  };
  const layers = rows.map((r) => {
    const [n, flags, color, linetype, lwt] = r.split("\t");
    return { name: lsName(n ?? "", "layerstate-import"), flags: int(flags, 0, 15), color: int(color, 0, 256), linetype: lsName(linetype || "Continuous", "layerstate-import"), lwt: int(lwt, -3, 211) };
  });
  return quiet(c, "layerstate-import", () => (model(c, "layerstate-import").dictSet(undefined, [LS_DICT, name], lsTags(h[3] ?? "", Number(h[2]) | 0, layers)), name));
});

// nested selection: the picked entity, its transform and the block references holding it
const insertMatrix = (e: Ent): Val[] => {
  const g = e.geom as unknown as { insert?: Pt; rotation?: number; xscale?: number; yscale?: number };
  const r = ((g.rotation ?? 0) * Math.PI) / 180;
  const [sx, sy] = [g.xscale ?? 1, g.yscale ?? 1];
  const [x, y] = g.insert ?? [0, 0];
  return [[sx * Math.cos(r), -sy * Math.sin(r), 0, x], [sx * Math.sin(r), sy * Math.cos(r), 0, y], [0, 0, 1, 0], [0, 0, 0, 1]];
};
const nested = (c: Run, picked: { sel: Sel; at: Pt }, four: boolean): Val => {
  const e = c.host.model?.get(picked.sel.handle);
  const name: Ename = { kind: "ename", sel: picked.sel };
  if (!e || e.geom.type !== "INSERT") return [name, [picked.at[0], picked.at[1], 0]];
  // ponytail: the reference itself is returned (the block's own entities are not editable objects here)
  const m = insertMatrix(e);
  const matrix = four ? m : [...[0, 1, 2].map((col) => [0, 1, 2].map((row) => (m[row] as number[])[col])), [(m[0] as number[])[3], (m[1] as number[])[3], 0]];
  return [name, [picked.at[0], picked.at[1], 0], matrix, [name]];
};
def("NENTSEL", async (a, c) => {
  if (!c.host.entsel) throw new LispError("이 화면에서는 객체 선택(nentsel)을 쓸 수 없습니다");
  const r = await waiting(c, () => c.host.entsel!(typeof a[0] === "string" ? a[0] : "객체 선택:"));
  return r ? nested(c, r, false) : NIL;
});
def("NENTSELP", async (a, c) => {
  const at = a.find((x) => isList(x) && x.length >= 2);
  if (at) {
    const p = pt(at, "nentselp");
    const m = model(c, "nentselp");
    let best: Ent | null = null;
    let bd = Infinity;
    for (const e of m.all()) {
      const i = curveInfo(e);
      if (!i) continue;
      const q = i.at(nearestParam(e, i, p));
      const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (d < bd) [bd, best] = [d, e];
    }
    return best && bd < 1e-6 + 1 ? nested(c, { sel: toSel(best), at: p }, true) : NIL;
  }
  if (!c.host.entsel) throw new LispError("이 화면에서는 객체 선택(nentselp)을 쓸 수 없습니다");
  const r = await waiting(c, () => c.host.entsel!(typeof a[0] === "string" ? a[0] : "객체 선택:"));
  return r ? nested(c, r, true) : NIL;
});

// --- W4 (plan §22.4): properties through the ActiveX member table (axobj.ts) --------------------

const objOf = (v: Val, fn: string): Obj => {
  if (typeof v === "object" && !isList(v) && !(v instanceof Sym) && v.kind === "ename") return { t: "ent", handle: v.sel.handle };
  return vla(v, fn);
};
/** "StartPoint/X" style names: the property and the coordinate (X, Y, Z). */
const splitProp = (name: string): [string, number | null] => {
  const [p, sub] = name.split("/");
  const i = sub ? "XYZ".indexOf(sub.toUpperCase()) : -1;
  return [p, i >= 0 ? i : null];
};
const valueText = (v: OVal): string => (v === null ? "nil" : Array.isArray(v) ? `(${v.map((x) => valueText(x as OVal)).join(" ")})` : typeof v === "object" ? `#<${(v as { t: string }).t}>` : String(v));
function dumpLines(m: DrawingModel, o: Obj, withMethods: boolean): string[] {
  const out: string[] = [];
  for (const { name, member } of axMembers(m, o)) {
    if (member.event) continue;
    if (member.get || member.legacy) {
      try {
        const v = getProp(m, o, name);
        out.push(`;   ${name}${member.put || member.legacy ? "" : " (RO)"} = ${valueText(v)}`);
        continue;
      } catch {
        /* a legacy name that is a method, or not this object's: listed below */
      }
    }
    if (withMethods && (member.call || member.legacy)) out.push(`;   ${name} ()`);
  }
  return out;
}
def("VLAX-DUMP-OBJECT", (a, c) => {
  const m = model(c, "vlax-dump-object");
  const o = objOf(a[0], "vlax-dump-object");
  log(c, `; ${describe(m, o)}: AutoCAD ${classOf(m, o) ?? "object"}`);
  log(c, "; Property values:");
  for (const l of dumpLines(m, o, a[1] !== undefined && truthy(a[1]))) log(c, l);
  return T;
});
def("DUMPALLPROPERTIES", (a, c) => {
  const m = model(c, "dumpallproperties");
  const o = objOf(a[0], "dumpallproperties");
  log(c, `Begin dumping object (class: ${String(getProp(m, o, "ObjectName"))})`);
  for (const l of dumpLines(m, o, false)) log(c, l.replace(/^;\s+/, ""));
  log(c, "End object dump");
  return NIL;
});
def("GETPROPERTYVALUE", (a, c) => {
  const m = model(c, "getpropertyvalue");
  const o = objOf(a[0], "getpropertyvalue");
  const [prop, axis] = splitProp(str(a[1], "getpropertyvalue"));
  const v = getProp(m, o, prop);
  return fromO(axis !== null && Array.isArray(v) ? ((v[axis] as OVal) ?? 0) : v);
});
def("SETPROPERTYVALUE", (a, c) => {
  const m = model(c, "setpropertyvalue");
  const o = objOf(a[0], "setpropertyvalue");
  const [prop, axis] = splitProp(str(a[1], "setpropertyvalue"));
  let v = toO(a[2]);
  if (axis !== null) {
    const cur = getProp(m, o, prop);
    if (!Array.isArray(cur)) throw new LispError(`setpropertyvalue: ${prop}은(는) 점이 아닙니다`);
    v = cur.map((x, i) => (i === axis ? v : x)) as OVal;
  }
  putProp(m, o, prop, v);
  return NIL;
});
def("ISPROPERTYREADONLY", (a, c) => {
  const m = model(c, "ispropertyreadonly");
  const o = objOf(a[0], "ispropertyreadonly");
  const [prop] = splitProp(str(a[1], "ispropertyreadonly"));
  const mem = axMembers(m, o).find((x) => x.name.toUpperCase() === prop.toUpperCase())?.member;
  if (!mem) throw new LispError(`ispropertyreadonly: ${describe(m, o)}에 ${prop} 속성이 없습니다`);
  return mem.put || mem.legacy ? 0 : 1;
});
def("VLAX-METHOD-APPLICABLE-P", (a, c) => bool(axHas(model(c, "vlax-method-applicable-p"), vla(a[0], "vlax-method-applicable-p"), propName(a[1], "vlax-method-applicable-p"), "method") === true));

// W9: what vlax-curve-* return are REALs (points, distances, parameters)
const realDeep = (v: Val): Val => (typeof v === "number" ? real(v) : isList(v) ? v.map(realDeep) : v);
for (const [n, b] of BUILTINS) if (n.startsWith("VLAX-CURVE-")) BUILTINS.set(n, { ...b, fn: async (a, c) => realDeep(await b.fn(a, c)) });
