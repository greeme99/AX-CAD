"""FN-13 tessellation: per-face arrays ready for a Three.js BufferGeometry."""

from typing import Any

from OCP.BRep import BRep_Tool
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.serialize import from_brep

DEFLECTION_MM = 0.1
ANGULAR_RAD = 0.5


def tessellate(shape: TopoDS_Shape) -> dict[str, Any]:
    BRepMesh_IncrementalMesh(shape, DEFLECTION_MM, False, ANGULAR_RAD, True)
    faces: list[dict[str, Any]] = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())  # OCCT 8: TopoDS is a namespace -> no _s suffix
        ex.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
        trsf, rev = loc.Transformation(), face.Orientation() == TopAbs_REVERSED
        s = -1.0 if rev else 1.0  # reversed face: flip normals and winding
        pos: list[float] = []
        nrm: list[float] = []
        idx: list[int] = []
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            n = tri.Normal(i).Transformed(trsf)
            pos += [p.X(), p.Y(), p.Z()]
            nrm += [s * n.X(), s * n.Y(), s * n.Z()]
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            idx += [a - 1, c - 1, b - 1] if rev else [a - 1, b - 1, c - 1]
        faces.append({"face_index": len(faces), "positions": pos, "normals": nrm, "indices": idx})
    return {"faces": faces, "triangle_count": sum(len(f["indices"]) // 3 for f in faces)}


def mesh_job(brep: bytes) -> dict[str, Any]:
    """Worker entry point."""
    return tessellate(from_brep(brep))
