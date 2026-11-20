"""Track 9 Q2: assemblies — placements, mates and interference (plan.md §15.3).

A component is a part body placed by a rigid transform: translation t (mm) and rotation r, XYZ
Euler angles in degrees applied as R = Rz·Ry·Rx (rotate about world X, then Y, then Z). Mates tie
a face of one component to a face of another:

- COINCIDENT  planes touch, facing each other (flip: facing the same way); cylinders -> CONCENTRIC
- DISTANCE    planes face each other `value` mm apart (gap along a's outward normal)
- PARALLEL    plane normals / cylinder axes parallel (either way)
- ANGLE       plane normals at `value` degrees
- CONCENTRIC  cylinder axes on one line

The solver moves only free components, Levenberg-Marquardt from the current placements, so an
under-constrained assembly stays where it was put (as the 2D sketch solver, ADR-12). Each mate
touches two components, so its Jacobian columns are found by perturbing just those two (12
unknowns): cost grows with the mate count, not mates x components.
"""

import math
import time
from typing import Any

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import (
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Line,
    GeomAbs_Plane,
    GeomAbs_Sphere,
)
from OCP.gp import gp_Trsf
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.refs import resolve
from core.geometry.serialize import from_brep, to_brep

MATE_TYPES = (
    "COINCIDENT",
    "DISTANCE",
    "PARALLEL",
    "ANGLE",
    "CONCENTRIC",
    "TANGENT",
    "PERPENDICULAR",
    "LOCK",
    "SYMMETRIC",
    "WIDTH",
    "LIMIT_DISTANCE",
    "LIMIT_ANGLE",
)
MAX_COMPONENTS = 200
MAX_MATES = 600
SOLVED_TOL = 1e-7  # max |residual| of a satisfied mate (mm, or unit-vector components)
RANK_TOL = 1e-6
MAX_ITER = 100
STEP_H = 1e-7
SOLVE_BUDGET_S = 40.0  # under the worker timeout: stop iterating and report what was reached
MAX_PAIRS = 2000  # Boolean checks per interference run (each pair whose boxes overlap)
INTERFERENCE_BUDGET_S = 90.0
MAX_COORD = 1e6  # mm: a solution further out than this has diverged
INTERFERENCE_MM3 = 1e-6  # common volume above this is a clash; below it the parts only touch


# --- rotations ---------------------------------------------------------------------------------


def rot(r_deg: list[float] | tuple[float, ...]) -> np.ndarray:
    ax, ay, az = (math.radians(a) for a in r_deg)
    cx, sx, cy, sy, cz, sz = (
        math.cos(ax),
        math.sin(ax),
        math.cos(ay),
        math.sin(ay),
        math.cos(az),
        math.sin(az),
    )
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def euler(m: np.ndarray) -> list[float]:
    """XYZ Euler angles (deg) of a rotation matrix (R = Rz·Ry·Rx); gimbal lock puts it all on X."""
    sy = -m[2, 0]
    if abs(sy) < 1 - 1e-12:
        ay = math.asin(sy)
        ax = math.atan2(m[2, 1], m[2, 2])
        az = math.atan2(m[1, 0], m[0, 0])
    else:
        ay = math.copysign(math.pi / 2, sy)
        # cos(ay) = 0: only ax ± az is defined; az = 0 and R = Ry(±90)·Rx(ax)
        ax = math.atan2(math.copysign(1, sy) * m[0, 1], m[1, 1])
        az = 0.0
    return [round(math.degrees(a), 9) + 0.0 for a in (ax, ay, az)]


def _rodrigues(w: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(w))
    if th < 1e-15:
        return np.eye(3)
    k = w / th
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * kx + (1 - math.cos(th)) * kx @ kx


def trsf(t: list[float], r: list[float]) -> gp_Trsf:
    m = rot(r)
    tr = gp_Trsf()
    tr.SetValues(*m[0], t[0], *m[1], t[1], *m[2], t[2])
    return tr


def placed(shape: TopoDS_Shape, t: list[float], r: list[float]) -> TopoDS_Shape:
    return BRepBuilderAPI_Transform(shape, trsf(t, r), True).Shape()


# --- faces -------------------------------------------------------------------------------------


