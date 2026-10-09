"""Shapes cross process boundaries and the disk cache only as BREP bytes (TopoDS_Shape is not picklable)."""

import io

from OCP.BinTools import BinTools, BinTools_FormatVersion
from OCP.TopoDS import TopoDS_Shape


def to_brep(shape: TopoDS_Shape) -> bytes:
    buf = io.BytesIO()
    # no triangulation (mesh is a separate artefact), pinned format version for cache stability
    BinTools.Write_s(
        shape, buf, False, False, BinTools_FormatVersion.BinTools_FormatVersion_VERSION_4
    )
    return buf.getvalue()


def from_brep(data: bytes) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    BinTools.Read_s(shape, io.BytesIO(data))
    return shape
