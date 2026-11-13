"""plan §23 S1: a general sheet metal unfolder (flat pattern of any constant-thickness sheet).

One skin of the sheet (the faces on one side of the material) is walked from a fixed plane face
across smooth edges: planes stay rigid, every cylinder (a bend) is laid out as a strip of its
bend allowance BA = angle * (R + K*T) with R the inside radius, and the plane beyond it is
placed BA further on. The skin's remaining edges (where it meets the thickness faces) are the
blank's outline and holes; each bend's strip centre is its bend line. FreeCAD's SheetMetal
unfolder works the same way; this one is written on OCCT directly.

Points on a bend map linearly in angle: a point at angle phi of theta, axial position s goes to
junction + s*axis + (phi/theta)*BA*inwards, so a cut or hole crossing a bend lands where the
press brake will make it.
"""

import math
from collections.abc import Callable
from typing import Any

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.ShapeAnalysis import ShapeAnalysis_Surface
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape

from core.geometry.errors import GeomError

Pt = tuple[float, float]
V3 = tuple[float, float, float]
SMOOTH = 1e-4  # radians between the two faces' normals along a shared edge: the same skin
DEFLECTION = 0.01  # mm: how closely a curved outline edge is followed
JOIN = 1e-4  # mm: outline pieces closer than this are the same point
MAX_FACES = 5000
MAX_POINTS = 200_000
MAX_EDGE_POINTS = 20_000
Allowance = Callable[[float, float, float], float]  # (angle rad, inside radius, thickness) -> BA


def _bad(msg: str, **details: Any) -> GeomError:
    return GeomError("SHEET_UNFOLD_UNSUPPORTED", msg, details=details or None)


def _v(p: gp_Pnt | gp_Vec | gp_Dir) -> V3:
    return (p.X(), p.Y(), p.Z())


def _sub(a: V3, b: V3) -> V3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: V3, b: V3, k: float = 1.0) -> V3:
    return (a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2])


def _dot(a: V3, b: V3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: V3, b: V3) -> V3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: V3) -> V3:
    n = math.sqrt(_dot(a, a))
    if n < 1e-12:
        raise _bad("Degenerate direction")
    return (a[0] / n, a[1] / n, a[2] / n)


def _apply(t: gp_Trsf, p: V3) -> V3:
    q = gp_Pnt(*p)
    q.Transform(t)
    return _v(q)


def _normal(face: TopoDS_Face, p: V3) -> V3:
    """The face's outward normal (the solid's outside) at the face point nearest p."""
    surf = BRepAdaptor_Surface(face).Surface().Surface()
    uv = ShapeAnalysis_Surface(surf).ValueOfUV(gp_Pnt(*p), 1e-7)
    pnt, nrm = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(uv.X(), uv.Y(), pnt, nrm)
    return _unit(_v(nrm))


def _area(face: TopoDS_Face) -> float:
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return float(g.Mass())


def _mid(face: TopoDS_Face) -> V3:
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return _v(g.CentreOfMass())


def _edge_points(edge: Any) -> list[V3]:
    c = BRepAdaptor_Curve(edge)
    if c.GetType() == GeomAbs_Line:
        pts = [_v(c.Value(c.FirstParameter())), _v(c.Value(c.LastParameter()))]
    else:
        d = GCPnts_QuasiUniformDeflection(c, DEFLECTION)
        if not d.IsDone():
            raise _bad("An outline edge could not be followed")
        if d.NbPoints() > MAX_EDGE_POINTS:
            raise _bad("An outline edge is too long to follow this closely")
        pts = [_v(d.Value(i)) for i in range(1, d.NbPoints() + 1)]
    if edge.Orientation() == TopAbs_REVERSED:
        pts.reverse()
    return pts


