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
    r"acet-reg-.*|acet-ini-.*|acet-file-(chdir|cwd|rmdir)|textscr|graphscr|help|setfunhelp|"
    # W1 (2026-11-10): the same sandbox categories, found while implementing the rest —
    # ARX loading, product keys (registry), a window on a URL, OS processes, help, the text screen
    r"autoarxload|vl-arx-import|vlax-(machine-|user-)?product-key|showhtmlmodalwindow|"
    r"acet-sys-(command|spawn|term|procid|wait|foreground)|acad_helpdlg|acet-help(-trap)?|textpage|"
    r"functions)$"  # "functions" is the reference's index page, not a function
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
    "defun-q",
    "*error*",  # the evaluator calls a user *error* on failure
}


def implemented() -> set[str]:
    s = SRC.read_text(encoding="utf-8")
    names: set[str] = set()
    for group in re.findall(r'\bdef(?:Each)?\(\s*"([^"]+)"', s):  # def("A B C", fn): several names
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
        elif EXCLUDED.match(n):  # the sandbox rule wins, also for Express Tools (acet-reg-*, ...)
            res["excluded"].append(n)
        elif EXPRESS.match(n):
            res["express"].append(n)  # in the denominator since 2026-11-10 (plan §22.2)
        else:
            res["missing"].append(n)
    return res


def report() -> str:
    r = classify()
    total = sum(len(v) for v in r.values())
    base = total - len(r["excluded"])  # Express Tools count (plan §22.2)
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
            f"- coverage of the in-scope set (reference − sandbox; Express Tools included): **{pct:.1f}%** "
            f"({len(r['done'])}/{base})"
        ),
        "",
        "## Not implemented",
        "",
        ", ".join(f"`{n}`" for n in r["missing"]) or "—",
        "",
        "## Express Tools (in scope, not implemented)",
        "",
        ", ".join(f"`{n}`" for n in r["express"]) or "—",
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
