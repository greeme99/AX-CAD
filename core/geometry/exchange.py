"""FN-12 STEP AP242 / IGES exchange through XCAF (names + assembly tree), FN-15 per-part metrics."""

import io
import re
import tempfile
from pathlib import Path
from typing import Any

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.collections import Sequence_TDF_Label
from OCP.IFSelect import IFSelect_RetDone, IFSelect_RetVoid
from OCP.IGESCAFControl import IGESCAFControl_Reader, IGESCAFControl_Writer
from OCP.IGESControl import IGESControl_Controller
from OCP.Interface import Interface_Static
from OCP.ShapeFix import ShapeFix_Solid
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

from core.geometry.errors import GeomError
from core.geometry.metrics import count, measure
from core.geometry.serialize import from_brep, to_brep

FORMATS = ("STEP", "IGES")
STEP_AP242 = 5  # write.step.schema: 5 = AP242DIS
SEW_TOL_MM = 1e-3
MAX_TREE_NODES = 2000  # assembly tree JSON cap; parts list is always complete
# entities through which a STEP file can pull in other files from disk (path traversal).
# Any occurrence of the token rejects, comments and quoted strings included: requiring "(" next
# could be dodged with "/*x*/" between name and "(", and stripping comments with a quoted "/*".
STEP_EXTERNAL = re.compile(rb"DOCUMENT_FILE|EXTERNAL_SOURCE|EXTERNALLY_DEFINED", re.IGNORECASE)
# relative references resolve against the directory of this name, which cannot exist
STEP_STREAM_NAME = "/dev/null/model.step"
IGES_EXTERNAL_TYPES = {416}  # External Reference Entity


def check_file(data: bytes, fmt: str) -> None:
    """Magic bytes + no external references. Raises GeomError STEP_INVALID_FILE."""
    bad = GeomError("STEP_INVALID_FILE", f"Not a valid {fmt} file")
    if fmt == "STEP":
        if not data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"ISO-10303-21;"):
            raise bad
        if STEP_EXTERNAL.search(data):
            raise GeomError("STEP_INVALID_FILE", "External file references are not allowed")
        return
    lines = data.splitlines()
    if not lines or len(lines[0]) < 73 or lines[0][72:73] != b"S":  # Start section, column 73
        raise bad
    for line in lines:  # directory entry (column 73 = D), field 1 = entity type
        head = line[:8].strip()
        if line[72:73] == b"D" and head.isdigit() and int(head) in IGES_EXTERNAL_TYPES:
            raise GeomError("STEP_INVALID_FILE", "External file references are not allowed")


def _doc() -> TDocStd_Document:
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    return doc


def _name(label: TDF_Label) -> str:
    n = TDataStd_Name()
    return str(n.Get().ToExtString()) if label.FindAttribute(TDataStd_Name.GetID_s(), n) else ""


def _entry(label: TDF_Label) -> str:
    s = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, s)
    return str(s.ToCString())