class _Bend:
    """A cylinder of the skin entered from a placed plane through junction edge `a`."""

    def __init__(self, face: TopoDS_Face, cyl: Any, a1: V3, d: V3, e_in: V3, n_in: V3) -> None:
        ax = cyl.Axis()
        self.face, self.c0, self.d = face, _v(ax.Location()), d
        self.r_skin = cyl.Radius()
        self.a1, self.e_in, self.n_in = a1, e_in, n_in
        self.rho1 = self._radial(a1)
        self.theta = 0.0  # set once the far side is known (or from the face's extent)
        self.sign = 1.0

    def _radial(self, p: V3) -> V3:
        v = _sub(p, self.c0)
        return _sub(
            v,
            (self.d[0] * _dot(v, self.d), self.d[1] * _dot(v, self.d), self.d[2] * _dot(v, self.d)),
        )

    def angle_of(self, p: V3) -> float:
        """Angle from the entry junction, 0..2pi the way the bend turns (a hem goes past pi)."""
        rp = self._radial(p)
        a = math.atan2(_dot(_cross(self.rho1, rp), self.d), _dot(self.rho1, rp)) * self.sign
        return a + 2 * math.pi if a < -1e-6 else max(a, 0.0)


class Unfolder:
    def __init__(
        self,
        shape: TopoDS_Shape,
        thickness: float,
        k_factor: float,
        allowance: Allowance | None = None,
        fixed: Callable[[TopoDS_Face, V3, V3], bool] | None = None,
    ) -> None:
        self.t, self.k = thickness, k_factor
        self.ba: Allowance = allowance or (lambda th, r, t: th * (r + k_factor * t))
        self.faces: list[TopoDS_Face] = []
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            self.faces.append(TopoDS.Face(exp.Current()))
            exp.Next()
        if not self.faces or len(self.faces) > MAX_FACES:
            raise _bad("The body has no faces or too many to unfold")
        self.edges = ShapeMap()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, self.edges)
        self.by_edge: dict[int, list[int]] = {}
        self.face_edges: list[list[tuple[int, Any]]] = []
        for fi, f in enumerate(self.faces):
            lst = []
            ex = TopExp_Explorer(f, TopAbs_EDGE)
            while ex.More():
                e = TopoDS.Edge(ex.Current())
                ei = self.edges.FindIndex(e)
                lst.append((ei, e))
                self.by_edge.setdefault(ei, []).append(fi)
                ex.Next()
            self.face_edges.append(lst)
        self.kind = [BRepAdaptor_Surface(f).GetType() for f in self.faces]
        self.fixed_pick = fixed

    # --- skin -----------------------------------------------------------------------------
    def _smooth(self, fi: int, gi: int, edge: Any) -> bool:
        c = BRepAdaptor_Curve(edge)
        p = _v(c.Value((c.FirstParameter() + c.LastParameter()) / 2))
        n1, n2 = _normal(self.faces[fi], p), _normal(self.faces[gi], p)
        return _dot(n1, n2) > math.cos(SMOOTH)

    def _fixed(self) -> int:
        planes = [i for i, k in enumerate(self.kind) if k == GeomAbs_Plane]
        if not planes:
            raise _bad("No flat face to hold still")
        if self.fixed_pick:
            for i in planes:
                p = _mid(self.faces[i])
                if self.fixed_pick(self.faces[i], p, _normal(self.faces[i], p)):
                    return i
        return max(planes, key=lambda i: _area(self.faces[i]))

    def _check_thickness(self, f0: int, p0: V3, n0: V3) -> None:
        """A sheet: some flat face faces the other way exactly one thickness behind the fixed one."""
        for i, k in enumerate(self.kind):
            if k != GeomAbs_Plane or i == f0:
                continue
            q = _mid(self.faces[i])
            n = _normal(self.faces[i], q)
            if _dot(n, n0) < -1 + 1e-9 and abs(-_dot(_sub(q, p0), n0) - self.t) <= 1e-4:
                return
        raise _bad(
            f"Not a sheet of thickness {self.t:g} mm (no face that far behind the fixed one)"
        )

    # --- unfolding ------------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        f0 = self._fixed()
        p0 = _mid(self.faces[f0])
        n0 = _normal(self.faces[f0], p0)
        self._check_thickness(f0, p0, n0)
        # flat frame: the fixed face is z = 0 with the material on +z (its outward normal is -z)
        if self.fixed_pick is None:
            x = _cross((0.0, 0.0, 1.0), n0) if abs(n0[2]) < 0.9 else (1.0, 0.0, 0.0)
            x = _unit(_sub(x, (n0[0] * _dot(x, n0), n0[1] * _dot(x, n0), n0[2] * _dot(x, n0))))
            frame = gp_Trsf()
            frame.SetTransformation(gp_Ax3(gp_Pnt(*p0), gp_Dir(*(-c for c in n0)), gp_Dir(*x)))
        else:
            frame = gp_Trsf()  # the caller's coordinates already are the flat ones
        place: dict[int, gp_Trsf] = {f0: gp_Trsf()}  # plane -> its unfolding (folded -> flat 3D)
        bends: dict[int, _Bend] = {}
        bend_place: dict[int, gp_Trsf] = {}
        queue = [f0]
        while queue:
            fi = queue.pop()
            for ei, edge in self.face_edges[fi]:
                for gi in self.by_edge.get(ei, []):
                    if gi == fi or gi in place or gi in bends:
                        continue
                    if not self._smooth(fi, gi, edge):
                        continue  # a thickness face (or a sharp fold): not this skin
                    if self.kind[gi] == GeomAbs_Plane:
                        if fi in bends:
                            place[gi] = self._beyond(bends[fi], bend_place[fi], gi, edge)
                        elif self.kind[fi] == GeomAbs_Plane:
                            place[gi] = place[fi]  # one plane split in two
                        queue.append(gi)
                    elif self.kind[gi] == GeomAbs_Cylinder:
                        if fi not in place:
                            raise _bad("Two bends touch with no flat part between them")
                        bends[gi] = self._enter(fi, gi, edge)
                        bend_place[gi] = place[fi]
                        queue.append(gi)
                    else:
                        raise _bad("Only flat faces and cylindrical bends can be unfolded", face=gi)
        for bi, b in bends.items():
            if b.theta == 0.0:  # a bend with nothing beyond it (a flange of length 0)
                b.theta = self._extent(bi, b)
        return self._collect(place, bends, bend_place, frame)

    def _enter(self, fi: int, gi: int, edge: Any) -> _Bend:
        cyl = BRepAdaptor_Surface(self.faces[gi]).Cylinder()
        d = _unit(_v(cyl.Axis().Direction()))
        c = BRepAdaptor_Curve(edge)
        a1 = _v(c.Value((c.FirstParameter() + c.LastParameter()) / 2))
        n_in = _normal(self.faces[fi], a1)
        # into the bend: from the junction towards the bend's middle, in the plane's own plane
        m = _mid(self.faces[gi])
        v = _sub(m, a1)
        v = _sub(v, (d[0] * _dot(v, d), d[1] * _dot(v, d), d[2] * _dot(v, d)))
        v = _sub(v, (n_in[0] * _dot(v, n_in), n_in[1] * _dot(v, n_in), n_in[2] * _dot(v, n_in)))
        b = _Bend(self.faces[gi], cyl, a1, d, _unit(v), n_in)
        rp = b._radial(m)
        b.sign = 1.0 if _dot(_cross(b.rho1, rp), d) >= 0 else -1.0  # the way the bend turns
        return b

    def _inside_radius(self, b: _Bend) -> float:
        m = _mid(b.face)
        toward_axis = _dot(_normal(b.face, m), b._radial(m)) < 0  # the skin is the bend's inside
        return b.r_skin if toward_axis else b.r_skin - self.t

    def _allowance(self, b: _Bend) -> float:
        return self.ba(b.theta, self._inside_radius(b), self.t)

    def _extent(self, bi: int, b: _Bend) -> float:
        return max(b.angle_of(p) for _, e in self.face_edges[bi] for p in _edge_points(e))

    def _beyond(self, b: _Bend, before: gp_Trsf, gi: int, edge: Any) -> gp_Trsf:
        """The unfolding of the plane past bend b: its junction goes BA further along e_in."""
        c = BRepAdaptor_Curve(edge)
        mid = _v(c.Value((c.FirstParameter() + c.LastParameter()) / 2))
        s = _dot(_sub(mid, b.a1), b.d)
        a2 = _add(mid, b.d, -s)  # level with a1 along the axis
        b.theta = b.angle_of(a2)
        n2 = _normal(self.faces[gi], a2)
        # away from the bend into the plane: from the bend's middle towards the junction
        v = _sub(a2, _mid(b.face))
        v = _sub(v, (b.d[0] * _dot(v, b.d), b.d[1] * _dot(v, b.d), b.d[2] * _dot(v, b.d)))
        v = _sub(v, (n2[0] * _dot(v, n2), n2[1] * _dot(v, n2), n2[2] * _dot(v, n2)))
        e2 = _unit(v)
        target = _add(b.a1, b.e_in, self._allowance(b))
        src = gp_Ax3(gp_Pnt(*a2), gp_Dir(*n2), gp_Dir(*e2))
        dst = gp_Ax3(gp_Pnt(*target), gp_Dir(*b.n_in), gp_Dir(*b.e_in))
        u = gp_Trsf()
        u.SetDisplacement(src, dst)
        return before.Multiplied(u)

    # --- output ---------------------------------------------------------------------------
    def _collect(
        self,
        place: dict[int, gp_Trsf],
        bends: dict[int, _Bend],
        bend_place: dict[int, gp_Trsf],
        frame: gp_Trsf,
    ) -> dict[str, Any]:
        self._place = place
        self._frame = frame
        skin = set(place) | set(bends)

        def flat(fi: int, p: V3) -> Pt:
            if fi in bends:
                b = bends[fi]
                s = _dot(_sub(p, b.a1), b.d)
                phi = min(max(b.angle_of(p), 0.0), b.theta)
                q = _add(
                    _add(b.a1, b.d, s),
                    b.e_in,
                    (phi / b.theta if b.theta else 0.0) * self._allowance(b),
                )
                q = _apply(bend_place[fi], q)
            else:
                q = _apply(place[fi], p)
            q = _apply(frame, q)
            return (q[0], q[1])

        pieces: list[list[Pt]] = []
        circles: list[dict[str, Any]] = []
        npts = 0
        for fi in skin:
            for ei, e in self.face_edges[fi]:
                if any(g in skin and g != fi for g in self.by_edge.get(ei, [])):
                    continue  # between two skin faces: folded over, not cut
                c = BRepAdaptor_Curve(e)
                if fi in place and c.GetType() == GeomAbs_Circle and e.Closed():
                    circ = c.Circle()
                    ctr = flat(fi, _v(circ.Location()))
                    circles.append({"center": [ctr[0], ctr[1]], "radius": circ.Radius()})
                    continue
                pts = [flat(fi, p) for p in _edge_points(e)]
                npts += len(pts)
                if npts > MAX_POINTS:
                    raise _bad("The flat pattern has too many points")
                pieces.append(pts)
        loops = _chain(pieces)
        bend_out = []
        for fi, b in bends.items():
            ba = self._allowance(b)
            ss = [_dot(_sub(p, b.a1), b.d) for _, e in self.face_edges[fi] for p in _edge_points(e)]
            s0, s1 = min(ss), max(ss)
            mid0 = _add(_add(b.a1, b.d, s0), b.e_in, ba / 2)
            mid1 = _add(_add(b.a1, b.d, s1), b.e_in, ba / 2)
            p0 = _apply(frame, _apply(bend_place[fi], mid0))
            p1 = _apply(frame, _apply(bend_place[fi], mid1))
            # up = the bend turns towards the material side (its axis is inside, away from the
            # skin's outward normal)
            up = _dot(_sub(b.c0, b.a1), b.n_in) < 0
            bend_out.append(
                {
                    "start": [p0[0], p0[1]],
                    "end": [p1[0], p1[1]],
                    "length": s1 - s0,
                    "angle": math.degrees(b.theta),
                    "up": up,
                    "radius": self._inside_radius(b),
                    "allowance": ba,
                    # the folded axis (the caller matches the bend to the feature that made it)
                    "axis": {
                        "p": list(b.c0),
                        "d": list(b.d),
                        "mid": list(_add(b.a1, b.d, (s0 + s1) / 2)),
                    },
                }
            )
        return {"loops": loops, "circles": circles, "bends": bend_out}


