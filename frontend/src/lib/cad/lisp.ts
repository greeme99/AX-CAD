// Track 11: AX-LISP — a safe AutoLISP subset (plan.md §17). Its own reader and evaluator: no eval,
// no files, network or OS; the drawing changes only through (command ...) inputs, which the screen
// runs through the command prompt reducer afterwards (one undo step, nothing on an error).
import type { Pt } from "./view";
import type { Sel } from "./tools";

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
export type Val = number | string | Sym | Val[] | Pair | SelSet | Ename | Builtin | Lambda;

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
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(8)));
  if (typeof v === "string") return JSON.stringify(v);
  if (v instanceof Sym) return v.name;
  if (isList(v)) return v.length ? `(${v.map(show).join(" ")})` : "nil";
  if (v.kind === "pair") return `(${show(v.car)} . ${show(v.cdr)})`;
  if (v.kind === "ss") return `<선택 집합 ${v.items.length}>`;
  if (v.kind === "ename") return `<객체 ${v.sel.handle}>`;
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
};

/** A run of command prompt inputs with the selection and current layer they use. */
export type Segment = { inputs: string[]; sel: Sel[]; layer?: string };
export type Outcome = { segments: Segment[]; log: string[]; commands: string[]; value: Val };

type Run = { host: Host; steps: number; deadline: number; segs: Segment[]; inputs: number; log: string[]; layer?: string; globals: Map<Sym, Val> };
type Env = { vars: Map<Sym, Val>; parent: Env | null };

const fmt = (n: number) => String(Number(n.toFixed(6)));

/** One (command ...) call -> prompt inputs; a selection argument starts its own segment. */
function command(args: Val[], c: Run): Val {
  const sel = args.find((a) => typeof a === "object" && !isList(a) && !(a instanceof Sym) && (a.kind === "ss" || a.kind === "ename")) as SelSet | Ename | undefined;
  const items: string[] = [];
  let skipEnter = false;
  for (const a of args) {
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
  if (s.name === "PI") return Math.PI;
  const b = BUILTINS.get(s.name);
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

async function call(f: Val, args: Val[], env: Env, c: Run, depth: number): Promise<Val> {
  // AutoLISP takes a quoted lambda or a function's name wherever a function goes ('(lambda ...), 'sq)
  if (isList(f) && f[0] instanceof Sym && f[0].name === "LAMBDA") f = lambda("lambda", f[1], f.slice(2));
  else if (f instanceof Sym) f = lookup(f, env, c);
  if (typeof f !== "object" || isList(f) || f instanceof Sym || (f.kind !== "builtin" && f.kind !== "lambda")) throw new LispError(`함수가 아닙니다: ${show(f)}`);
  if (f.kind === "builtin") return f.fn(args, c);
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
  if (++c.steps > LIMITS.steps) throw new LispError(`실행 단계가 ${LIMITS.steps.toLocaleString()}을 넘었습니다 (무한 반복?)`);
  if (depth > LIMITS.depth) throw new LispError("함수 호출이 너무 깊습니다 (재귀?)");
  if ((c.steps & 1023) === 0 && Date.now() > c.deadline) throw new LispError("실행 시간이 30초를 넘었습니다");
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
      case "LAMBDA":
        return lambda("lambda", rest[0], rest.slice(1));
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
const SPECIAL = new Set(["QUOTE", "SETQ", "IF", "COND", "PROGN", "AND", "OR", "WHILE", "REPEAT", "FOREACH", "LAMBDA", "DEFUN"]);

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
  for (let i = 0; i < n; i++) r.push(await call(a[0], ls.map((l) => l[i]), { vars: new Map(), parent: null }, c, 1));
  return cap(r);
});
def("APPLY", (a, c) => call(a[0], list(a[1], "apply"), { vars: new Map(), parent: null }, c, 1));

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
  if (a.length) c.log.push(typeof a[0] === "string" ? a[0] : show(a[0]));
  return a[0] ?? NIL;
});
def("COMMAND", (a, c) => command(a, c));
def("SETVAR", (a, c) => {
  const name = str(a[0], "setvar").toUpperCase();
  if (name === "CLAYER") {
    const l = str(a[1], "setvar");
    if (c.host.layers && !c.host.layers.some((x) => x.toUpperCase() === l.toUpperCase())) throw new LispError(`setvar: 도면에 없는 레이어 ${l}`);
    c.layer = c.host.layers?.find((x) => x.toUpperCase() === l.toUpperCase()) ?? l;
  } // other system variables (OSMODE, CMDECHO, ...) have no meaning here: accepted, ignored
  return a[1] ?? NIL;
});
def("GETVAR", (a, c) => (str(a[0], "getvar").toUpperCase() === "CLAYER" ? (c.layer ?? c.host.layer ?? "0") : NIL));

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
  if (!c.host.ssget) throw new LispError("이 화면에서는 선택 집합(ssget)을 쓸 수 없습니다");
  const items = await waiting(c, () => c.host.ssget!(typeof a[0] === "string" ? a[0] : null));
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
def("ENTGET", (a) => {
  const e = a[0];
  if (typeof e !== "object" || isList(e) || e instanceof Sym || e.kind !== "ename") throw new LispError("entget: 객체 이름(entsel/ssname)이 필요합니다");
  return entget(e.sel);
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
export async function runLisp(src: string, host: Host = {}, callCommand?: string): Promise<Outcome> {
  try {
    return await runForms(src, host, callCommand);
  } catch (e) {
    // a list nested 100k deep overflows JavaScript's stack while printing or comparing it
    if (e instanceof RangeError) throw new LispError("목록이 너무 깊게 중첩되었습니다");
    throw e;
  }
}

async function runForms(src: string, host: Host, callCommand?: string): Promise<Outcome> {
  const forms = read(src);
  const c: Run = { host, steps: 0, deadline: Date.now() + LIMITS.ms, segs: [], inputs: 0, log: [], layer: host.layer, globals: new Map() };
  const env: Env = { vars: new Map(), parent: null };
  let value: Val = NIL;
  // calling one command loads the file's definitions only, not the drawing its top level does
  const defining = (f: Val) => isList(f) && f[0] instanceof Sym && (f[0].name === "DEFUN" || f[0].name === "SETQ");
  for (const f of forms) if (!callCommand || defining(f)) value = await ev(f, env, c, 0);
  const commands = [...c.globals.entries()].filter(([s, v]) => s.name.startsWith("C:") && typeof v === "object" && !isList(v) && !(v instanceof Sym) && v.kind === "lambda").map(([s]) => s.name.slice(2));
  if (callCommand) {
    const f = c.globals.get(Sym.of(`C:${callCommand}`));
    if (!f) throw new LispError(`정의되지 않은 명령: ${callCommand}`);
    value = await call(f, [], env, c, 0);
  }
  return { segments: c.segs.filter((s) => s.inputs.length), log: c.log, commands, value };
}