def _compound(shapes: list[TopoDS_Shape]) -> TopoDS_Compound:
    comp, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def _sew(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Surface-only models (typical IGES): sew faces into shells, close shells into solids."""
    sew = BRepBuilderAPI_Sewing(SEW_TOL_MM)
    sew.Add(shape)
    sew.Perform()
    sewn = sew.SewedShape()
    solids: list[TopoDS_Shape] = []
    ex = TopExp_Explorer(sewn, TopAbs_SHELL)
    while ex.More():
        mk = BRepBuilderAPI_MakeSolid(TopoDS.Shell(ex.Current()))
        if mk.IsDone():
            fix = ShapeFix_Solid(mk.Solid())
            fix.Perform()
            solids.append(fix.Solid())
        ex.Next()
    return _compound(solids) if solids else sewn


def _read(data: bytes, fmt: str, path: str | None) -> TDocStd_Document:
    doc = _doc()
    reader: Any = STEPCAFControl_Reader() if fmt == "STEP" else IGESCAFControl_Reader()
    reader.SetNameMode(True)
    if fmt == "STEP":
        status = reader.ReadStream(STEP_STREAM_NAME, io.BytesIO(data))
    elif path:  # OCCT 8 IGES ReadStream fails on valid files: read the stored upload itself
        status = reader.ReadFile(path)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "model.igs"
            tmp_path.write_bytes(data)
            status = reader.ReadFile(str(tmp_path))
    if status == IFSelect_RetVoid:
        raise GeomError("STEP_EMPTY", f"{fmt} file contains no entities")
    if status != IFSelect_RetDone:
        raise GeomError("STEP_READ_FAILED", f"{fmt} file could not be read")
    if reader.NbRootsForTransfer() == 0:
        raise GeomError("STEP_EMPTY", f"{fmt} file contains no shapes")
    if not reader.Transfer(doc):
        raise GeomError("STEP_READ_FAILED", f"{fmt} transfer failed")
    if fmt == "STEP" and reader.ExternFiles().Extent():
        raise GeomError("STEP_INVALID_FILE", "External file references are not allowed")
    return doc


class _Walk:
    """Assembly tree + instance counts per part prototype (keyed by its label entry)."""

    def __init__(self, tool: XCAFDoc_ShapeTool):
        self.tool, self.nodes = tool, 0
        self.parts: dict[str, dict[str, Any]] = {}

    def node(self, label: TDF_Label) -> dict[str, Any] | None:
        self.nodes += 1
        name = _name(label)
        if not XCAFDoc_ShapeTool.IsAssembly_s(label):
            key = _entry(label)
            part = self.parts.get(key)
            if part is None:
                shape = XCAFDoc_ShapeTool.GetShape_s(label)
                part = self.parts[key] = {
                    "part_key": key,
                    "name": _name(label) or f"Part {len(self.parts) + 1}",
                    "instance_count": 0,
                    **measure(shape),
                }
            part["instance_count"] += 1
            if self.nodes > MAX_TREE_NODES:
                return None
            return {"name": name or part["name"], "kind": "PART", "part_key": key}
        comps = Sequence_TDF_Label()
        XCAFDoc_ShapeTool.GetComponents_s(label, comps, False)
        children = []
        for i in range(1, comps.Length() + 1):
            comp, ref = comps.Value(i), TDF_Label()
            if XCAFDoc_ShapeTool.GetReferredShape_s(comp, ref):
                child = self.node(ref)
                if child is not None:
                    children.append(child)
        if self.nodes > MAX_TREE_NODES:
            return None
        return {"name": name or "Assembly", "kind": "ASSEMBLY", "children": children}


def import_model(
    data: bytes, fmt: str, path: str | None = None
) -> tuple[TopoDS_Shape, dict[str, Any]]:
    """Read STEP/IGES into one located compound; metrics carry parts, assembly tree, warnings.
    `path` is where `data` is stored (lets IGES skip a temp copy)."""
    if fmt not in FORMATS:
        raise GeomError("STEP_INVALID_FILE", "Unsupported format")
    check_file(data, fmt)
    doc = _read(data, fmt, path)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = Sequence_TDF_Label()
    tool.GetFreeShapes(free)
    walk = _Walk(tool)
    tree, shapes = [], []
    for i in range(1, free.Length() + 1):
        label = free.Value(i)
        shapes.append(XCAFDoc_ShapeTool.GetShape_s(label))
        n = walk.node(label)
        if n is not None:
            tree.append(n)
    shape: TopoDS_Shape = _compound(shapes)
    if count(shape, TopAbs_FACE) == 0:
        raise GeomError("STEP_EMPTY", f"{fmt} file contains no geometry")
    warnings = []
    if count(shape, TopAbs_SOLID) == 0:
        shape = _sew(shape)
        warnings.append("SEWN")
        if count(shape, TopAbs_SOLID) == 0:
            warnings.append("OPEN_SHELLS")  # volume is meaningless
    if walk.nodes > MAX_TREE_NODES:
        warnings.append("TREE_TRUNCATED")
    parts = sorted(walk.parts.values(), key=lambda p: p["part_key"])
    metrics = {
        **measure(shape),
        "parts": parts,
        "part_count": len(parts),
        "instance_count": sum(p["instance_count"] for p in parts),
        "assembly_tree": tree,
        "warnings": warnings,
    }
    return shape, metrics


def export_model(items: list[tuple[str, TopoDS_Shape]], fmt: str) -> bytes:
    """Each item becomes a named top-level product. STEP is written as AP242, IGES in BRep mode."""
    doc = _doc()
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    for name, shape in items:
        TDataStd_Name.Set_s(tool.AddShape(shape, False), TCollection_ExtendedString(name))
    buf = io.BytesIO()
    if fmt == "STEP":
        writer = STEPCAFControl_Writer()
        # OCCT 8: the writer resets statics on creation, so set the schema afterwards and check it
        if not Interface_Static.SetIVal_s("write.step.schema", STEP_AP242):
            raise GeomError("GEOM_KERNEL_CRASH", "Could not select the AP242 schema")
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        ok = writer.Transfer(doc, STEPControl_AsIs) and writer.WriteStream(buf) == IFSelect_RetDone
    elif fmt == "IGES":
        # statics exist only after the controller is initialised; unset they silently stay faces-only
        IGESControl_Controller.Init_s()
        if not Interface_Static.SetIVal_s("write.iges.brep.mode", 1):  # 1 = BRep (MSBO solids)
            raise GeomError("GEOM_KERNEL_CRASH", "Could not select IGES BRep mode")
        Interface_Static.SetCVal_s("write.iges.unit", "MM")
        iges = IGESCAFControl_Writer()
        ok = iges.Transfer(doc) and iges.Write(buf)
    else:
        raise GeomError("STEP_INVALID_FILE", "Unsupported format")
    if not ok:
        raise GeomError("GEOM_KERNEL_CRASH", f"{fmt} export failed")
    return buf.getvalue()


# --- worker jobs: only bytes and dicts cross the process boundary ---


def import_job(path: str, fmt: str) -> tuple[bytes, dict[str, Any]]:
    # a path, not 100 MB of pickled bytes; nothing left behind if the worker is killed
    shape, metrics = import_model(Path(path).read_bytes(), fmt, path)
    return to_brep(shape), metrics


def export_job(items: list[tuple[str, bytes]], fmt: str) -> bytes:
    return export_model([(n, from_brep(b)) for n, b in items], fmt)
