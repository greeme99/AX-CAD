// 2D automation A1: DCL (Dialog Control Language) for AX-LISP dialogs. Parses the dialog
// definitions of a .dcl file into a tile tree the screen draws; actions stay in LISP
// (action_tile). Supported tiles are the everyday ones; unknown tiles are refused, not guessed.

export type Tile = {
  type: string;
  key?: string;
  label?: string;
  value?: string;
  list?: string[];
  attrs: Record<string, string | number | boolean>;
  children: Tile[];
};

export const DCL_LIMITS = { bytes: 64 * 1024, tiles: 500, depth: 20 };

const CONTAINERS = ["dialog", "row", "column", "boxed_row", "boxed_column", "radio_row", "radio_column", "boxed_radio_row", "boxed_radio_column", "concatenation", "paragraph"];
const LEAVES = ["text", "text_part", "edit_box", "button", "toggle", "radio_button", "list_box", "popup_list", "spacer", "spacer_0", "spacer_1", "errtile", "slider", "image", "image_button"];
// predefined tiles of base.dcl
const PREDEF: Record<string, () => Tile> = {
  ok_only: () => row([button("accept", "확인", { is_default: true })]),
  ok_cancel: () => row([button("accept", "확인", { is_default: true }), button("cancel", "취소", { is_cancel: true })]),
  ok_cancel_help: () => row([button("accept", "확인", { is_default: true }), button("cancel", "취소", { is_cancel: true }), button("help", "도움말", {})]),
  ok_cancel_help_errtile: () => column([PREDEF.ok_cancel_help(), leaf("errtile", { key: "error" })]),
};
function leaf(type: string, attrs: Tile["attrs"]): Tile {
  return { type, attrs, children: [], ...(typeof attrs.key === "string" ? { key: attrs.key } : {}) };
}
function button(key: string, label: string, extra: Tile["attrs"]): Tile {
  return { type: "button", key, label, attrs: { key, label, ...extra }, children: [] };
}
const row = (children: Tile[]): Tile => ({ type: "row", attrs: {}, children });
const column = (children: Tile[]): Tile => ({ type: "column", attrs: {}, children });

export class DclError extends Error {}

type Tok = { t: string; str?: boolean; line: number };

function tokens(src: string): Tok[] {
  if (src.length > DCL_LIMITS.bytes) throw new DclError("DCL 파일은 64 KB까지입니다");
  const out: Tok[] = [];
  let i = 0;
  let line = 1;
  while (i < src.length) {
    const c = src[i];
    if (c === "\n") line++;
    if (/\s/.test(c)) i++;
    else if (src.startsWith("//", i)) while (i < src.length && src[i] !== "\n") i++;
    else if (src.startsWith("/*", i)) {
      const end = src.indexOf("*/", i + 2);
      const stop = end < 0 ? src.length : end + 2;
      line += (src.slice(i, stop).match(/\n/g) ?? []).length;
      i = stop;
    } else if (c === '"') {
      let s = "";
      i++;
      while (i < src.length && src[i] !== '"') {
        if (src[i] === "\\" && i + 1 < src.length) {
          const e = src[++i];
          s += e === "n" ? "\n" : e === "t" ? "\t" : e;
        } else s += src[i];
        i++;
      }
      if (i >= src.length) throw new DclError(`${line}번째 줄: 닫히지 않은 문자열`);
      i++;
      out.push({ t: s, str: true, line });
    } else if ("{}:;=".includes(c)) {
      out.push({ t: c, line });
      i++;
    } else if (c === "@") {
      throw new DclError(`${line}번째 줄: @include는 쓸 수 없습니다 (한 파일에 모두 적으세요)`);
    } else {
      let j = i;
      while (j < src.length && /[\w.$-]/.test(src[j])) j++;
      if (j === i) throw new DclError(`${line}번째 줄: 알 수 없는 문자 '${c}'`);
      out.push({ t: src.slice(i, j), line });
      i = j;
    }
  }
  return out;
}

type Def = { base: string; attrs: Tile["attrs"]; children: Tile[] };

const clone = (t: Tile): Tile => ({ ...t, attrs: { ...t.attrs }, children: t.children.map(clone), ...(t.list ? { list: [...t.list] } : {}) });
const size = (t: Tile): number => 1 + t.children.reduce((n, c) => n + size(c), 0);
const height = (t: Tile): number => 1 + Math.max(0, ...t.children.map(height));

