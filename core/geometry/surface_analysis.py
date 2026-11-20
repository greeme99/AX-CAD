"""S8: Surface quality analysis — continuity check, curvature map, zebra/highlight, draft analysis.

All coordinates in mm, angles in degrees.
"""

from __future__ import annotations

import math
from typing import Any

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.gp import gp_Dir, gp_Pnt, gp_Pnt2d, gp_Vec
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_OUT, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.serialize import from_brep

# ── limits ──────────────────────────────────────────────────────────────────
MAX_SAMPLE_POINTS = 200_000
MAX_EDGE_SAMPLES = 500
MAX_FACES = 1000


# ═══════════════════════════════════════════════════════════════════════════
# 1. CURVATURE MAP — Gauss / Mean / Min / Max at sampled UV points
# ═══════════════════════════════════════════════════════════════════════════


def curvature_map(
    face_shape: TopoDS_Shape,
    n_u: int = 20,
    n_v: int = 20,
) -> dict[str, Any]:
    """Sample curvature over every face of a body, an n_u × n_v grid per face (points outside a
    trimmed face's outline are skipped). Signs follow each face's outward side, so neighbouring
    faces colour alike.

    Returns dict with:
      - samples: list of {face_idx, u, v, x, y, z, gauss, mean, k_min, k_max}
      - stats: {gauss_min, gauss_max, mean_min, mean_max, k_min_global, k_max_global}

    §23.5 verification: cylinder → curvature 1/R, gauss 1/R² at 1e-4 relative.
    """
    if n_u < 2 or n_v < 2:
        raise GeomError("GEOM_INVALID_PARAM", "need at least 2×2 samples")
    if n_u * n_v > MAX_SAMPLE_POINTS:
        raise GeomError("GEOM_INVALID_PARAM", f"too many sample points (max {MAX_SAMPLE_POINTS})")

    faces: list[Any] = []
    ex = TopExp_Explorer(face_shape, TopAbs_FACE)
    while ex.More():
        faces.append(TopoDS.Face(ex.Current()))
        ex.Next()
    if not faces:
        raise GeomError("GEOM_INVALID_PARAM", "input has no face")
    if len(faces) > MAX_FACES or n_u * n_v * len(faces) > MAX_SAMPLE_POINTS:
        raise GeomError("GEOM_INVALID_PARAM", "too many faces for this sample grid")

    samples: list[dict[str, float]] = []
    stats: dict[str, float | None] = {
        "gauss_min": float("inf"),
        "gauss_max": float("-inf"),
        "mean_min": float("inf"),
        "mean_max": float("-inf"),
        "k_min_global": float("inf"),
        "k_max_global": float("-inf"),
    }

    for face_idx, face in enumerate(faces):
        adaptor = BRepAdaptor_Surface(face)
        u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
        v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
        inside = BRepClass_FaceClassifier()
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(n_u):
            u = u0 + (u1 - u0) * i / (n_u - 1) if n_u > 1 else (u0 + u1) / 2
            for j in range(n_v):
                v = v0 + (v1 - v0) * j / (n_v - 1) if n_v > 1 else (v0 + v1) / 2

                inside.Perform(face, gp_Pnt2d(u, v), 1e-7)
                if inside.State() == TopAbs_OUT:
                    continue
                props = BRepLProp_SLProps(adaptor, u, v, 2, 1e-6)
                if not props.IsCurvatureDefined():
                    continue

                gauss = props.GaussianCurvature()
                mean = props.MeanCurvature()
                k_min = props.MinCurvature()
                k_max = props.MaxCurvature()
                if flip:
                    mean, k_min, k_max = -mean, -k_max, -k_min

                p = props.Value()
                sample = {
                    "face_idx": face_idx,
                    "u": u,
                    "v": v,
                    "x": p.X(),
                    "y": p.Y(),
                    "z": p.Z(),
                    "gauss": gauss,
                    "mean": mean,
                    "k_min": k_min,
                    "k_max": k_max,
                }
                samples.append(sample)

                if math.isfinite(gauss):
                    stats["gauss_min"] = min(stats["gauss_min"], gauss)
                    stats["gauss_max"] = max(stats["gauss_max"], gauss)
                if math.isfinite(mean):
                    stats["mean_min"] = min(stats["mean_min"], mean)
                    stats["mean_max"] = max(stats["mean_max"], mean)
                if math.isfinite(k_min):
                    stats["k_min_global"] = min(stats["k_min_global"], k_min)
                if math.isfinite(k_max):
                    stats["k_max_global"] = max(stats["k_max_global"], k_max)

    # Replace inf with None for JSON serialization
    for key, value in stats.items():
        if value is not None and not math.isfinite(value):
            stats[key] = None

    return {"samples": samples, "stats": stats, "n_u": n_u, "n_v": n_v}


