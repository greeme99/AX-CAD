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
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.gp import gp_Trsf
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.refs import resolve
from core.geometry.serialize import from_brep, to_brep

MATE_TYPES = ("COINCIDENT", "DISTANCE", "PARALLEL", "ANGLE", "CONCENTRIC")
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


def face_geom(shape: TopoDS_Shape, sig: dict[str, Any]) -> dict[str, Any]:
    """A picked face (part coordinates) as a plane {p, n: outward normal} or a cylinder {p, n:
    axis}; other surfaces cannot be mated."""
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
    raise GeomError("ASSEMBLY_FACE_UNSUPPORTED", "Only planar and cylindrical faces can be mated")


def faces_job(brep: bytes, sigs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shape = from_brep(brep)
    return [face_geom(shape, s) for s in sigs]


# --- solver ------------------------------------------------------------------------------------


def _check(m: dict[str, Any], i: int) -> None:
    ka, kb = m["a"]["kind"], m["b"]["kind"]
    t = m["type"]
    if t not in MATE_TYPES:
        raise GeomError("ASSEMBLY_MATE_INVALID", f"Unknown mate type {t}", details={"mate": i})
    planes = ka == kb == "PLANE"
    cyls = ka == kb == "CYLINDER"
    ok = {
        "COINCIDENT": planes or cyls,
        "DISTANCE": planes,
        "ANGLE": planes,
        "PARALLEL": ka == kb,
        "CONCENTRIC": cyls,
    }[t]
    if not ok:
        raise GeomError(
            "ASSEMBLY_MATE_INVALID",
            f"{t} needs {'two cylinders' if t == 'CONCENTRIC' else 'two planes' if t in ('DISTANCE', 'ANGLE') else 'two planes or two cylinders'}",
            details={"mate": i},
        )
    if m["a"]["component"] == m["b"]["component"]:
        raise GeomError(
            "ASSEMBLY_MATE_INVALID", "A mate joins two different components", details={"mate": i}
        )


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # np.cross costs ~40x this on single 3-vectors (axis handling); the solver calls it a lot
    return np.array(
        [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    )


def _residual(m: dict[str, Any], pose: dict[Any, tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    ra, ta = pose[m["a"]["component"]]
    rb, tb = pose[m["b"]["component"]]
    pa, na = ra @ np.asarray(m["a"]["p"]) + ta, ra @ np.asarray(m["a"]["n"])
    pb, nb = rb @ np.asarray(m["b"]["p"]) + tb, rb @ np.asarray(m["b"]["n"])
    t = m["type"]
    if t == "CONCENTRIC" or (t == "COINCIDENT" and m["a"]["kind"] == "CYLINDER"):
        return np.concatenate([_cross(na, nb), _cross(pb - pa, na)])
    if t == "PARALLEL":
        return _cross(na, nb)
    if t == "ANGLE":
        return np.array([float(na @ nb) - math.cos(math.radians(m.get("value") or 0.0))])
    # normals opposite (or the same, flip): on the right side the cross product is linear near the
    # solution; na + nb there is not (its part along the normal is second order, which made
    # Gauss-Newton ask for huge steps), so it only pulls a part back from the wrong side
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
        if m["type"] not in ("COINCIDENT", "DISTANCE") or m["a"]["kind"] != "PLANE":
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


def solve(components: list[dict[str, Any]], mates: list[dict[str, Any]]) -> dict[str, Any]:
    """components: {id, t, r, fixed}; mates: {type, a: {component, kind, p, n}, b, value, flip}
    with face geometry in part coordinates. Returns new placements (free ones only change), each
    mate's residual and status, and the remaining degrees of freedom."""
    if len(components) > MAX_COMPONENTS:
        raise GeomError("ASSEMBLY_TOO_LARGE", f"At most {MAX_COMPONENTS} components")
    if len(mates) > MAX_MATES:
        raise GeomError("ASSEMBLY_TOO_LARGE", f"At most {MAX_MATES} mates")
    ids = {c["id"] for c in components}
    for i, m in enumerate(mates):
        _check(m, i)
        if not {m["a"]["component"], m["b"]["component"]} <= ids:
            raise GeomError(
                "ASSEMBLY_MATE_INVALID", "Mate refers to a missing component", details={"mate": i}
            )
    pose: dict[Any, tuple[np.ndarray, np.ndarray]] = {
        c["id"]: (rot(c["r"]), np.asarray(c["t"], dtype=float)) for c in components
    }
    free = [c["id"] for c in components if not c.get("fixed")]
    col = {cid: 6 * k for k, cid in enumerate(free)}
    n = 6 * len(free)
    # rotation unknowns are scaled by the part's size (mm per rad): a minimum-norm step then fixes a
    # gap by moving the part, not by tilting it about a lever arm
    size = {cid: 1.0 for cid in pose}
    for m in mates:
        for side in ("a", "b"):
            c = m[side]["component"]
            size[c] = max(size[c], float(np.linalg.norm(m[side]["p"])))

    def moved(
        cur: dict[Any, tuple[np.ndarray, np.ndarray]], x: np.ndarray
    ) -> dict[Any, tuple[np.ndarray, np.ndarray]]:
        """Poses after small increments x (translation, rotation vector) about the current ones."""
        out = dict(cur)
        for cid, j in col.items():
            r0, t0 = cur[cid]
            out[cid] = (_rodrigues(x[j + 3 : j + 6] / size[cid]) @ r0, t0 + x[j : j + 3])
        return out

    _preflip(mates, pose, set(free))
    rows = [len(_residual(m, pose)) for m in mates]
    start = np.cumsum([0, *rows])

    def resid(cur: dict[Any, tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
        return np.concatenate([_residual(m, cur) for m in mates]) if mates else np.zeros(0)

    def eval_all(cur: dict[Any, tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
        r = resid(cur)
        jac = np.zeros((len(r), n))
        for k, m in enumerate(mates):
            for cid in {m["a"]["component"], m["b"]["component"]}:
                if cid not in col:
                    continue
                j = col[cid]
                r0, t0 = cur[cid]
                for d in range(6):
                    dx = np.zeros(6)
                    dx[d] = STEP_H
                    pp = dict(cur)
                    pp[cid] = (_rodrigues(dx[3:] / size[cid]) @ r0, t0 + dx[:3])
                    jac[start[k] : start[k + 1], j + d] = (
                        _residual(m, pp) - r[start[k] : start[k + 1]]
                    ) / STEP_H
        return r, jac

    r, jac = eval_all(pose)
    cost = float(r @ r)
    lam = 0.0  # Gauss-Newton with a line search first; Levenberg damping only when that stalls
    deadline = time.monotonic() + SOLVE_BUDGET_S
    for _ in range(MAX_ITER):
        if not n or np.max(np.abs(r), initial=0.0) < SOLVED_TOL * 1e-2:
            break
        if time.monotonic() > deadline:
            break  # the mates not reached are reported FAILED below
        # (J^T J + lam I)^-1 J^T = J^T (J J^T + lam I)^-1: the mates x mates system is the smaller
        # one; lam -> 0 gives the minimum-norm step (directions no mate constrains do not move)
        if len(r) <= n:
            jj = jac @ jac.T
            mu = max(lam, 1e-12 * max(1.0, float(np.trace(jj)) / len(r)))
            step = jac.T @ np.linalg.solve(jj + mu * np.eye(len(r)), -r)
        else:  # more equations than unknowns: the unknowns x unknowns system is smaller
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
    out_mates = []
    for k, m in enumerate(mates):
        res = float(np.max(np.abs(r[start[k] : start[k + 1]]), initial=0.0))
        out_mates.append({"residual": res, "status": "OK" if res < SOLVED_TOL else "FAILED"})
    rank = int(np.linalg.matrix_rank(jac, tol=RANK_TOL)) if n and len(r) else 0
    for m_, t_ in pose.values():
        if not (
            np.all(np.isfinite(m_)) and np.all(np.isfinite(t_)) and np.max(np.abs(t_)) <= MAX_COORD
        ):
            raise GeomError(
                "ASSEMBLY_SOLVE_DIVERGED", "The mates could not be solved; remove the last one"
            )
    placements = {
        cid: {"t": [round(float(v), 9) + 0.0 for v in t], "r": euler(m)}
        for cid, (m, t) in pose.items()
    }
    return {"placements": placements, "mates": out_mates, "dof": n - rank}


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
