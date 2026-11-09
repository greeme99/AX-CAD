// AutoLISP wcmatch patterns (2D automation A5): * any run, ? one character, # a digit, @ a letter,
// . a character that is neither, [abc] / [a-z] one of, [~abc] none of, ~ at the start of a
// pattern negates it, `,` separates alternatives, ` takes the next character literally.
// Its own linear matcher (each token takes one character, * backtracks to the last star): no
// regular expressions, so long text and hostile patterns cannot stall the screen.
export const WC_LIMITS = { pattern: 1000, text: 64 * 1024 };

type Tok = { t: "star" } | { t: "any" } | { t: "digit" } | { t: "alpha" } | { t: "punct" } | { t: "ch"; c: string } | { t: "set"; neg: boolean; ranges: [string, string][] };

const isDigit = (c: string) => c >= "0" && c <= "9";
const isAlpha = (c: string) => c.toLowerCase() !== c.toUpperCase();

/** Pattern text -> alternatives, each with its tokens and whether it is negated; null for a pattern that cannot be read. */
function compile(pattern: string): { neg: boolean; toks: Tok[] }[] | null {
  const alts: { neg: boolean; toks: Tok[] }[] = [];
  let cur: { neg: boolean; toks: Tok[] } = { neg: false, toks: [] };
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "`") {
      if (i + 1 >= pattern.length) return null;
      cur.toks.push({ t: "ch", c: pattern[++i] });
    } else if (c === ",") {
      alts.push(cur);
      cur = { neg: false, toks: [] };
    } else if (c === "~" && cur.toks.length === 0 && !cur.neg) cur.neg = true;
    else if (c === "*") {
      if (cur.toks.at(-1)?.t !== "star") cur.toks.push({ t: "star" }); // ** = *
    } else if (c === "?") cur.toks.push({ t: "any" });
    else if (c === "#") cur.toks.push({ t: "digit" });
    else if (c === "@") cur.toks.push({ t: "alpha" });
    else if (c === ".") cur.toks.push({ t: "punct" });
    else if (c === "[") {
      let j = i + 1;
      let neg = false;
      if (pattern[j] === "~") {
        neg = true;
        j++;
      }
      const ranges: [string, string][] = [];
      for (; j < pattern.length && pattern[j] !== "]"; j++) {
        let lo = pattern[j];
        if (lo === "`" && j + 1 < pattern.length) lo = pattern[++j];
        if (pattern[j + 1] === "-" && j + 2 < pattern.length && pattern[j + 2] !== "]") {
          ranges.push([lo, pattern[j + 2]]);
          j += 2;
        } else ranges.push([lo, lo]);
      }
      if (j >= pattern.length) return null; // no closing ]
      cur.toks.push({ t: "set", neg, ranges });
      i = j;
    } else cur.toks.push({ t: "ch", c });
  }
  alts.push(cur);
  return alts;
}

function one(tok: Tok, ch: string, ci: boolean): boolean {
  switch (tok.t) {
    case "any":
      return true;
    case "digit":
      return isDigit(ch);
    case "alpha":
      return isAlpha(ch);
    case "punct":
      return !isDigit(ch) && !isAlpha(ch);
    case "ch":
      return ci ? tok.c.toLowerCase() === ch.toLowerCase() : tok.c === ch;
    case "set": {
      const c = ci ? ch.toLowerCase() : ch;
      const hit = tok.ranges.some(([lo, hi]) => (ci ? lo.toLowerCase() <= c && c <= hi.toLowerCase() : lo <= c && c <= hi));
      return hit !== tok.neg;
    }
    default:
      return false;
  }
}

function matchAlt(toks: Tok[], s: string, ci: boolean): boolean {
  let p = 0;
  let i = 0;
  let star = -1;
  let mark = 0;
  while (i < s.length) {
    const t = toks[p];
    if (t && t.t !== "star" && one(t, s[i], ci)) {
      p++;
      i++;
    } else if (t && t.t === "star") {
      star = p++;
      mark = i;
    } else if (star >= 0) {
      p = star + 1;
      i = ++mark;
    } else return false;
  }
  while (toks[p]?.t === "star") p++;
  return p === toks.length;
}

/** AutoLISP (wcmatch str pattern); `ci` for the case-insensitive uses (layer names in filters). */
export function wcmatch(text: string, pattern: string, ci = false): boolean {
  if (pattern.length > WC_LIMITS.pattern || text.length > WC_LIMITS.text) return false;
  const alts = compile(pattern);
  if (!alts) return false;
  return alts.some((a) => matchAlt(a.toks, text, ci) !== a.neg);
}
