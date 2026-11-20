"""3D B-Rep feature auto-recognition for 2.5D CAM: holes & pockets (plan.md §24 C2)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.cam.mill25d import ensure_ccw, signed_area


@dataclass(frozen=True)
class RecognizedHole:
    x: float
    y: float
    diameter_mm: float
    depth_mm: float
    z_top: float
    z_bottom: float
    through: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": round(self.x, 6),
            "y": round(self.y, 6),
            "diameter_mm": round(self.diameter_mm, 6),
            "depth_mm": round(self.depth_mm, 6),
            "z_top": round(self.z_top, 6),
            "z_bottom": round(self.z_bottom, 6),
            "through": self.through,
        }


@dataclass(frozen=True)
class RecognizedPocket:
    loop: list[tuple[float, float]]
    depth_mm: float
    z_top: float
    z_floor: float
    area_mm2: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop": [[round(p[0], 6), round(p[1], 6)] for p in self.loop],
            "depth_mm": round(self.depth_mm, 6),
            "z_top": round(self.z_top, 6),
            "z_floor": round(self.z_floor, 6),
            "area_mm2": round(self.area_mm2, 4),
        }


def _shape_z_bounds(shape: TopoDS_Shape) -> tuple[float, float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    return float(box.CornerMin().Z()), float(box.CornerMax().Z())


def _wire_polygon_2d(wire: Any) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    wexp = BRepTools_WireExplorer(wire)
    while wexp.More():
        v = wexp.CurrentVertex()
        p = BRep_Tool.Pnt_s(v)
        xy = (float(p.X()), float(p.Y()))
        if not pts or math.hypot(xy[0] - pts[-1][0], xy[1] - pts[-1][1]) > 1e-6:
            pts.append(xy)
        wexp.Next()
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 1e-6:
        pts.pop()
    return pts


def _is_internal_cylinder(face: Any, surf: BRepAdaptor_Surface) -> bool:
    """Return True when the cylindrical face normal points inward toward the cylinder axis."""
    u_mid = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
    v_mid = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
    p = surf.Value(u_mid, v_mid)
    d1u = surf.DN(u_mid, v_mid, 1, 0)
    d1v = surf.DN(u_mid, v_mid, 0, 1)
    n = d1u.Crossed(d1v)
    if face.Orientation() == TopAbs_REVERSED:
        n.Reverse()
    axis_loc = surf.Cylinder().Axis().Location()
    # Radial vector from axis to surface point in XY
    rx = p.X() - axis_loc.X()
    ry = p.Y() - axis_loc.Y()
    # Inward normal has negative dot product with outward radial vector
    return (n.X() * rx + n.Y() * ry) < 0.0


def recognize_features(shape: TopoDS_Shape) -> dict[str, Any]:
    """Recognize vertical Z-axis cylindrical holes and planar-floor pockets from an OCCT shape."""
    z_min, z_max = _shape_z_bounds(shape)

    # 1. Collect vertical internal cylindrical faces, merging split half-cylinders by (x, y, r)
    hole_buckets: dict[tuple[int, int, int], list[tuple[float, float, float, float, float]]] = {}
    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More():
        face = TopoDS.Face(fexp.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            ax_dir = cyl.Axis().Direction()
            if abs(ax_dir.Z()) > 0.999 and _is_internal_cylinder(face, surf):
                loc = cyl.Axis().Location()
                r = float(cyl.Radius())
                fbox = Bnd_Box()
                BRepBndLib.Add_s(face, fbox)
                fzmin, fzmax = float(fbox.CornerMin().Z()), float(fbox.CornerMax().Z())
                key = (round(loc.X() * 1000), round(loc.Y() * 1000), round(r * 1000))
                hole_buckets.setdefault(key, []).append(
                    (float(loc.X()), float(loc.Y()), 2.0 * r, float(fzmin), float(fzmax))
                )
        fexp.Next()

    holes: list[RecognizedHole] = []
    for entries in hole_buckets.values():
        cx = sum(e[0] for e in entries) / len(entries)
        cy = sum(e[1] for e in entries) / len(entries)
        diam = sum(e[2] for e in entries) / len(entries)
        hz_min = min(e[3] for e in entries)
        hz_max = max(e[4] for e in entries)
        depth = hz_max - hz_min
        if depth > 1e-4:
            through = abs(hz_min - z_min) <= 1e-3 and abs(hz_max - z_max) <= 1e-3
            holes.append(
                RecognizedHole(
                    x=cx,
                    y=cy,
                    diameter_mm=diam,
                    depth_mm=depth,
                    z_top=hz_max,
                    z_bottom=hz_min,
                    through=through,
                )
            )
    holes.sort(key=lambda h: (round(h.x, 4), round(h.y, 4)))

    # 2. Collect horizontal upward-facing planar faces strictly between z_min and z_max (pocket floors)
    pockets: list[RecognizedPocket] = []
    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More():
        face = TopoDS.Face(fexp.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Plane:
            pln = surf.Plane()
            nz = float(pln.Axis().Direction().Z())
            if face.Orientation() == TopAbs_REVERSED:
                nz = -nz
            if nz > 0.999:
                zf = float(pln.Location().Z())
                if z_min + 1e-3 < zf < z_max - 1e-3:
                    # Exclude circular blind-hole bottoms (all edges on outer wire are circles)
                    outer_wire = BRepTools.OuterWire_s(face)
                    eexp = TopExp_Explorer(outer_wire, TopAbs_EDGE)
                    edge_count = 0
                    while eexp.More():
                        edge_count += 1
                        eexp.Next()
                    poly = _wire_polygon_2d(outer_wire)
                    if len(poly) >= 3 and edge_count >= 3:
                        ccw = ensure_ccw(poly)
                        area = abs(signed_area(ccw))
                        if area > 1e-3:
                            pockets.append(
                                RecognizedPocket(
                                    loop=ccw,
                                    depth_mm=z_max - zf,
                                    z_top=z_max,
                                    z_floor=zf,
                                    area_mm2=area,
                                )
                            )
        fexp.Next()

    pockets.sort(key=lambda p: -p.area_mm2)
    return {
        "z_min": round(z_min, 6),
        "z_max": round(z_max, 6),
        "holes": [h.to_dict() for h in holes],
        "pockets": [p.to_dict() for p in pockets],
    }