def _log_so3(m: np.ndarray) -> np.ndarray:
    """Rotation vector w (rad) such that exp([w]x) == m (SO(3) logarithm)."""
    tr = max(-1.0, min(3.0, float(np.trace(m))))
    cos_th = max(-1.0, min(1.0, (tr - 1.0) * 0.5))
    th = math.acos(cos_th)
    vee = np.array([m[2, 1] - m[1, 2], m[0, 2] - m[2, 0], m[1, 0] - m[0, 1]])
    if th < 1e-7:
        return 0.5 * vee
    if abs(math.pi - th) < 1e-4:
        # near 180 deg: extract axis from diagonal of (m + I) / 2
        diag = np.maximum(0.0, np.diag(m) + 1.0) * 0.5
        axis = np.sqrt(diag)
        if axis[0] >= axis[1] and axis[0] >= axis[2]:
            axis[1] = math.copysign(axis[1], m[0, 1] + m[1, 0])
            axis[2] = math.copysign(axis[2], m[0, 2] + m[2, 0])
        elif axis[1] >= axis[2]:
            axis[0] = math.copysign(axis[0], m[0, 1] + m[1, 0])
            axis[2] = math.copysign(axis[2], m[1, 2] + m[2, 1])
        else:
            axis[0] = math.copysign(axis[0], m[0, 2] + m[2, 0])
            axis[1] = math.copysign(axis[1], m[1, 2] + m[2, 1])
        norm = float(np.linalg.norm(axis))
        return (axis / norm) * th if norm > 1e-12 else np.zeros(3)
    return (th / (2.0 * math.sin(th))) * vee


def ref_geom(shape: TopoDS_Shape, sig: dict[str, Any], ref_kind: str = "FACE") -> dict[str, Any]:
    """Resolve a FACE (PLANE/CYLINDER/CONE/SPHERE), EDGE (LINE/CIRCLE), or VERTEX (POINT)
    signature in part coordinates into solver geometry."""
    if ref_kind == "VERTEX":
        pt = sig.get("point") or sig.get("mid")
        if not pt or len(pt) != 3:
            raise GeomError("ASSEMBLY_FACE_UNSUPPORTED", "Vertex reference needs a 3D point")
        target = np.asarray(pt, dtype=float)
        best_p: list[float] | None = None
        best_d = float("inf")
        ex = TopExp_Explorer(shape, TopAbs_VERTEX)
        while ex.More():
            vp = BRep_Tool.Pnt_s(TopoDS.Vertex(ex.Current()))
            cand = [vp.X(), vp.Y(), vp.Z()]
            d = float(np.linalg.norm(np.asarray(cand) - target))
            if d < best_d:
                best_d, best_p = d, cand
            ex.Next()
        if best_p is None:
            raise GeomError("ASSEMBLY_FACE_UNSUPPORTED", "Body has no vertices")
        return {"kind": "POINT", "p": best_p}

    if ref_kind == "EDGE":
        edge = TopoDS.Edge(resolve(shape, [sig], "EDGE")[0])
        c = BRepAdaptor_Curve(edge)
        if c.GetType() == GeomAbs_Line:
            lin = c.Line()
            u_mid = 0.5 * (c.FirstParameter() + c.LastParameter())
            pm = c.Value(u_mid)
            d = lin.Direction()
            return {"kind": "LINE", "p": [pm.X(), pm.Y(), pm.Z()], "n": [d.X(), d.Y(), d.Z()]}
        if c.GetType() == GeomAbs_Circle:
            circ = c.Circle()
            o, d = circ.Location(), circ.Axis().Direction()
            return {
                "kind": "CIRCLE",
                "p": [o.X(), o.Y(), o.Z()],
                "n": [d.X(), d.Y(), d.Z()],
                "radius": circ.Radius(),
            }
        raise GeomError("ASSEMBLY_FACE_UNSUPPORTED", "Only line and circle edges can be mated")

    face = TopoDS.Face(resolve(shape, [sig], "FACE")[0])
    s = BRepAdaptor_Surface(face)
    if s.GetType() == GeomAbs_Plane:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c, d = props.CentreOfMass(), s.Plane().Axis().Direction()
        n = [d.X(), d.Y(), d.Z()]
        if face.Orientation() == TopAbs_REVERSED:
            n = [-x for x in n]
        return {"kind": "PLANE", "p": [c.X(), c.Y(), c.Z()], "n": n}
    if s.GetType() == GeomAbs_Cylinder:
        ax = s.Cylinder().Axis()
        o, d = ax.Location(), ax.Direction()
        return {
            "kind": "CYLINDER",
            "p": [o.X(), o.Y(), o.Z()],
            "n": [d.X(), d.Y(), d.Z()],
            "radius": s.Cylinder().Radius(),
        }
    if s.GetType() == GeomAbs_Cone:
        cone = s.Cone()
        ax = cone.Axis()
        o, d = cone.Apex(), ax.Direction()
        return {
            "kind": "CONE",
            "p": [o.X(), o.Y(), o.Z()],
            "n": [d.X(), d.Y(), d.Z()],
            "half_angle": cone.SemiAngle(),
        }
    if s.GetType() == GeomAbs_Sphere:
        sph = s.Sphere()
        o = sph.Location()
        return {"kind": "SPHERE", "p": [o.X(), o.Y(), o.Z()], "radius": sph.Radius()}
    raise GeomError(
        "ASSEMBLY_FACE_UNSUPPORTED",
        "Only planar, cylindrical, conical, and spherical faces can be mated",
    )