def _chain(pieces: list[list[Pt]]) -> list[list[Pt]]:
    """Join edge polylines end to end into closed loops (any direction); ends are found through a
    grid of JOIN-sized cells, so the work grows with the number of pieces, not its square."""
    left = [p for p in pieces if len(p) >= 2]
    cell = lambda q: (math.floor(q[0] / JOIN), math.floor(q[1] / JOIN))
    ends: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for i, p in enumerate(left):
        for at_end in (False, True):
            ends.setdefault(cell(p[-1] if at_end else p[0]), []).append((i, at_end))
    used = [False] * len(left)

    def take(q: Pt) -> list[Pt] | None:
        cx, cy = cell(q)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, at_end in ends.get((cx + dx, cy + dy), []):
                    p = left[i]
                    if not used[i] and math.dist(p[-1] if at_end else p[0], q) <= JOIN:
                        used[i] = True
                        return p[::-1] if at_end else p
        return None

    loops: list[list[Pt]] = []
    for i, first in enumerate(left):
        if used[i]:
            continue
        used[i] = True
        cur = list(first)
        while math.dist(cur[0], cur[-1]) > JOIN:
            nxt = take(cur[-1])
            if nxt is None:
                raise _bad("The flat outline does not close")
            cur.extend(nxt[1:])
        loops.append(cur[:-1])
    return loops


