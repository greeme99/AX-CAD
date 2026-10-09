"""FN-15 measurement. Units: mm, mm2, mm3."""

from typing import Any

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape


def count(shape: TopoDS_Shape, kind: Any) -> int:
    n, ex = 0, TopExp_Explorer(shape, kind)
    while ex.More():
        n, _ = n + 1, ex.Next()
    return n


def measure(shape: TopoDS_Shape) -> dict[str, Any]:
    vp, sp = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    BRepGProp.SurfaceProperties_s(shape, sp)
    box = Bnd_Box()
    # Add_s enlarges by tolerance and is loose on curved faces; AddOptimal is tight (quote bboxes)
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return {
        "volume_mm3": vp.Mass(),
        "surface_area_mm2": sp.Mass(),
        "bbox": {
            "min": [lo.X(), lo.Y(), lo.Z()],
            "max": [hi.X(), hi.Y(), hi.Z()],
            "size": [hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()],
        },
        "solids": count(shape, TopAbs_SOLID),
    }
