"""W0 (plan §22): how much of Autodesk's published AutoLISP function list AX-LISP implements.

ref/lisp.json  = function names + one-line summaries from Autodesk's public AutoLISP reference
                 (names and the short purpose line only; collected 2026-11-10, AutoCAD 2025 help).
frontend/src/lib/cad/lisp.ts = the interpreter (builtins registered with def("NAME", ...) and the
                 special forms in the evaluator's switch).
Output: Markdown report with implemented / left out on purpose / not implemented, and the %.
Run:  python3 -I tools/coverage/lisp_coverage.py [--md out.md]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend/src/lib/cad/lisp.ts"
REF = ROOT / "tools/coverage/ref/lisp.json"

# plan §22.5: the sandbox leaves these out on purpose (they get a clear error, not a silent no-op)
EXCLUDED = re.compile(
    r"^(startapp|arx.*|ads|menucmd|menu.*|vl-registry-.*|vlax-(create|get|get-or-create)-object|"
    r"vlax-import-type-library|vlax-(typeinfo|dump-object)-.*|vl-vba.*|vlisp-compile|vl-load-all|"
    r"vlax-add-cmd|vl-doc-.*|vl-exit-.*|command-s?-not-allowed|getfiled-.*|"
    r"acet-reg-.*|acet-ini-.*|acet-file-(chdir|cwd|rmdir)|textscr|graphscr|help|setfunhelp)$"
)
EXPRESS = re.compile(r"^acet-")  # Express Tools (a separate library, decided on its own)
SPECIAL = {  # forms the evaluator handles itself, not through def()
    "quote",
    "setq",
    "if",
    "cond",
    "progn",
    "and",
    "or",
    "while",
    "repeat",
    "foreach",
    "vlax-for",
    "lambda",
    "function",
    "set",
    "boundp",
    "vl-symbol-value",
    "eval",
    "defun",
    "*error*",  # the evaluator calls a user *error* on failure
}


def implemented() -> set[str]:
    s = SRC.read_text(encoding="utf-8")
    names: set[str] = set()
    for group in re.findall(r'\bdef\(\s*"([^"]+)"', s):  # def("A B C", fn): one body, several names
        names |= {n.lower() for n in group.split()}
    names |= SPECIAL
    return names


def classify() -> dict[str, list[str]]:
    ref = json.loads(REF.read_text(encoding="utf-8"))
    have = implemented()
    res: dict[str, list[str]] = {"done": [], "excluded": [], "express": [], "missing": []}
    for n in sorted(ref):
        if n in have or n.startswith("vla-"):  # vla-* is dispatched by the object model
            res["done"].append(n)
        elif EXPRESS.match(n):
            res["express"].append(n)
        elif EXCLUDED.match(n):
            res["excluded"].append(n)
        else:
            res["missing"].append(n)
    return res


def report() -> str:
    r = classify()
    total = sum(len(v) for v in r.values())
    base = total - len(r["excluded"]) - len(r["express"])
    pct = 100 * len(r["done"]) / base if base else 0
    L = [
        "# AX-LISP vs Autodesk AutoLISP reference",
        "",
        (
            f"- reference functions: **{total}**  ·  implemented: **{len(r['done'])}**  ·  "
            f"left out on purpose (sandbox): **{len(r['excluded'])}**  ·  Express Tools: "
            f"**{len(r['express'])}**  ·  not implemented: **{len(r['missing'])}**"
        ),
        (
            f"- coverage of the in-scope set (reference − sandbox − Express): **{pct:.1f}%** "
            f"({len(r['done'])}/{base})"
        ),
        "",
        "## Not implemented",
        "",
        ", ".join(f"`{n}`" for n in r["missing"]) or "—",
        "",
        "## Left out on purpose (sandbox)",
        "",
        ", ".join(f"`{n}`" for n in r["excluded"]) or "—",
        "",
    ]
    return "\n".join(L)


if __name__ == "__main__":
    text = report()
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(text, encoding="utf-8")
    print(text)