def face_geom(shape: TopoDS_Shape, sig: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible wrapper around ref_geom(..., ref_kind='FACE')."""
    return ref_geom(shape, sig, "FACE")


def faces_job(brep: bytes, sigs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shape = from_brep(brep)
    return [face_geom(shape, s) for s in sigs]


def refs_job(brep: bytes, items: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    shape = from_brep(brep)
    return [ref_geom(shape, sig, rk) for rk, sig in items]


# --- solver ------------------------------------------------------------------------------------


_AXIAL = ("CYLINDER", "CONE", "LINE", "CIRCLE")
_DIRECTED = ("PLANE", *_AXIAL)
_POINTLIKE = ("POINT", "SPHERE")


def _mate_components(m: dict[str, Any]) -> set[Any]:
    out = {m["a"]["component"], m["b"]["component"]}
    extra = m.get("extra") or {}
    if isinstance(extra.get("plane"), dict) and "component" in extra["plane"]:
        out.add(extra["plane"]["component"])
    for key in ("a2", "b2"):
        if isinstance(extra.get(key), dict) and "component" in extra[key]:
            out.add(extra[key]["component"])
    return out


def _check(m: dict[str, Any], i: int) -> None:
    ka, kb = m["a"]["kind"], m["b"]["kind"]
    t = m["type"]
    if t not in MATE_TYPES:
        raise GeomError("ASSEMBLY_MATE_INVALID", f"Unknown mate type {t}", details={"mate": i})
    if m["a"]["component"] == m["b"]["component"]:
        raise GeomError(
            "ASSEMBLY_MATE_INVALID", "A mate joins two different components", details={"mate": i}
        )
    planes = ka == kb == "PLANE"
    axials = ka in _AXIAL and kb in _AXIAL
    extra = m.get("extra") or {}
    if t == "COINCIDENT":
        ok = (
            planes
            or axials
            or (ka in _POINTLIKE and kb in _POINTLIKE)
            or (ka in _POINTLIKE and kb in ("PLANE", *_AXIAL))
            or (kb in _POINTLIKE and ka in ("PLANE", *_AXIAL))
        )
    elif t == "DISTANCE":
        ok = (
            planes
            or (ka in _POINTLIKE and kb in _POINTLIKE)
            or (ka in _POINTLIKE and kb == "PLANE")
            or (kb in _POINTLIKE and ka == "PLANE")
        )
    elif t == "ANGLE" or t == "PARALLEL":
        ok = ka in _DIRECTED and kb in _DIRECTED
    elif t == "CONCENTRIC":
        ok = axials
    elif t == "PERPENDICULAR":
        ok = ka in _DIRECTED and kb in _DIRECTED
    elif t == "TANGENT":
        ok = (
            (ka == "PLANE" and kb in ("CYLINDER", "SPHERE"))
            or (kb == "PLANE" and ka in ("CYLINDER", "SPHERE"))
            or (ka == "CYLINDER" and kb == "CYLINDER")
        )
    elif t == "LOCK":
        ok = True
    elif t == "SYMMETRIC":
        pl = extra.get("plane")
        ok = (
            isinstance(pl, dict)
            and pl.get("kind") == "PLANE"
            and (planes or (ka in _POINTLIKE and kb in _POINTLIKE))
        )
    elif t == "WIDTH":
        a2, b2 = extra.get("a2"), extra.get("b2")
        ok = (
            planes
            and isinstance(a2, dict)
            and a2.get("kind") == "PLANE"
            and isinstance(b2, dict)
            and b2.get("kind") == "PLANE"
        )
    elif t == "LIMIT_DISTANCE":
        vmin, vmax = m.get("value_min"), m.get("value_max")
        ok = (
            planes
            and vmin is not None
            and vmax is not None
            and math.isfinite(vmin)
            and math.isfinite(vmax)
            and vmin <= vmax
        )
    elif t == "LIMIT_ANGLE":
        vmin, vmax = m.get("value_min"), m.get("value_max")
        ok = (
            ka in _DIRECTED
            and kb in _DIRECTED
            and vmin is not None
            and vmax is not None
            and math.isfinite(vmin)
            and math.isfinite(vmax)
            and 0.0 <= vmin <= vmax <= 180.0
        )
    else:
        ok = False
    if not ok:
        raise GeomError(
            "ASSEMBLY_MATE_INVALID",
            f"{t} is not compatible with {ka} and {kb} (or missing required bounds/extra)",
            details={"mate": i},
        )


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # np.cross costs ~40x this on single 3-vectors (axis handling); the solver calls it a lot
    return np.array(
        [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    )


def _pt_n(
    side: dict[str, Any], pose: dict[Any, tuple[np.ndarray, np.ndarray]]
) -> tuple[np.ndarray, np.ndarray | None]:
    r, t = pose[side["component"]]
    p = r @ np.asarray(side["p"], dtype=float) + t
    n = r @ np.asarray(side["n"], dtype=float) if "n" in side and side["n"] is not None else None
    return p, n


def _residual(m: dict[str, Any], pose: dict[Any, tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    t = m["type"]
    ka, kb = m["a"]["kind"], m["b"]["kind"]
    pa, na = _pt_n(m["a"], pose)
    pb, nb = _pt_n(m["b"], pose)

    if t == "LOCK":
        ra, ta = pose[m["a"]["component"]]
        rb, tb = pose[m["b"]["component"]]
        extra = m.get("extra") or {}
        rel_t0 = np.asarray(extra.get("rel_t", [0.0, 0.0, 0.0]), dtype=float)
        rel_r0 = rot(extra.get("rel_r", [0.0, 0.0, 0.0]))
        rel_t = ra.T @ (tb - ta)
        rel_r = ra.T @ rb
        return np.concatenate([rel_t - rel_t0, _log_so3(rel_r @ rel_r0.T)])

    if t == "PERPENDICULAR":
        assert na is not None and nb is not None
        return np.array([float(na @ nb)])

    if t == "CONCENTRIC" or (t == "COINCIDENT" and ka in _AXIAL and kb in _AXIAL):
        assert na is not None and nb is not None
        return np.concatenate([_cross(na, nb), _cross(pb - pa, na)])

    if t == "COINCIDENT":
        if ka in _POINTLIKE and kb in _POINTLIKE:
            return pb - pa
        if ka in _POINTLIKE and kb == "PLANE":
            assert nb is not None
            return np.array([float((pa - pb) @ nb)])
        if kb in _POINTLIKE and ka == "PLANE":
            assert na is not None
            return np.array([float((pb - pa) @ na)])
        if ka in _POINTLIKE and kb in _AXIAL:
            assert nb is not None
            return _cross(pa - pb, nb)
        if kb in _POINTLIKE and ka in _AXIAL:
            assert na is not None
            return _cross(pb - pa, na)

    if t == "DISTANCE":
        offset = float(m.get("value") or 0.0)
        if ka in _POINTLIKE and kb in _POINTLIKE:
            return np.array([float(np.linalg.norm(pb - pa)) - offset])
        if ka in _POINTLIKE and kb == "PLANE":
            assert nb is not None
            return np.array([float((pa - pb) @ nb) - offset])
        if kb in _POINTLIKE and ka == "PLANE":
            assert na is not None
            return np.array([float((pb - pa) @ na) - offset])

    if t == "PARALLEL":
        assert na is not None and nb is not None
        return _cross(na, nb)

    if t == "ANGLE":
        assert na is not None and nb is not None
        return np.array([float(na @ nb) - math.cos(math.radians(m.get("value") or 0.0))])

    if t == "TANGENT":
        s = -1.0 if m.get("flip") else 1.0
        if ka == "PLANE" and kb == "CYLINDER":
            assert na is not None and nb is not None
            r_cyl = float(m["b"]["radius"])
            return np.array([float(na @ nb), float((pb - pa) @ na) - s * r_cyl])
        if kb == "PLANE" and ka == "CYLINDER":
            assert na is not None and nb is not None
            r_cyl = float(m["a"]["radius"])
            return np.array([float(nb @ na), float((pa - pb) @ nb) - s * r_cyl])
        if ka == "PLANE" and kb == "SPHERE":
            assert na is not None
            return np.array([float((pb - pa) @ na) - s * float(m["b"]["radius"])])
        if kb == "PLANE" and ka == "SPHERE":
            assert nb is not None
            return np.array([float((pa - pb) @ nb) - s * float(m["a"]["radius"])])
        # CYLINDER - CYLINDER
        assert na is not None and nb is not None
        ra_c, rb_c = float(m["a"]["radius"]), float(m["b"]["radius"])
        target_d = abs(ra_c - rb_c) if m.get("flip") else (ra_c + rb_c)
        perp_v = (pb - pa) - float((pb - pa) @ na) * na
        return np.concatenate([_cross(na, nb), [float(np.linalg.norm(perp_v)) - target_d]])

    if t == "SYMMETRIC":
        pm, nm = _pt_n(m["extra"]["plane"], pose)
        assert nm is not None
        mid_err = float((0.5 * (pa + pb) - pm) @ nm)
        if na is not None and nb is not None:
            # Reflection of na across plane normal nm: na_ref = na - 2 (na·nm) nm
            na_ref = na - 2.0 * float(na @ nm) * nm
            want = -1.0 if m.get("flip") else 1.0
            orient = _cross(na_ref, nb) if float(na_ref @ nb) * want > 0 else nb - want * na_ref
            return np.concatenate([orient, [mid_err]])
        # Point-point symmetry: midpoint on plane AND vector (pb - pa) parallel to nm
        return np.concatenate([_cross(pb - pa, nm), [mid_err]])

    if t == "WIDTH":
        pa2, _ = _pt_n(m["extra"]["a2"], pose)
        pb2, _ = _pt_n(m["extra"]["b2"], pose)
        assert na is not None and nb is not None
        mid_a = 0.5 * (pa + pa2)
        mid_b = 0.5 * (pb + pb2)
        return np.concatenate([_cross(na, nb), [float((mid_b - mid_a) @ na)]])

    if t == "LIMIT_DISTANCE":
        assert na is not None and nb is not None
        want = 1.0 if m.get("flip") else -1.0
        facing = _cross(na, nb) if float(na @ nb) * want > 0 else nb - want * na
        d = float((pb - pa) @ na)
        vmin, vmax = float(m["value_min"]), float(m["value_max"])
        gap = d - vmin if d < vmin else d - vmax if d > vmax else 0.0
        return np.concatenate([facing, [gap]])

    if t == "LIMIT_ANGLE":
        assert na is not None and nb is not None
        cos_th = max(-1.0, min(1.0, float(na @ nb)))
        th = math.degrees(math.acos(cos_th))
        vmin, vmax = float(m["value_min"]), float(m["value_max"])
        err = (
            math.radians(th - vmin) if th < vmin else math.radians(th - vmax) if th > vmax else 0.0
        )
        return np.array([err])

    # Default plane-plane COINCIDENT / DISTANCE
    assert na is not None and nb is not None
    want = 1.0 if m.get("flip") else -1.0
    facing = _cross(na, nb) if float(na @ nb) * want > 0 else nb - want * na
    offset = (m.get("value") or 0.0) if t == "DISTANCE" else 0.0
    gap = float((pb - pa) @ na) - offset
    return np.concatenate([facing, [gap]])


def _perp(n: np.ndarray) -> np.ndarray:
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    v = _cross(n, a)
    return v / float(np.linalg.norm(v))


def _preflip(
    mates: list[dict[str, Any]], pose: dict[Any, tuple[np.ndarray, np.ndarray]], free: set[Any]
) -> None:
    """A facing mate whose planes point the wrong way is a saddle for the solver (no rotation
    direction is downhill when they are exactly parallel): turn the free part over first, 180 deg
    about an axis in its face, keeping the face centre in place (as CAD flips a part on a mate)."""
    for m in mates:
        if (
            m["type"] not in ("COINCIDENT", "DISTANCE", "LIMIT_DISTANCE")
            or m["a"]["kind"] != "PLANE"
            or m["b"]["kind"] != "PLANE"
        ):
            continue
        want = 1.0 if m.get("flip") else -1.0
        ra, _ = pose[m["a"]["component"]]
        side = "b" if m["b"]["component"] in free else "a" if m["a"]["component"] in free else None
        if side is None:
            continue
        rb, _ = pose[m["b"]["component"]]
        na, nb = ra @ np.asarray(m["a"]["n"]), rb @ np.asarray(m["b"]["n"])
        if float(na @ nb) * want >= 0:
            continue
        r0, t0 = pose[m[side]["component"]]
        n_side = r0 @ np.asarray(m[side]["n"])
        centre = r0 @ np.asarray(m[side]["p"]) + t0
        turn = _rodrigues(_perp(n_side) * math.pi)
        r1 = turn @ r0
        pose[m[side]["component"]] = (r1, centre - r1 @ np.asarray(m[side]["p"]))


def _solve_core(
    components: list[dict[str, Any]],
    active_mates: list[dict[str, Any]],
    budget_s: float = SOLVE_BUDGET_S,
) -> tuple[dict[Any, tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray, list[int], list[Any]]:
    """Core LM solver on active (non-suppressed) mates."""
    pose: dict[Any, tuple[np.ndarray, np.ndarray]] = {
        c["id"]: (rot(c["r"]), np.asarray(c["t"], dtype=float)) for c in components
    }
    free = [c["id"] for c in components if not c.get("fixed")]
    col = {cid: 6 * k for k, cid in enumerate(free)}
    n = 6 * len(free)
    size = {cid: 1.0 for cid in pose}
    for m in active_mates:
        for side in ("a", "b"):
            c = m[side]["component"]
            size[c] = max(size[c], float(np.linalg.norm(m[side]["p"])))

    def moved(
        cur: dict[Any, tuple[np.ndarray, np.ndarray]], x: np.ndarray
    ) -> dict[Any, tuple[np.ndarray, np.ndarray]]:
        out = dict(cur)
        for cid, j in col.items():
            r0, t0 = cur[cid]
            out[cid] = (_rodrigues(x[j + 3 : j + 6] / size[cid]) @ r0, t0 + x[j : j + 3])
        return out

    _preflip(active_mates, pose, set(free))
    rows = [len(_residual(m, pose)) for m in active_mates]
    start = [int(v) for v in np.cumsum([0, *rows])]

    def resid(cur: dict[Any, tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
        return (
            np.concatenate([_residual(m, cur) for m in active_mates])
            if active_mates
            else np.zeros(0)
        )

    mate_comps = [_mate_components(m) for m in active_mates]
    _eye3 = np.eye(3)

    def eval_all(cur: dict[Any, tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
        r = resid(cur)
        jac = np.zeros((len(r), n))
        for k, m in enumerate(active_mates):
            r_k = r[start[k] : start[k + 1]]
            for cid in mate_comps[k]:
                if cid not in col:
                    continue
                j = col[cid]
                r0, t0 = cur[cid]
                inv_s = STEP_H / size[cid]
                for d in range(3):
                    t1 = t0.copy()
                    t1[d] += STEP_H
                    cur[cid] = (r0, t1)
                    jac[start[k] : start[k + 1], j + d] = (_residual(m, cur) - r_k) / STEP_H
                for d in range(3):
                    cur[cid] = (_rodrigues(_eye3[d] * inv_s) @ r0, t0)
                    jac[start[k] : start[k + 1], j + 3 + d] = (_residual(m, cur) - r_k) / STEP_H
                cur[cid] = (r0, t0)
        return r, jac

    r, jac = eval_all(pose)
    cost = float(r @ r)
    lam = 0.0
    deadline = time.monotonic() + budget_s
    for _ in range(MAX_ITER):
        if not n or np.max(np.abs(r), initial=0.0) < SOLVED_TOL * 1e-2:
            break
        if budget_s <= 0.0 or time.monotonic() > deadline:
            break
        if len(r) <= n:
            jj = jac @ jac.T
            mu = max(lam, 1e-12 * max(1.0, float(np.trace(jj)) / len(r)))
            step = jac.T @ np.linalg.solve(jj + mu * np.eye(len(r)), -r)
        else:
            jtj = jac.T @ jac
            mu = max(lam, 1e-12 * max(1.0, float(np.trace(jtj)) / n))
            step = np.linalg.solve(jtj + mu * np.eye(n), -(jac.T @ r))
        accepted = False
        for alpha in (1.0, 0.5, 0.25, 0.125, 0.0625):
            cand = moved(pose, alpha * step)
            r2 = resid(cand)
            cost2 = float(r2 @ r2)
            if cost2 < cost:
                pose, cost, accepted = cand, cost2, True
                r, jac = eval_all(pose)
                break
        if accepted:
            lam = lam / 10 if lam > 1e-9 else 0.0
            if float(np.linalg.norm(step)) < 1e-14:
                break
        else:
            lam = max(lam * 10, 1e-3)
            if lam > 1e12:
                break
    return pose, r, jac, start, free


def _dof_by_component(
    components: list[dict[str, Any]], free: list[Any], jac: np.ndarray
) -> tuple[int, dict[Any, int]]:
    """Total DOF and per-component remaining DOF via Jacobian nullspace projection."""
    n = 6 * len(free)
    if not n:
        return 0, {c["id"]: 0 for c in components}
    if jac.shape[0] == 0:
        free_set = set(free)
        return n, {c["id"]: (6 if c["id"] in free_set else 0) for c in components}
    col = {cid: 6 * k for k, cid in enumerate(free)}
    if len(free) > 50:
        rank = int(np.linalg.matrix_rank(jac, tol=RANK_TOL))
        per_comp: dict[Any, int] = {}
        for c in components:
            cid = c["id"]
            if cid not in col:
                per_comp[cid] = 0
            else:
                j = col[cid]
                nz = np.any(np.abs(jac[:, j : j + 6]) > RANK_TOL, axis=1)
                block = jac[nz, j : j + 6]
                r_c = int(np.linalg.matrix_rank(block, tol=RANK_TOL)) if block.shape[0] else 0
                per_comp[cid] = max(0, 6 - r_c)
        return n - rank, per_comp
    _, s, vh = np.linalg.svd(jac, full_matrices=True)
    tol = max(RANK_TOL, float(s[0]) * 1e-7) if len(s) else RANK_TOL
    rank = int(np.sum(s > tol))
    null_basis = vh[rank:]  # shape (n - rank, n)
    per_comp = {}
    for c in components:
        cid = c["id"]
        if cid not in col or null_basis.shape[0] == 0:
            per_comp[cid] = 0
        else:
            j = col[cid]
            block = null_basis[:, j : j + 6]
            per_comp[cid] = int(np.linalg.matrix_rank(block, tol=1e-6))
    return n - rank, per_comp


def solve(
    components: list[dict[str, Any]],
    mates: list[dict[str, Any]],
    diagnose: bool = True,
) -> dict[str, Any]:
    """components: {id, t, r, fixed}; mates: {id?, type, a, b, value, value_min, value_max, flip,
    suppressed, extra}. Returns new placements, mate residuals/statuses, total & per-component DOF,
    and overconstrained diagnostics."""
    if len(components) > MAX_COMPONENTS:
        raise GeomError("ASSEMBLY_TOO_LARGE", f"At most {MAX_COMPONENTS} components")
    if len(mates) > MAX_MATES:
        raise GeomError("ASSEMBLY_TOO_LARGE", f"At most {MAX_MATES} mates")
    ids = {c["id"] for c in components}
    active_idx: list[int] = []
    active_mates: list[dict[str, Any]] = []
    for i, m in enumerate(mates):
        if m.get("suppressed"):
            continue
        _check(m, i)
        if not _mate_components(m) <= ids:
            raise GeomError(
                "ASSEMBLY_MATE_INVALID", "Mate refers to a missing component", details={"mate": i}
            )
        active_idx.append(i)
        active_mates.append(m)

    pose, r, jac, start, free = _solve_core(components, active_mates, SOLVE_BUDGET_S)
    for m_, t_ in pose.values():
        if not (
            np.all(np.isfinite(m_)) and np.all(np.isfinite(t_)) and np.max(np.abs(t_)) <= MAX_COORD
        ):
            raise GeomError(
                "ASSEMBLY_SOLVE_DIVERGED", "The mates could not be solved; remove the last one"
            )

    out_mates: list[dict[str, Any]] = [{"residual": 0.0, "status": "SUPPRESSED"} for _ in mates]
    failed_active: list[int] = []
    for k, orig_i in enumerate(active_idx):
        res = float(np.max(np.abs(r[start[k] : start[k + 1]]), initial=0.0))
        ok = res < SOLVED_TOL
        out_mates[orig_i] = {"residual": res, "status": "OK" if ok else "FAILED"}
        if not ok:
            failed_active.append(k)

    total_dof, dof_by_comp = _dof_by_component(components, free, jac)

    # Overconstrained diagnostics: leave-one-out on active mates when any mate failed (max 20, 5s)
    conflicting: list[Any] = []
    if diagnose and failed_active:
        diag_deadline = time.monotonic() + 5.0
        # Prioritize failed mates first, then remaining active mates, capped at 20 trials
        order = failed_active + [k for k in range(len(active_mates)) if k not in failed_active]
        for k in order[:20]:
            if time.monotonic() > diag_deadline:
                break
            sub = [m for idx_k, m in enumerate(active_mates) if idx_k != k]
            _, sub_r, _, _, _ = _solve_core(components, sub, budget_s=0.5)
            if float(np.max(np.abs(sub_r), initial=0.0)) < SOLVED_TOL:
                orig_m = active_mates[k]
                conflicting.append(orig_m.get("id", active_idx[k]))

    placements = {
        cid: {"t": [round(float(v), 9) + 0.0 for v in t], "r": euler(m)}
        for cid, (m, t) in pose.items()
    }
    return {
        "placements": placements,
        "mates": out_mates,
        "dof": total_dof,
        "dof_by_component": dof_by_comp,
        "conflicting_mate_ids": conflicting,
        "overconstrained": bool(failed_active),
    }


# --- interference ------------------------------------------------------------------------------


def _box(shape: TopoDS_Shape) -> tuple[list[float], list[float]]:
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    lo, hi = b.CornerMin(), b.CornerMax()
    return [lo.X(), lo.Y(), lo.Z()], [hi.X(), hi.Y(), hi.Z()]


def _overlap(
    a: tuple[list[float], list[float]], b: tuple[list[float], list[float]], gap: float
) -> bool:
    return all(a[0][i] - gap <= b[1][i] and b[0][i] - gap <= a[1][i] for i in range(3))


def interference_job(
    parts: dict[str, bytes],
    comps: list[dict[str, Any]],
    clearance: float = 0.0,
    max_pairs: int = MAX_PAIRS,
    budget_s: float = INTERFERENCE_BUDGET_S,
) -> dict[str, Any]:
    """parts: body key -> BREP; comps: {id, part, t, r}. Pairs whose boxes overlap are cut with a
    Boolean common: volume > INTERFERENCE_MM3 is a clash; else, when `clearance` > 0, parts closer
    than it are listed (0 = touching)."""
    shapes = {k: from_brep(v) for k, v in parts.items()}
    placed_ = [(c["id"], placed(shapes[c["part"]], c["t"], c["r"])) for c in comps]
    boxes = [_box(s) for _, s in placed_]
    clashes, near, checked, truncated = [], [], 0, False
    deadline = time.monotonic() + budget_s
    for i in range(len(placed_)):
        for j in range(i + 1, len(placed_)):
            if not _overlap(boxes[i], boxes[j], clearance):
                continue
            if checked >= max_pairs or time.monotonic() > deadline:
                truncated = True  # partial result: the user narrows it down, the pool is not killed
                break
            checked += 1
            (ia, sa), (ib, sb) = placed_[i], placed_[j]
            common = BRepAlgoAPI_Common(sa, sb)
            vol = 0.0
            if common.IsDone():
                props = GProp_GProps()
                BRepGProp.VolumeProperties_s(common.Shape(), props)
                vol = props.Mass()
                if vol > INTERFERENCE_MM3:
                    c = props.CentreOfMass()
                    clashes.append(
                        {"a": ia, "b": ib, "volume_mm3": vol, "center": [c.X(), c.Y(), c.Z()]}
                    )
                    continue
            if clearance > 0:
                d = BRepExtrema_DistShapeShape(sa, sb)
                if d.IsDone() and d.Value() < clearance:
                    near.append({"a": ia, "b": ib, "distance_mm": d.Value()})
        if truncated:
            break
    return {
        "clashes": clashes,
        "near": near,
        "pairs_checked": checked,
        "components": len(comps),
        "truncated": truncated,
    }


def compound_job(parts: dict[str, bytes], comps: list[dict[str, Any]]) -> bytes:
    """All placed bodies as one compound (STEP export of the assembly)."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    shapes = {k: from_brep(v) for k, v in parts.items()}
    comp, bld = TopoDS_Compound(), BRep_Builder()
    bld.MakeCompound(comp)
    for c in comps:
        bld.Add(comp, placed(shapes[c["part"]], c["t"], c["r"]))
    return to_brep(comp)


def export_assembly_job(parts: dict[str, bytes], comps: list[dict[str, Any]], fmt: str) -> bytes:
    """STEP/IGES/STL/BREP of the placed bodies, one named product per component."""
    from core.geometry.exchange import export_model

    shapes = {k: from_brep(v) for k, v in parts.items()}
    return export_model(
        [(c["name"], placed(shapes[c["part"]], c["t"], c["r"])) for c in comps], fmt
    )
