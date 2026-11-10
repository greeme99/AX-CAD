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
SCOPE = ROOT / "tools/coverage/ref/activex_scope.json"  # plan §22.2: the 50 objects AX-CAD has
# W4: the object model's member table, per object ("full" / "limited"), written by axobj.test.ts
TABLE = ROOT / "tools/coverage/ref/axobj_members.json"

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
# plan §22.5 inside the §22.2 scope (W6, 2026-11-10): members of the 50 objects that are 3D modelling,
# external references / raster files, COM and external code, files outside the data folder,
# AutoCAD's own windows and menus, and plotting. Left out of the denominator; scripts get a clear
# error for the ones they can name (docs/8 lists them).
SCOPE_EXCLUDED = re.compile(
    r"^("
    # 3D solids, surfaces, meshes, regions (a 2D drawing)
    r"Add3DFace|Add3DMesh|Add3DPoly|AddBox|AddCone|AddCylinder|AddEllipticalCone|"
    r"AddEllipticalCylinder|AddExtrudedSolid|AddExtrudedSolidAlongPath|AddPolyfaceMesh|AddRegion|"
    r"AddRevolvedSolid|AddSection|AddSphere|AddTorus|AddWedge|AddCustomObject|"
    # external references and raster images (files outside the drawing)
    r"AttachExternalReference|AddRaster|Bind|Detach|Reload|Unload|XRefDatabase|"
    # COM, ARX/VBA projects, command line injection, shapes
    r"Eval|GetInterfaceObject|LoadARX|UnloadARX|ListARX|LoadDVB|UnloadDVB|RunMacro|VBE|ARXLoaded|"
    r"ARXUnloaded|LoadShapeFile|SendCommand|PostCommand|"
    # files and URLs outside the data folder
    r"Open|New|Save|SaveAs|Close|Import|Export|WBlock|GetRemoteFile|PutRemoteFile|IsRemoteFile|"
    r"IsURL|LaunchBrowserDialog|BeginOpen|EndOpen|BeginSave|EndSave|BeginFileDrop|"
    # AutoCAD's windows, menus, preferences and session
    r"HWND|MenuBar|MenuGroups|Preferences|StatusID|WindowLeft|WindowTop|WindowState|Quit|BeginQuit|"
    r"AppActivate|AppDeactivate|WindowChanged|WindowMovedOrResized|BeginRightClick|"
    r"BeginShortcutMenu\w*|BeginDoubleClick|BeginModal|EndModal|NewDrawing|BeginClose|BeginDocClose|"
    r"Activate|Deactivate|"
    # plotting (the PLOT dialog prints)
    r"Plot|PlotConfigurations|BeginPlot|EndPlot"
    r")$",
    re.IGNORECASE,
)
# window size is AutoCAD's frame only on these (elsewhere Height / Width are drawing data)
SCOPE_EXCLUDED_ON = {"Application": {"height", "width"}, "Document": {"height", "width"}}


def scope_excluded(obj: str, member: str) -> bool:
    return bool(SCOPE_EXCLUDED.match(member)) or member.lower() in SCOPE_EXCLUDED_ON.get(obj, set())


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
    "MText": "MTEXT",  # W7a
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


def scope_coverage(have: set[str]) -> tuple[dict[str, tuple[int, int, int]], int, int, int]:
    """plan §22.2 denominator: (object, member) pairs of the 50 in-scope objects. Since W4 a pair
    counts when the object model's table has that member FOR THAT OBJECT (axobj_members.json);
    "limited" pairs (AX-CAD has no such data: the default is read) are counted and shown apart.
    Utility (VBA only) is still matched by name in vba.ts."""
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    table = json.loads(TABLE.read_text(encoding="utf-8")) if TABLE.exists() else {}
    per: dict[str, tuple[int, int, int]] = {}
    done = limited = total = 0
    for obj, kinds in scope.items():
        all_members = [m for k in ("Methods", "Properties", "Events") for m in kinds[k]]
        members = [m for m in all_members if not scope_excluded(obj, m)]
        mine = {k.lower(): v for k, v in table.get(obj, {}).items()}
        if obj == "Utility":
            ok, lim = sum(1 for m in members if m.lower() in have), 0
        else:
            ok = sum(1 for m in members if m.lower() in mine)
            lim = sum(1 for m in members if mine.get(m.lower()) == "limited")
        per[obj] = (ok, lim, len(members))
        done += ok
        limited += lim
        total += len(members)
    return per, done, limited, total


def report() -> str:
    ref = json.loads(REF.read_text(encoding="utf-8"))
    have = implemented_members()
    per, sdone, slim, stotal = scope_coverage(have)
    L = [
        "# AX-CAD object model (VBA · vla-) vs Autodesk ActiveX reference",
        "",
        (
            f"- **plan §22.2 scope** (members of the 50 objects AX-CAD has): **{sdone}/{stotal} = "
            f"{100 * sdone / stotal:.1f}%** (target 85%)"
        ),
        (
            f"- of these, **limited** (AX-CAD keeps no such data: the default is read and only it "
            f"is taken): {slim}; full: {sdone - slim} = {100 * (sdone - slim) / stotal:.1f}%"
        ),
        "",
        "| object | members done / total | limited |",
        "|---|---|---|",
        *[f"| {o} | {d}/{t} | {lim} |" for o, (d, lim, t) in sorted(per.items())],
        "",
        "## Whole reference (for scale)",
        "",
    ]
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
