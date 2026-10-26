"""Track 8 P2-1: 2D sketch constraint solver (FreeCAD Sketcher behaviour, clean-room per ADR-12).

Unknowns are the entities' coordinates (mm; arc angles in radians), every constraint adds residual
equations, and Levenberg-Marquardt drives them to zero with the smallest move from the start
geometry (so an under-constrained sketch stays where it was drawn). At the solution:
- degrees of freedom = unknowns - rank(J)
- a constraint whose equations add no rank is redundant (solved) or conflicting (not solved).

Entities (sketch plane coordinates): POINT {point}, LINE {start, end}, CIRCLE {center, radius},
ARC {center, radius, start_angle, end_angle} (degrees, counter-clockwise, like the DXF geoms).
A reference is {"e": entity id, "p": "edge" | "start" | "end" | "center"} (a POINT is its
"center"). Entity -1 is the fixed horizontal axis, -2 the vertical one; (-1, "start") is the origin.
"""

import math
from typing import Any

import numpy as np

from core.geometry.errors import GeomError

SIZES = {"POINT": 2, "LINE": 4, "CIRCLE": 3, "ARC": 5}
AXES = {-1: ((0.0, 0.0), (1.0, 0.0)), -2: ((0.0, 0.0), (0.0, 1.0))}
SOLVED_TOL = 1e-9  # max |residual| (mm, or rad for angles)
RANK_TOL = 1e-7  # a unit Jacobian row closer than this to the earlier rows' span is dependent
MAX_ITER = 200
MAX_ENTITIES = 500
MAX_CONSTRAINTS = 1000
EPS = 1e-12

Vec = tuple[float, float]


def _bad(i: int, msg: str) -> GeomError:
    return GeomError("SKETCH_INVALID_CONSTRAINT", msg, details={"constraint": i})


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1])