/** A .dcl file -> its dialogs by name (lower case), ready for new_dialog. */
export function parseDcl(src: string): Map<string, Tile> {
  const toks = tokens(src);
  let p = 0;
  let count = 0;
  const defs = new Map<string, Def>();
  const peek = () => toks[p];
  const next = (what: string) => {
    const t = toks[p++];
    if (!t) throw new DclError(`파일이 끝났습니다 (${what} 필요)`);
    return t;
  };
  const expect = (s: string) => {
    const t = next(`'${s}'`);
    if (t.t !== s || t.str) throw new DclError(`${t.line}번째 줄: '${s}'가 필요합니다 ('${t.t}')`);
  };
  const value = (): string | number | boolean => {
    const t = next("값");
    if (t.str) return t.t;
    if (/^-?\d+(\.\d+)?$/.test(t.t)) return Number(t.t);
    if (t.t === "true") return true;
    if (t.t === "false") return false;
    return t.t; // left, centered, ...
  };
  // body: { attr = value; ... : tile { ... } ... name; }
  const body = (depth: number): { attrs: Tile["attrs"]; children: Tile[] } => {
    if (depth > DCL_LIMITS.depth) throw new DclError("타일이 너무 깊게 중첩되었습니다");
    expect("{");
    const attrs: Tile["attrs"] = {};
    const children: Tile[] = [];
    while (peek() && peek().t !== "}") {
      const t = peek();
      if (t.t === ":" && !t.str) {
        p++;
        const base = next("타일 종류").t;
        const b = body(depth + 1);
        children.push(build(base, b.attrs, b.children, depth + 1));
      } else {
        const name = next("이름");
        if (peek()?.t === "=") {
          p++;
          attrs[name.t.toLowerCase()] = value();
          expect(";");
        } else if (peek()?.t === ";") {
          p++;
          children.push(build(name.t, {}, [], depth + 1));
        } else if (peek()?.t === ":") {
          // named tile inside a body: "mybox : row { ... }"
          p++;
          const base = next("타일 종류").t;
          const b = body(depth + 1);
          children.push(build(base, b.attrs, b.children, depth + 1));
        } else throw new DclError(`${name.line}번째 줄: '=' 또는 ';'가 필요합니다`);
      }
    }
    expect("}");
    if (peek()?.t === ";") p++;
    return { attrs, children };
  };
  const build = (base: string, attrs: Tile["attrs"], children: Tile[], depth: number): Tile => {
    if (++count > DCL_LIMITS.tiles) throw new DclError(`타일은 ${DCL_LIMITS.tiles}개까지입니다`);
    if (depth > DCL_LIMITS.depth) throw new DclError("타일이 너무 깊게 중첩되었습니다");
    const b = base.toLowerCase();
    if (PREDEF[b] && !Object.keys(attrs).length && !children.length) return PREDEF[b]();
    const proto = defs.get(b);
    if (proto) {
      // an inherited tile is copied, and the copies count: a chain of definitions that doubles at
      // each step cannot grow past the limit (security review A1)
      const inherited = proto.children.map(clone);
      count += inherited.reduce((n, c) => n + size(c), 0);
      if (count > DCL_LIMITS.tiles) throw new DclError(`타일은 ${DCL_LIMITS.tiles}개까지입니다`);
      return build(proto.base, { ...proto.attrs, ...attrs }, [...inherited, ...children], depth + 1);
    }
    if (!CONTAINERS.includes(b) && !LEAVES.includes(b)) throw new DclError(`지원하지 않는 타일: ${base}`);
    const t: Tile = { type: b, attrs, children };
    if (typeof attrs.key === "string") t.key = attrs.key;
    if (attrs.label !== undefined) t.label = String(attrs.label);
    if (attrs.value !== undefined) t.value = String(attrs.value);
    if (typeof attrs.list === "string") t.list = attrs.list.split("\n");
    return t;
  };
  const dialogs = new Map<string, Tile>();
  while (p < toks.length) {
    const name = next("이름");
    expect(":");
    const base = next("타일 종류").t;
    const b = body(1);
    defs.set(name.t.toLowerCase(), { base, attrs: b.attrs, children: b.children });
    const t = build(base, b.attrs, b.children, 1);
    if (height(t) > DCL_LIMITS.depth) throw new DclError("타일이 너무 깊게 중첩되었습니다");
    if (t.type === "dialog") dialogs.set(name.t.toLowerCase(), t);
  }
  return dialogs;
}
