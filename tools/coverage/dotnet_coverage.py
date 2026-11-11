"""W0 (plan §22): how close the AX-CAD C# SDK (sdk/dotnet/AxCad) is to the AutoCAD .NET API.

ref/dotnet.json = class / structure / enum names per namespace from Autodesk's public Managed .NET
                  reference (AutoCAD 2024 help, collected 2026-11-10).
The SDK is a REST client outside the app (plan §22.1), so it is measured against CORE: the types an
automation script that reads and writes drawings actually uses. The whole-namespace numbers are
shown for scale only.
Run:  python3 -I tools/coverage/dotnet_coverage.py [--md out.md]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "sdk/dotnet/AxCad"
REF = ROOT / "tools/coverage/ref/dotnet.json"

CORE = [  # DatabaseServices, then Geometry, then EditorInput
    "Database",
    "Transaction",
    "TransactionManager",
    "ObjectId",
    "ObjectIdCollection",
    "DBObject",
    "Entity",
    "Curve",
    "Line",
    "Circle",
    "Arc",
    "Polyline",
    "DBPoint",
    "DBText",
    "MText",
    "Ellipse",
    "Spline",
    "Hatch",
    "Leader",
    "MLeader",
    "BlockTable",
    "BlockTableRecord",
    "BlockReference",
    "AttributeDefinition",
    "AttributeReference",
    "LayerTable",
    "LayerTableRecord",
    "LinetypeTable",
    "LinetypeTableRecord",
    "TextStyleTable",
    "TextStyleTableRecord",
    "DimStyleTable",
    "DimStyleTableRecord",
    "RegAppTable",
    "RegAppTableRecord",
    "SymbolTable",
    "SymbolTableRecord",
    "DBDictionary",
    "Xrecord",
    "ResultBuffer",
    "TypedValue",
    "Dimension",
    "AlignedDimension",
    "RotatedDimension",
    "RadialDimension",
    "DiametricDimension",
    "LineAngularDimension2",
    "OpenMode",
    "Point2d",
    "Point3d",
    "Vector2d",
    "Vector3d",
    "Matrix3d",
    "Extents3d",
    "Tolerance",
    "Scale3d",
    "SelectionFilter",
    "PromptSelectionResult",
]
# what the SDK calls a type when its name differs from AutoCAD's
ALIASES = {"ModelSpace": "BlockTableRecord"}


def sdk_types() -> set[str]:
    names: set[str] = set()
    for f in SDK.glob("*.cs"):
        names |= set(
            re.findall(
                r"public (?:sealed |abstract |static |readonly |partial )*(?:class|struct|enum|interface|record(?: struct| class)?) (\w+)",
                f.read_text(encoding="utf-8"),
            )
        )
    return names | {ALIASES[n] for n in names if n in ALIASES}


def report() -> str:
    ref = json.loads(REF.read_text(encoding="utf-8"))
    have = sdk_types()
    done = [n for n in CORE if n in have]
    miss = [n for n in CORE if n not in have]
    L = [
        "# AX-CAD C# SDK vs AutoCAD .NET API",
        "",
        f"- core set: **{len(done)}/{len(CORE)} = {100 * len(done) / len(CORE):.1f}%**",
        "",
        "| namespace | classes | structures | enums | in the SDK |",
        "|---|---|---|---|---|",
    ]
    for ns, kinds in ref.items():
        allnames = {n for v in kinds.values() for n in v}
        L.append(
            f"| {ns} | {len(kinds.get('Classes', []))} | {len(kinds.get('Structures', []))} | {len(kinds.get('Enumerations', []))} | {len(allnames & have)} |"
        )
    L += [
        "",
        "## Core: in the SDK",
        "",
        ", ".join(f"`{n}`" for n in done) or "—",
        "",
        "## Core: missing",
        "",
        ", ".join(f"`{n}`" for n in miss) or "—",
        "",
    ]
    return "\n".join(L)


if __name__ == "__main__":
    text = report()
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(text, encoding="utf-8")
    print(text)
