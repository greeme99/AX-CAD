"""FN-12 STEP/IGES import and export via OCCT XDE (STEPCAFControl / IGESCAFControl).

Units: mm. Export default: AP242.
IGES has no stream API in OCP 8 → temp files.
"""

import io
import tempfile
from pathlib import Path
from typing import Any

from OCP.collections import Sequence_TDF_Label
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESCAFControl import IGESCAFControl_Reader, IGESCAFControl_Writer
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.XSControl import XSControl_WorkSession

from core.geometry.errors import GeomError
from core.geometry.metrics import measure
from core.geometry.serialize import from_brep, to_brep

_APP = XCAFApp_Application.GetApplication_s()
_FMT = TCollection_ExtendedString("MDTV-XCAF")

# ---------- helpers ----------


def _new_doc() -> TDocStd_Document:
    doc = TDocStd_Document(_FMT)
    _APP.InitDocument(doc)
    return doc


def _label_name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return attr.Get().ToExtString()
    return ""


def _collect_parts(st: XCAFDoc_ShapeTool) -> list[dict[str, Any]]:
    """Walk free shapes, flatten assembly → parts list with metrics."""
    seq = Sequence_TDF_Label()
    st.GetFreeShapes(seq)
    parts: list[dict[str, Any]] = []

    def _walk(label: TDF_Label) -> None:
        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            comps = Sequence_TDF_Label()
            XCAFDoc_ShapeTool.GetComponents_s(label, comps)
            for i in range(1, comps.Size() + 1):
                ref = TDF_Label()
                if XCAFDoc_ShapeTool.GetReferredShape_s(comps.Value(i), ref):
                    _walk(ref)
                else:
                    _walk(comps.Value(i))
        else:
            shape = XCAFDoc_ShapeTool.GetShape_s(label)
            if shape is None or shape.IsNull():
                return
            name = _label_name(label) or f"Part_{len(parts) + 1}"
            m = measure(shape)
            parts.append({"name": name, "brep": to_brep(shape), "metrics": m})

    for i in range(1, seq.Size() + 1):  # OCP sequences are 1-indexed
        _walk(seq.Value(i))

    return parts


# ---------- STEP ----------


def step_import_job(data: bytes) -> list[dict[str, Any]]:
    """Worker entry: bytes → [{name, brep (bytes), metrics}]."""
    doc = _new_doc()
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(False)
    reader.SetLayerMode(False)

    status = reader.ReadStream("import.step", io.BytesIO(data))
    if status != IFSelect_RetDone:
        raise GeomError("STEP_READ_FAILED", "Failed to read STEP data", 422)

    if not reader.Transfer(doc):
        raise GeomError("STEP_READ_FAILED", "Failed to transfer STEP shapes", 422)

    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    parts = _collect_parts(st)
    if not parts:
        raise GeomError("STEP_EMPTY", "STEP file contains no solids", 422)
    return parts


def step_export_job(brep: bytes, name: str = "Part") -> bytes:
    """Worker entry: BREP bytes + name → STEP AP242 bytes."""
    shape = from_brep(brep)
    doc = _new_doc()
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    label = st.AddShape(shape)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

    session = XSControl_WorkSession()
    writer = STEPCAFControl_Writer(session, False)
    writer.SetNameMode(True)
    writer.SetColorMode(False)
    writer.SetLayerMode(False)

    Interface_Static.SetCVal_s("write.step.schema", "AP242")
    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")

    writer.Transfer(doc, STEPControl_AsIs)
    buf = io.BytesIO()
    status = writer.WriteStream(buf)
    if status != IFSelect_RetDone:
        raise GeomError("STEP_WRITE_FAILED", "Failed to write STEP data", 422)
    return buf.getvalue()


# ---------- IGES (no stream API in OCP 8 → temp files) ----------


def iges_import_job(data: bytes) -> list[dict[str, Any]]:
    """Worker entry: bytes → [{name, brep (bytes), metrics}]."""
    with tempfile.NamedTemporaryFile(suffix=".iges", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        doc = _new_doc()
        reader = IGESCAFControl_Reader()
        reader.SetNameMode(True)
        reader.SetColorMode(False)
        reader.SetLayerMode(False)

        status = reader.ReadFile(tmp_path)
        if status != IFSelect_RetDone:
            raise GeomError("IGES_READ_FAILED", "Failed to read IGES data", 422)

        if not reader.Transfer(doc):
            raise GeomError("IGES_READ_FAILED", "Failed to transfer IGES shapes", 422)

        st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        parts = _collect_parts(st)
        if not parts:
            raise GeomError("IGES_EMPTY", "IGES file contains no solids", 422)
        return parts
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def iges_export_job(brep: bytes, name: str = "Part") -> bytes:
    """Worker entry: BREP bytes → IGES bytes."""
    shape = from_brep(brep)
    doc = _new_doc()
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    label = st.AddShape(shape)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

    writer = IGESCAFControl_Writer()
    writer.Transfer(doc)
    buf = io.BytesIO()
    ok = writer.Write(buf)
    if not ok:
        raise GeomError("IGES_WRITE_FAILED", "Failed to write IGES data", 422)
    return buf.getvalue()
