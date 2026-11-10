"""W0 (plan §22): how much of Autodesk's published ActiveX object library (the VBA object model,
also reached from LISP through vla-/vlax-) the shared AX-CAD object model implements.

ref/activex.json = object, method, property and event names (+ one-line summaries) from Autodesk's
                   public ActiveX Reference Guide index pages (AutoCAD 2026 help, collected 2026-11-10).
Implemented members are the names the object model answers: getProp/putProp/invoke in
frontend/src/lib/cad/objmodel.ts and the Utility object in vba.ts. Matching is BY NAME ONLY
(a method counts once whichever object has it), so the % is an upper bound per member and the
object table says which objects exist at all.
Run:  python3 -I tools/coverage/activex_coverage.py [--md out.md]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBJMODEL = ROOT / "frontend/src/lib/cad/objmodel.ts"
VBA = ROOT / "frontend/src/lib/cad/vba.ts"
REF = ROOT / "tools/coverage/ref/activex.json"

# plan §22.5: AutoCAD UI / COM / external-code entry points the sandbox leaves out on purpose
EXCLUDED_OBJECTS = re.compile(
    r"(Menu|Toolbar|Popup|^Preferences|^Hyperlink|^FileDependenc|^Security|^Plot)", re.IGNORECASE
)
EXCLUDED_METHODS = re.compile(
    r"^(LoadARX|UnloadARX|ListARX|LoadDVB|UnloadDVB|RunMacro|GetInterfaceObject|Eval|"
    r"LoadShapeFile|SendCommand|PostCommand|Quit|WindowState|Import|Export|SaveAs|Save|Close|"
    r"Open|New|AddMenu.*|AddToolbar.*|Float|Dock|InsertMenuInMenuBar|RemoveMenuFromMenuBar)$",
    re.IGNORECASE,
)
# ActiveX object -> the object model kind that answers for it
OBJECTS_DONE = {
    "Application": "app",
    "Document": "doc",
    "ModelSpace": "space",
    "Layers": "layers",
    "Layer": "layer",
    "Utility": "Utility (vba.ts)",
    "Line": "LINE",
    "Circle": "CIRCLE",
    "Arc": "ARC",
    "LWPolyline": "LWPOLYLINE",
    "Text": "TEXT",
    "Point": "POINT",
    "Ellipse": "ELLIPSE",
    "BlockReference": "INSERT",
}


def implemented_members() -> set[str]:
    s = OBJMODEL.read_text(encoding="utf-8")
    body = s[s.index("export function getProp") :]
    # member names appear as `case "NAME":` or `k === "NAME"`; DXF entity tags are not members
    tags = {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "POINT", "ELLIPSE", "INSERT", "BYLAYER"}
    names = {
        n.lower() for n in re.findall(r'(?:case |=== )"([A-Z][A-Z0-9]*)"', body) if n not in tags
    }
    v = VBA.read_text(encoding="utf-8")
    u = v[v.index("async function utility") : v.index("const log = (r: Run")]
    names |= set(re.findall(r'case "([a-z][a-z0-9]*)"', u))
    return names


def classify(
    kind: str, ref: dict[str, str], have: set[str], excluded: re.Pattern[str]
) -> dict[str, list[str]]:
    res: dict[str, list[str]] = {"done": [], "excluded": [], "missing": []}
    for n in sorted(ref, key=str.lower):
        if kind == "Objects":
            key = "done" if n in OBJECTS_DONE else "excluded" if excluded.search(n) else "missing"
        else:
            key = "done" if n.lower() in have else "excluded" if excluded.search(n) else "missing"
        res[key].append(n)
    return res


def report() -> str:
    ref = json.loads(REF.read_text(encoding="utf-8"))
    have = implemented_members()
    L = ["# AX-CAD object model (VBA · vla-) vs Autodesk ActiveX reference", ""]
    L.append(
        "| kind | reference | implemented | left out on purpose | not implemented | coverage of in-scope |"
    )
    L.append("|---|---|---|---|---|---|")
    parts = {}
    for kind, ex in (
        ("Objects", EXCLUDED_OBJECTS),
        ("Methods", EXCLUDED_METHODS),
        ("Properties", re.compile(r"$^")),
        ("Events", re.compile(r"$^")),
    ):
        r = classify(kind, ref[kind], have, ex)
        parts[kind] = r
        base = len(r["done"]) + len(r["missing"])
        pct = 100 * len(r["done"]) / base if base else 0
        L.append(
            f"| {kind} | {len(ref[kind])} | {len(r['done'])} | {len(r['excluded'])} | {len(r['missing'])} | **{pct:.1f}%** |"
        )
    L += ["", "Members match by name only (see the module docstring).", ""]
    for kind, r in parts.items():
        L += [f"## {kind}: implemented", "", ", ".join(f"`{n}`" for n in r["done"]) or "—", ""]
    return "\n".join(L)


if __name__ == "__main__":
    text = report()
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(text, encoding="utf-8")
    print(text)