def sheet_thickness(shape: TopoDS_Shape) -> float:
    """The thickness of a sheet body: how far behind its largest flat face the nearest flat face
    looking the other way lies."""
    faces = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        f = TopoDS.Face(exp.Current())
        if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane:
            faces.append(f)
        exp.Next()
        if len(faces) > MAX_FACES:
            raise _bad("The body has too many faces to unfold")
    if not faces:
        raise _bad("No flat face to hold still")
    f0 = max(faces, key=_area)
    p0 = _mid(f0)
    n0 = _normal(f0, p0)
    best = None
    for f in faces:
        q = _mid(f)
        if f.IsSame(f0) or _dot(_normal(f, q), n0) > -1 + 1e-9:
            continue
        d = -_dot(_sub(q, p0), n0)
        if d > 1e-4 and (best is None or d < best):
            best = d
    if best is None:
        raise _bad("Not a sheet: no flat face behind the largest one")
    return round(best, 6)


def unfold(
    shape: TopoDS_Shape,
    thickness: float,
    k_factor: float,
    allowance: Allowance | None = None,
    fixed: Callable[[TopoDS_Face, V3, V3], bool] | None = None,
) -> dict[str, Any]:
    """Flat pattern of a sheet body: closed loops (outline + holes, flat mm), full circles
    (holes on flat faces), bends (centre line, length, angle, up, inside radius, allowance)."""
    return Unfolder(shape, thickness, k_factor, allowance, fixed).run()