# ═══════════════════════════════════════════════════════════════════════════
# 2. CONTINUITY CHECK — G0 gap, G1 angle, G2 curvature difference
# ═══════════════════════════════════════════════════════════════════════════


def _face_normal_at(face: TopoDS_Shape, u: float, v: float) -> gp_Vec | None:
    """Get surface normal at (u,v) on a face."""
    f = TopoDS.Face(face)
    adaptor = BRepAdaptor_Surface(f)
    p = gp_Pnt()
    d1u = gp_Vec()
    d1v = gp_Vec()
    adaptor.D1(u, v, p, d1u, d1v)
    cross = d1u.Crossed(d1v)
    if cross.Magnitude() < 1e-12:
        return None
    cross.Normalize()
    return cross


def _curvature_at(face: TopoDS_Shape, u: float, v: float) -> float | None:
    """Get mean curvature at (u,v) on a face."""
    f = TopoDS.Face(face)
    adaptor = BRepAdaptor_Surface(f)
    props = BRepLProp_SLProps(adaptor, u, v, 2, 1e-6)
    if not props.IsCurvatureDefined():
        return None
    return props.MeanCurvature()


def continuity_check(
    shape: TopoDS_Shape,
    n_samples: int = 20,
    g0_tol: float = 1e-3,
    g1_tol_deg: float = 1.0,
    g2_tol_pct: float = 5.0,
) -> dict[str, Any]:
    """Check G0/G1/G2 continuity across shared edges of a multi-face body.

    For each shared edge, sample n_samples points and measure:
    - G0: positional gap (mm) between adjacent face evaluations
    - G1: angle between normals (degrees)
    - G2: curvature difference (% of max curvature)

    Returns:
      edges: list of {edge_idx, g0_max, g1_max_deg, g2_max_pct, pass_g0, pass_g1, pass_g2, samples}
      summary: {total_edges, g0_pass, g1_pass, g2_pass, all_pass}
    """
    if n_samples < 2 or n_samples > MAX_EDGE_SAMPLES:
        raise GeomError("GEOM_INVALID_PARAM", f"n_samples must be in [2, {MAX_EDGE_SAMPLES}]")

    # Collect all faces
    faces: list[TopoDS_Shape] = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        faces.append(ex.Current())
        ex.Next()

    if len(faces) < 2:
        return {
            "edges": [],
            "summary": {
                "total_edges": 0,
                "g0_pass": 0,
                "g1_pass": 0,
                "g2_pass": 0,
                "all_pass": True,
            },
        }

    # Build edge → face adjacency via IsSame matching
    # For each face, extract edges; then find shared edges across face pairs
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface

    face_edges: list[list[TopoDS_Shape]] = []
    for face in faces:
        edges_of_face: list[TopoDS_Shape] = []
        ex_e = TopExp_Explorer(face, TopAbs_EDGE)
        while ex_e.More():
            edges_of_face.append(TopoDS.Edge(ex_e.Current()))
            ex_e.Next()
        face_edges.append(edges_of_face)

    # Collect shared edge → (edge, face_a, face_b), deduplicate by edge identity
    shared_pairs: list[tuple[TopoDS_Shape, TopoDS_Shape, TopoDS_Shape]] = []
    seen_edges: list[TopoDS_Shape] = []
    for fi in range(len(faces)):
        for fj in range(fi + 1, len(faces)):
            for ea in face_edges[fi]:
                for eb in face_edges[fj]:
                    if ea.IsSame(eb):
                        # Check not already recorded
                        already = any(ea.IsSame(se) for se in seen_edges)
                        if not already:
                            shared_pairs.append((ea, faces[fi], faces[fj]))
                            seen_edges.append(ea)
                        break

    edges_result: list[dict[str, Any]] = []
    g0_pass_count = 0
    g1_pass_count = 0
    g2_pass_count = 0
    total_edges = 0

    for edge_shape, face_a, face_b in shared_pairs:
        edge = TopoDS.Edge(edge_shape)
        total_edges += 1

        curve = BRepAdaptor_Curve(edge)
        t0, t1 = curve.FirstParameter(), curve.LastParameter()

        sa_a = ShapeAnalysis_Surface(BRep_Tool.Surface_s(TopoDS.Face(face_a)))
        sa_b = ShapeAnalysis_Surface(BRep_Tool.Surface_s(TopoDS.Face(face_b)))

        g0_max = 0.0
        g1_max = 0.0
        g2_max = 0.0
        adaptor_a = BRepAdaptor_Surface(TopoDS.Face(face_a))
        adaptor_b = BRepAdaptor_Surface(TopoDS.Face(face_b))

        for s in range(n_samples):
            t = t0 + (t1 - t0) * s / (n_samples - 1) if n_samples > 1 else (t0 + t1) / 2
            pt_3d = curve.Value(t)

            # Project point onto each face to get (u,v)
            uv_a = sa_a.ValueOfUV(pt_3d, 1e-3)
            uv_b = sa_b.ValueOfUV(pt_3d, 1e-3)

            # G0: positional gap
            pt_a = gp_Pnt()
            pt_b = gp_Pnt()
            adaptor_a.D0(uv_a.X(), uv_a.Y(), pt_a)
            adaptor_b.D0(uv_b.X(), uv_b.Y(), pt_b)
            gap = pt_a.Distance(pt_b)
            g0_max = max(g0_max, gap)

            # G1: normal angle
            n_a = _face_normal_at(face_a, uv_a.X(), uv_a.Y())
            n_b = _face_normal_at(face_b, uv_b.X(), uv_b.Y())
            if n_a is not None and n_b is not None:
                dot = n_a.Dot(n_b)
                dot = max(-1.0, min(1.0, dot))
                angle_deg = math.degrees(math.acos(abs(dot)))
                g1_max = max(g1_max, angle_deg)

            # G2: curvature difference
            c_a = _curvature_at(face_a, uv_a.X(), uv_a.Y())
            c_b = _curvature_at(face_b, uv_b.X(), uv_b.Y())
            if c_a is not None and c_b is not None:
                denom = max(abs(c_a), abs(c_b), 1e-12)
                diff_pct = abs(c_a - c_b) / denom * 100
                g2_max = max(g2_max, diff_pct)

        pass_g0 = g0_max <= g0_tol
        pass_g1 = g1_max <= g1_tol_deg
        pass_g2 = g2_max <= g2_tol_pct
        if pass_g0:
            g0_pass_count += 1
        if pass_g1:
            g1_pass_count += 1
        if pass_g2:
            g2_pass_count += 1

        edges_result.append(
            {
                "edge_idx": total_edges - 1,
                "g0_max_mm": g0_max,
                "g1_max_deg": g1_max,
                "g2_max_pct": g2_max,
                "pass_g0": pass_g0,
                "pass_g1": pass_g1,
                "pass_g2": pass_g2,
            }
        )

    return {
        "edges": edges_result,
        "summary": {
            "total_edges": total_edges,
            "g0_pass": g0_pass_count,
            "g1_pass": g1_pass_count,
            "g2_pass": g2_pass_count,
            "all_pass": (
                g0_pass_count == total_edges
                and g1_pass_count == total_edges
                and g2_pass_count == total_edges
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. DRAFT ANALYSIS — angle between face normals and a pull direction
# ═══════════════════════════════════════════════════════════════════════════


def draft_analysis(
    shape: TopoDS_Shape,
    pull_direction: list[float],
    n_u: int = 10,
    n_v: int = 10,
) -> dict[str, Any]:
    """Analyze draft angles of each face relative to a pull direction.

    Returns per-face: min/max/avg signed draft angle in degrees, measured on the face's outward
    side: a wall along the pull is 0, a face looking along the pull is +90, one looking against
    it is -90. ``undercut`` marks a face with a negative angle (it would lock in the mould).
    """
    if n_u < 2 or n_v < 2:
        raise GeomError("GEOM_INVALID_PARAM", "n_u and n_v must be >= 2")
    dx, dy, dz = [float(x) for x in pull_direction]
    if not (math.isfinite(dx) and math.isfinite(dy) and math.isfinite(dz)):
        raise GeomError("GEOM_INVALID_PARAM", "pull direction must be finite")
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag < 1e-10:
        raise GeomError("GEOM_INVALID_PARAM", "pull direction must be non-zero")
    pull = gp_Dir(dx / mag, dy / mag, dz / mag)

    faces: list[Any] = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        faces.append(TopoDS.Face(ex.Current()))
        ex.Next()
    if len(faces) > MAX_FACES:
        raise GeomError("GEOM_INVALID_PARAM", f"too many faces (max {MAX_FACES})")
    if len(faces) * n_u * n_v > MAX_SAMPLE_POINTS:
        raise GeomError(
            "GEOM_INVALID_PARAM",
            f"too many draft samples ({len(faces) * n_u * n_v} > {MAX_SAMPLE_POINTS})",
        )

    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_OUT

    faces_result: list[dict[str, Any]] = []
    clf = BRepClass_FaceClassifier()
    for face_idx, face in enumerate(faces):
        adaptor = BRepAdaptor_Surface(face)
        u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
        v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()

        angles: list[float] = []
        for i in range(n_u):
            u = u0 + (u1 - u0) * i / max(n_u - 1, 1)
            for j in range(n_v):
                v = v0 + (v1 - v0) * j / max(n_v - 1, 1)
                clf.Perform(face, gp_Pnt2d(u, v), 1e-7)
                if clf.State() == TopAbs_OUT:
                    continue
                p = gp_Pnt()
                d1u = gp_Vec()
                d1v = gp_Vec()
                adaptor.D1(u, v, p, d1u, d1v)
                normal = d1u.Crossed(d1v)
                if normal.Magnitude() < 1e-12:
                    continue
                normal.Normalize()
                if face.Orientation() == TopAbs_REVERSED:
                    normal.Reverse()
                dot = max(-1.0, min(1.0, normal.Dot(gp_Vec(pull))))
                angles.append(math.degrees(math.asin(dot)))

        if angles:
            faces_result.append(
                {
                    "face_idx": face_idx,
                    "draft_min_deg": min(angles),
                    "draft_max_deg": max(angles),
                    "draft_avg_deg": sum(angles) / len(angles),
                    "undercut": min(angles) < -1e-9,
                    "n_samples": len(angles),
                }
            )

    return {"faces": faces_result, "pull_direction": [dx / mag, dy / mag, dz / mag]}


# ═══════════════════════════════════════════════════════════════════════════
# 4. ZEBRA / HIGHLIGHT LINES — environment reflection directions
# ═══════════════════════════════════════════════════════════════════════════


def zebra_stripes(
    face_shape: TopoDS_Shape,
    stripe_dir: list[float] | None = None,
    n_u: int = 40,
    n_v: int = 40,
) -> dict[str, Any]:
    """Compute zebra stripe intensity at UV grid points for visual analysis.

    Simulates parallel light strips reflected off the surface.
    Each sample returns the stripe intensity (0/1 alternating bands based on
    reflected direction dot with stripe_dir).

    This data is used by the frontend shader/renderer.
    """
    if stripe_dir is None:
        stripe_dir = [0.0, 0.0, 1.0]  # vertical stripes

    sx, sy, sz = [float(x) for x in stripe_dir]
    smag = math.sqrt(sx * sx + sy * sy + sz * sz)
    if smag < 1e-10:
        raise GeomError("GEOM_INVALID_PARAM", "stripe direction must be non-zero")
    sx, sy, sz = sx / smag, sy / smag, sz / smag

    ex = TopExp_Explorer(face_shape, TopAbs_FACE)
    if not ex.More():
        raise GeomError("GEOM_INVALID_PARAM", "input has no face")
    face = TopoDS.Face(ex.Current())

    adaptor = BRepAdaptor_Surface(face)
    u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()

    samples: list[dict[str, float]] = []
    stripe_freq = 10.0  # stripe frequency

    for i in range(n_u):
        u = u0 + (u1 - u0) * i / max(n_u - 1, 1)
        for j in range(n_v):
            v = v0 + (v1 - v0) * j / max(n_v - 1, 1)
            p = gp_Pnt()
            d1u = gp_Vec()
            d1v = gp_Vec()
            adaptor.D1(u, v, p, d1u, d1v)
            normal = d1u.Crossed(d1v)
            if normal.Magnitude() < 1e-12:
                continue
            normal.Normalize()

            # Project the surface normal onto the stripe direction
            # This gives a measure of how the surface curves relative to stripes
            proj = normal.X() * sx + normal.Y() * sy + normal.Z() * sz

            # Stripe intensity: sin function → 0/1 bands
            intensity = 0.5 + 0.5 * math.sin(proj * stripe_freq * math.pi)
            stripe = 1 if intensity > 0.5 else 0

            samples.append(
                {
                    "u": u,
                    "v": v,
                    "x": p.X(),
                    "y": p.Y(),
                    "z": p.Z(),
                    "intensity": intensity,
                    "stripe": stripe,
                }
            )

    return {
        "samples": samples,
        "stripe_dir": [sx, sy, sz],
        "n_u": n_u,
        "n_v": n_v,
    }


# ═══════════════════════════════════════════════════════════════════════════
# JOB ENTRY POINTS
# ═══════════════════════════════════════════════════════════════════════════


def curvature_map_job(
    face_brep: bytes,
    n_u: int = 20,
    n_v: int = 20,
) -> dict[str, Any]:
    shape = from_brep(face_brep)
    return curvature_map(shape, n_u, n_v)


def continuity_check_job(
    body_brep: bytes,
    n_samples: int = 20,
    g0_tol: float = 1e-3,
    g1_tol_deg: float = 1.0,
    g2_tol_pct: float = 5.0,
) -> dict[str, Any]:
    shape = from_brep(body_brep)
    return continuity_check(shape, n_samples, g0_tol, g1_tol_deg, g2_tol_pct)


def draft_analysis_job(
    body_brep: bytes,
    pull_direction: list[float],
    n_u: int = 10,
    n_v: int = 10,
) -> dict[str, Any]:
    shape = from_brep(body_brep)
    return draft_analysis(shape, pull_direction, n_u, n_v)


def zebra_stripes_job(
    face_brep: bytes,
    stripe_dir: list[float] | None = None,
    n_u: int = 40,
    n_v: int = 40,
) -> dict[str, Any]:
    shape = from_brep(face_brep)
    return zebra_stripes(shape, stripe_dir, n_u, n_v)