def _cross(a: Vec, b: Vec) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _len(a: Vec) -> float:
    return max(math.hypot(*a), EPS)


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class _Model:
    """Maps entity ids to slices of the unknown vector and reads geometry out of it."""

    def __init__(self, entities: list[dict[str, Any]]):
        self.kind: dict[int, str] = {}
        self.at: dict[int, int] = {}
        x: list[float] = []
        for e in entities:
            t, i = e["type"], e["id"]
            if t not in SIZES or i in self.kind or i < 1:
                raise GeomError(
                    "SKETCH_INVALID_ENTITY", "Unknown type or duplicate id", details={"id": i}
                )
            self.kind[i], self.at[i] = t, len(x)
            if t == "POINT":
                x += e["point"]
            elif t == "LINE":
                x += [*e["start"], *e["end"]]
            elif t == "CIRCLE":
                x += [*e["center"], e["radius"]]
            else:
                x += [
                    *e["center"],
                    e["radius"],
                    *map(math.radians, (e["start_angle"], e["end_angle"])),
                ]
        self.x0 = np.array(x, dtype=float)

    def kind_of(self, e: int) -> str:
        if e in AXES:
            return "LINE"
        if e not in self.kind:
            raise GeomError(
                "SKETCH_INVALID_ENTITY", "Constraint names a missing entity", details={"id": e}
            )
        return self.kind[e]

    def deps(self, e: int) -> list[int]:
        if e in AXES:
            return []
        o = self.at[e]
        return list(range(o, o + SIZES[self.kind[e]]))

    def point(self, x: np.ndarray, e: int, p: str) -> Vec:
        if e in AXES:
            return AXES[e][1] if p == "end" else AXES[e][0]
        o, t = self.at[e], self.kind[e]
        if t == "LINE" and p in ("start", "end"):
            o += 2 if p == "end" else 0
            return (x[o], x[o + 1])
        if t == "ARC" and p in ("start", "end"):
            a, r = x[o + (3 if p == "start" else 4)], x[o + 2]
            return (x[o] + r * math.cos(a), x[o + 1] + r * math.sin(a))
        return (x[o], x[o + 1])  # POINT, CIRCLE/ARC center

    def line(self, x: np.ndarray, e: int) -> tuple[Vec, Vec]:
        return self.point(x, e, "start"), self.point(x, e, "end")

    def circle(self, x: np.ndarray, e: int) -> tuple[Vec, float]:
        o = self.at[e]
        return (x[o], x[o + 1]), x[o + 2]

    def entities(self, x: np.ndarray, src: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for e in src:
            o, t = self.at[e["id"]], e["type"]
            v = [float(a) for a in x[o : o + SIZES[t]]]
            g: dict[str, Any]
            if t == "POINT":
                g = {"point": v}
            elif t == "LINE":
                g = {"start": v[:2], "end": v[2:]}
            elif t == "CIRCLE":
                g = {"center": v[:2], "radius": abs(v[2])}
            else:
                r, a0, a1 = v[2], v[3], v[4]
                if r < 0:  # the same arc seen from the other side of its centre
                    r, a0, a1 = -r, a0 + math.pi, a1 + math.pi
                g = {
                    "center": v[:2],
                    "radius": r,
                    "start_angle": math.degrees(a0) % 360,
                    "end_angle": math.degrees(a1) % 360,
                }
            out.append({**e, **g})
        return out


RULES: dict[
    str, set[tuple[str, ...]]
] = {  # constraint type -> the reference signatures it accepts (P point, L line, C circle/arc)
    "COINCIDENT": {("P", "P")},
    "CONCENTRIC": {("C", "C")},
    "HORIZONTAL": {("L",), ("P", "P")},
    "VERTICAL": {("L",), ("P", "P")},
    "PARALLEL": {("L", "L")},
    "PERPENDICULAR": {("L", "L")},
    # P/P: FreeCAD endpoint-to-endpoint tangency (the ends meet and the curves turn smoothly there)
    "TANGENT": {("L", "C"), ("C", "L"), ("C", "C"), ("P", "P")},
    "EQUAL": {("L", "L"), ("C", "C")},
    "FIXED": {("P",)},
    "DISTANCE": {("P", "P"), ("P", "L"), ("L",)},
    "DISTANCE_X": {("P", "P"), ("P",), ("L",)},
    "DISTANCE_Y": {("P", "P"), ("P",), ("L",)},
    "RADIUS": {("C",)},
    "DIAMETER": {("C",)},
    "ANGLE": {("L", "L"), ("L",)},
    "POINT_ON_OBJECT": {("P", "L"), ("P", "C")},
    "SYMMETRIC": {("P", "P", "L"), ("P", "P", "P")},
}
VALUED = ("DISTANCE", "DISTANCE_X", "DISTANCE_Y", "RADIUS", "DIAMETER", "ANGLE")


class _Constraint:
    """One constraint bound to the model: residual(x) gives its equations."""

    def __init__(self, m: _Model, i: int, c: dict[str, Any]):
        t, refs, v = c["type"], c["refs"], c.get("value")
        kinds = [m.kind_of(r["e"]) for r in refs]
        for r, k in zip(refs, kinds, strict=True):
            ok = {
                "edge": k != "POINT",
                "center": k != "LINE",
                "start": k in ("LINE", "ARC"),
                "end": k in ("LINE", "ARC"),
            }
            if not ok.get(r["p"], False):
                raise _bad(i, f"{r['p']} is not a part of a {k}")
        sig = tuple(
            "P" if r["p"] != "edge" else ("L" if k == "LINE" else "C")
            for r, k in zip(refs, kinds, strict=True)
        )
        if sig not in RULES.get(t, set()):
            raise _bad(i, f"{t} does not apply to {'/'.join(sig) or 'nothing'}")
        if (t in VALUED) != (v is not None):
            raise _bad(i, f"{t} {'needs' if t in VALUED else 'takes no'} value")
        if t == "TANGENT" and sig == ("P", "P") and any(r["p"] == "center" for r in refs):
            raise _bad(i, "endpoint tangency needs two curve ends")
        v = float(v or 0)
        if t in ("DISTANCE", "RADIUS", "DIAMETER") and v < 0:
            raise _bad(i, f"{t} must not be negative")
        self.m, self.t, self.sig, self.refs = m, t, sig, refs
        self.v = math.radians(v) if t == "ANGLE" else v
        self.deps = sorted({d for r in refs for d in m.deps(r["e"])})
        x0 = m.x0
        self.at = self.P(x0, 0) if t == "FIXED" else None  # FreeCAD Lock: stays where it is now
        if t == "TANGENT" and sig == ("C", "C"):  # as drawn: one circle inside the other or not
            (c0, r0), (c1, r1) = self.C(x0, 0), self.C(x0, 1)
            self.inner = math.dist(c0, c1) < max(abs(r0), abs(r1))

    def P(self, x: np.ndarray, j: int) -> Vec:
        return self.m.point(x, self.refs[j]["e"], self.refs[j]["p"])

    def C(self, x: np.ndarray, j: int) -> tuple[Vec, float]:
        return self.m.circle(x, self.refs[j]["e"])

    def d(self, x: np.ndarray, j: int) -> Vec:
        a, b = self.m.line(x, self.refs[j]["e"])
        return _sub(b, a)

    def tangent_at(self, x: np.ndarray, j: int) -> Vec:
        """Direction of curve j at its referenced end."""
        e = self.refs[j]["e"]
        if self.m.kind_of(e) == "LINE":
            return self.d(x, j)
        c, _ = self.C(x, j)
        q = _sub(self.P(x, j), c)
        return (-q[1], q[0])

    def off(self, x: np.ndarray, p: Vec, j: int) -> float:
        """Signed distance of p from line j."""
        a, _ = self.m.line(x, self.refs[j]["e"])
        u = self.d(x, j)
        return _cross(u, _sub(p, a)) / _len(u)

    def residual(self, x: np.ndarray) -> list[float]:
        t, sig, v, P, C, d = self.t, self.sig, self.v, self.P, self.C, self.d
        if t == "COINCIDENT":
            return list(_sub(P(x, 0), P(x, 1)))
        if t == "CONCENTRIC":
            return list(_sub(C(x, 0)[0], C(x, 1)[0]))
        if t in ("HORIZONTAL", "VERTICAL"):
            k = 1 if t == "HORIZONTAL" else 0
            return [d(x, 0)[k]] if sig == ("L",) else [P(x, 1)[k] - P(x, 0)[k]]
        if t in ("PARALLEL", "PERPENDICULAR"):
            a, b = d(x, 0), d(x, 1)
            return [(_cross(a, b) if t == "PARALLEL" else _dot(a, b)) / (_len(a) * _len(b))]
        if t == "TANGENT" and sig == ("P", "P"):
            a, b = self.tangent_at(x, 0), self.tangent_at(x, 1)
            return [*_sub(P(x, 0), P(x, 1)), _cross(a, b) / (_len(a) * _len(b))]
        if t == "TANGENT" and sig == ("C", "C"):
            (a, ra), (b, rb) = C(x, 0), C(x, 1)
            want = abs(abs(ra) - abs(rb)) if self.inner else abs(ra) + abs(rb)
            return [math.dist(a, b) - want]
        if t == "TANGENT":
            li, ci = (0, 1) if sig[0] == "L" else (1, 0)
            c, r = C(x, ci)
            return [abs(self.off(x, c, li)) - abs(r)]
        if t == "EQUAL":
            if sig == ("L", "L"):
                return [_len(d(x, 0)) - _len(d(x, 1))]
            return [abs(C(x, 0)[1]) - abs(C(x, 1)[1])]
        if t == "FIXED":
            assert self.at is not None
            return list(_sub(P(x, 0), self.at))
        if t == "DISTANCE":
            if sig == ("P", "P"):
                return [math.dist(P(x, 0), P(x, 1)) - v]
            if sig == ("P", "L"):
                return [abs(self.off(x, P(x, 0), 1)) - v]
            return [_len(d(x, 0)) - v]
        if t in ("DISTANCE_X", "DISTANCE_Y"):
            k = 0 if t == "DISTANCE_X" else 1
            if sig == ("P", "P"):
                return [P(x, 1)[k] - P(x, 0)[k] - v]
            return [(P(x, 0)[k] if sig == ("P",) else d(x, 0)[k]) - v]
        if t in ("RADIUS", "DIAMETER"):
            return [(2.0 if t == "DIAMETER" else 1.0) * abs(C(x, 0)[1]) - v]
        if t == "ANGLE":
            a = d(x, 0)
            if sig == ("L",):
                return [_wrap(math.atan2(a[1], a[0]) - v)]
            b = d(x, 1)
            return [_wrap(math.atan2(_cross(a, b), _dot(a, b)) - v)]
        if t == "POINT_ON_OBJECT":
            if sig == ("P", "L"):
                return [self.off(x, P(x, 0), 1)]
            c, r = C(x, 1)
            return [math.dist(P(x, 0), c) - abs(r)]
        # SYMMETRIC
        a, b = P(x, 0), P(x, 1)
        if sig == ("P", "P", "P"):
            q = P(x, 2)
            return [a[0] + b[0] - 2 * q[0], a[1] + b[1] - 2 * q[1]]
        u = d(x, 2)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        return [self.off(x, mid, 2), _dot(u, _sub(b, a)) / _len(u)]


def _jacobian(parts: list[_Constraint], rows: list[int], x: np.ndarray) -> np.ndarray:
    """Central differences, only over the unknowns each constraint reads."""
    jac = np.zeros((sum(rows), len(x)))
    r0 = 0
    for c, n in zip(parts, rows, strict=True):
        for j in c.deps:
            h = 1e-7 * max(1.0, abs(x[j]))
            xp, xm = x.copy(), x.copy()
            xp[j] += h
            xm[j] -= h
            jac[r0 : r0 + n, j] = (np.array(c.residual(xp)) - np.array(c.residual(xm))) / (2 * h)
        r0 += n
    return jac


def _dependent(jac: np.ndarray, rows: list[int]) -> tuple[list[int], int]:
    """Constraint indices with a row in the span of the earlier rows (Gram-Schmidt in order),
    and the rank of the Jacobian."""
    basis = np.zeros((0, jac.shape[1]))
    flagged: list[int] = []
    r0 = 0
    for ci, n in enumerate(rows):
        for row in jac[r0 : r0 + n]:
            norm = np.linalg.norm(row)
            if norm < EPS:
                flagged.append(ci)
                continue
            q = row / norm
            for _ in range(2):  # twice: classic Gram-Schmidt loses orthogonality once
                q = q - basis.T @ (basis @ q)
            if np.linalg.norm(q) < RANK_TOL:
                flagged.append(ci)
            else:
                basis = np.vstack([basis, q / np.linalg.norm(q)])
        r0 += n
    return sorted(set(flagged)), len(basis)


def solve(entities: list[dict[str, Any]], constraints: list[dict[str, Any]]) -> dict[str, Any]:
    """Solved entities, degrees of freedom and status OK / REDUNDANT / CONFLICT."""
    if len(entities) > MAX_ENTITIES or len(constraints) > MAX_CONSTRAINTS:
        raise GeomError("SKETCH_TOO_LARGE", "Sketch has too many entities or constraints")
    m = _Model(entities)
    parts = [_Constraint(m, i, c) for i, c in enumerate(constraints)]
    x = m.x0.copy()
    rows = [len(c.residual(x)) for c in parts]

    def residual(x: np.ndarray) -> np.ndarray:
        return np.array([r for c in parts for r in c.residual(x)], dtype=float)

    f = residual(x)
    cost, lam = float(f @ f), 1e-3
    for _ in range(MAX_ITER):
        if not len(f) or np.max(np.abs(f)) < SOLVED_TOL:
            break
        jac = _jacobian(parts, rows, x)
        step = None
        while lam < 1e12:
            # min-norm LM: dx = J^T (J J^T + lam I)^-1 (-f), the same step as the usual normal form
            a = jac @ jac.T + lam * np.eye(len(f))
            dx = jac.T @ np.linalg.solve(a, -f)
            fn_ = residual(x + dx)
            c = float(fn_ @ fn_)
            if np.isfinite(c) and c < cost:
                step, lam = (x + dx, fn_, c), max(lam / 10, 1e-15)
                break
            lam *= 10
        if step is None:
            break  # no step lowers the error: over-constrained
        x, f, cost = step
    solved = not len(f) or float(np.max(np.abs(f))) < SOLVED_TOL * 10
    jac = _jacobian(parts, rows, x)
    flagged, rank = _dependent(jac, rows)
    status = "CONFLICT" if not solved else "REDUNDANT" if flagged else "OK"
    return {
        "entities": m.entities(x if solved else m.x0, entities),
        "dof": len(x) - rank,
        "status": status,
        "conflicting": flagged if status == "CONFLICT" else [],
        "redundant": flagged if status == "REDUNDANT" else [],
    }


def solve_job(entities: list[dict[str, Any]], constraints: list[dict[str, Any]]) -> dict[str, Any]:
    """Worker entry point (bounded time and memory, like the kernel jobs)."""
    return solve(entities, constraints)
