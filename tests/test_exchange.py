"""S7 kernel tests: STEP AP242 / IGES exchange (FN-12) and per-part metrics (FN-15). TC-49..53."""

import io
import math
import re

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TopAbs import TopAbs_FACE
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from core.geometry.errors import GeomError
from core.geometry.exchange import _compound, _doc, export_model, import_model
from core.geometry.metrics import measure

REL = 1e-6


def box():
    return BRepPrimAPI_MakeBox(10, 20, 30).Shape()


def cyl():
    return BRepPrimAPI_MakeCylinder(5, 10).Shape()


def assembly_step(pins=3):
    """ASM = BOX x1 + PIN x pins, written as STEP through XCAF (stand-in for G1 sample files)."""
    doc = _doc()
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    b, p = tool.AddShape(box(), False), tool.AddShape(cyl(), False)
    TDataStd_Name.Set_s(b, TCollection_ExtendedString("BRACKET"))
    TDataStd_Name.Set_s(p, TCollection_ExtendedString("PIN"))
    asm = tool.NewShape()
    TDataStd_Name.Set_s(asm, TCollection_ExtendedString("ASM"))

    def at(x):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(x, 0, 40))
        return TopLoc_Location(t)

    tool.AddComponent(asm, b, at(0))
    for i in range(pins):
        tool.AddComponent(asm, p, at(20 * i))
    tool.UpdateAssemblies()
    w, buf = STEPCAFControl_Writer(), io.BytesIO()
    assert w.Transfer(doc, STEPControl_AsIs) and w.WriteStream(buf) == IFSelect_RetDone
    return buf.getvalue()


def faces_only(shape):
    """Compound of the faces of a solid: a surface model like many IGES files."""
    from OCP.TopExp import TopExp_Explorer

    faces, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        faces.append(ex.Current())
        ex.Next()
    return _compound(faces)


def test_tc49_step_round_trip_preserves_volume_area_and_names():
    data = export_model([("Extrude001", box()), ("Revolve002", cyl())], "STEP")
    assert b"AP242" in data.upper() and data.startswith(b"ISO-10303-21;")
    _, m = import_model(data, "STEP")
    expect = measure(box())["volume_mm3"] + measure(cyl())["volume_mm3"]
    assert m["volume_mm3"] == pytest.approx(expect, rel=REL)
    area = measure(box())["surface_area_mm2"] + measure(cyl())["surface_area_mm2"]
    assert m["surface_area_mm2"] == pytest.approx(area, rel=REL)
    assert sorted(p["name"] for p in m["parts"]) == ["Extrude001", "Revolve002"]
    assert m["warnings"] == [] and m["solids"] == 2


def test_tc50_assembly_tree_and_instance_counts():
    _, m = import_model(assembly_step(pins=3), "STEP")
    assert m["part_count"] == 2 and m["instance_count"] == 4
    by = {p["name"]: p for p in m["parts"]}
    assert by["PIN"]["instance_count"] == 3 and by["BRACKET"]["instance_count"] == 1
    assert by["PIN"]["volume_mm3"] == pytest.approx(math.pi * 25 * 10, rel=REL)
    [root] = m["assembly_tree"]
    assert root["name"] == "ASM" and root["kind"] == "ASSEMBLY"
    assert [c["name"] for c in root["children"]] == ["BRACKET", "PIN", "PIN", "PIN"]
    # the compound carries every located instance
    assert m["volume_mm3"] == pytest.approx(6000 + 3 * math.pi * 25 * 10, rel=REL)
    assert m["bbox"]["min"][2] == pytest.approx(40)


@pytest.mark.parametrize(
    "data, code",
    [
        (b"", "STEP_INVALID_FILE"),
        (b"hello world", "STEP_INVALID_FILE"),
        (b"ISO-10303-21;\nHEADER;\ngarbage", "STEP_READ_FAILED"),
        (None, "STEP_EMPTY"),  # valid header, empty DATA section
        (
            b"ISO-10303-21;\nDATA;\n#1=DOCUMENT_FILE('x','../../etc/passwd',$,#2,'',$);\n",
            "STEP_INVALID_FILE",
        ),
        # bypass attempts: comment before "(", lower case, comment opener hidden in a string
        (b"ISO-10303-21;\nDATA;\n#1=DOCUMENT_FILE/*x*/('x','/etc/passwd');\n", "STEP_INVALID_FILE"),
        (b"ISO-10303-21;\nDATA;\n#1=document_file('x','/etc/passwd');\n", "STEP_INVALID_FILE"),
        (b"ISO-10303-21;\n#1=A('/*');#2=EXTERNAL_SOURCE/*x*/(('f.stp'));\n", "STEP_INVALID_FILE"),
    ],
)
def test_tc51_bad_step_files(data, code):
    if data is None:
        full = export_model([("B", box())], "STEP")
        data = re.sub(rb"DATA;.*?ENDSEC;", b"DATA;\nENDSEC;", full, flags=re.DOTALL)
    with pytest.raises(GeomError) as e:
        import_model(data, "STEP")
    assert e.value.code == code


def test_tc52_iges_round_trip_and_sewing():
    data = export_model([("BOX", box()), ("PIN", cyl())], "IGES")
    _, m = import_model(data, "IGES")
    assert m["solids"] == 2 and m["warnings"] == []
    assert m["volume_mm3"] == pytest.approx(6000 + math.pi * 250, rel=REL)
    # surface-only IGES: faces are sewn into a closed solid, with a warning
    _, m = import_model(export_model([("SKIN", faces_only(box()))], "IGES"), "IGES")
    assert m["warnings"] == ["SEWN"] and m["solids"] == 1
    assert m["volume_mm3"] == pytest.approx(6000, rel=REL)


def test_iges_rejects_bad_header_and_external_refs():
    with pytest.raises(GeomError) as e:
        import_model(b"ISO-10303-21;", "IGES")
    assert e.value.code == "STEP_INVALID_FILE"
    data = export_model([("BOX", box())], "IGES").decode()
    lines = data.splitlines()
    i = next(n for n, line in enumerate(lines) if line[72:73] == "D")
    lines[i] = "     416" + lines[i][8:]
    with pytest.raises(GeomError) as e:
        import_model("\n".join(lines).encode(), "IGES")
    assert e.value.code == "STEP_INVALID_FILE" and "External" in e.value.message


def test_tc53_metrics_match_analytic_solutions():
    _, m = import_model(
        export_model([("CUBE", BRepPrimAPI_MakeBox(10, 10, 10).Shape())], "STEP"), "STEP"
    )
    assert m["volume_mm3"] == pytest.approx(1000, rel=REL)
    assert m["surface_area_mm2"] == pytest.approx(600, rel=REL)
    assert m["bbox"]["size"] == pytest.approx([10, 10, 10], rel=REL)
    _, m = import_model(export_model([("CYL", cyl())], "STEP"), "STEP")
    assert m["volume_mm3"] == pytest.approx(math.pi * 25 * 10, rel=REL)
    assert m["surface_area_mm2"] == pytest.approx(2 * math.pi * 25 + 2 * math.pi * 5 * 10, rel=REL)