# --- fold map (S3b: flat ↔ folded coordinate mapping) ----------------------------------------


class FoldMap:
    """plan §23 S3b: maps flat-plane geometry back to the folded body so a cut drawn on the flat
    pattern appears correctly on each planar face of the folded part.

    ``fold_tools(flat_tool)`` takes an OCCT shape (the cut tool sitting on z = 0 of the flat
    pattern) and returns a list of the same tool transformed into each planar face's folded
    position. The Boolean engine keeps only the intersections that actually hit material.

    Bends are covered by extending adjacent planar face tools — correct for cuts that don't
    straddle two bends at once (the practical case).
    """

    def __init__(self, u: "Unfolder") -> None:
        self._inv: dict[int, gp_Trsf] = {}
        frame_inv = u._frame.Inverted()
        for fi, place_trsf in u._place.items():
            self._inv[fi] = place_trsf.Inverted().Multiplied(frame_inv)

    def fold_tools(self, flat_tool: TopoDS_Shape) -> list[TopoDS_Shape]:
        """The flat_tool in each planar face's folded coordinates."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

        out: list[TopoDS_Shape] = []
        for inv in self._inv.values():
            out.append(BRepBuilderAPI_Transform(flat_tool, inv, True).Shape())
        return out


def unfold_with_map(
    shape: TopoDS_Shape,
    thickness: float,
    k_factor: float,
    allowance: Allowance | None = None,
    fixed: Callable[[TopoDS_Face, V3, V3], bool] | None = None,
) -> tuple[dict[str, Any], FoldMap]:
    """Like ``unfold()`` but also returns a FoldMap for mapping flat cuts back to folded."""
    u = Unfolder(shape, thickness, k_factor, allowance, fixed)
    result = u.run()
    return result, FoldMap(u)
