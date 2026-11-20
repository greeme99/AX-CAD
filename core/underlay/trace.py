"""2D Overlay Auto-Trace & Scale/Dimension Detection for Vector & Raster PDFs.

Zero-new-dependency implementation using pypdfium2 + Pillow + NumPy + SciPy:
  1. Detects explicit scale notations (e.g. 1:50, SCALE 1/100) and dimension texts on the page.
     If neither scale nor dimensions exist (or the page is a raw scanned raster image), flags
     `needs_reference_dimension = True` so the UI prompts the user for a reference dimension.
  2. For vector pages: cleans & normalizes paths/arcs into CAD LINE / LWPOLYLINE / CIRCLE / ARC.
  3. For raster/scanned PDF pages: renders grayscale PNG, binarizes via Otsu threshold, traces
     connected-component boundaries (Moore-neighbor contour walk), applies Douglas-Peucker
     simplification, orthogonal angle snapping, and circle detection.
"""

from __future__ import annotations

import io
import math
import re
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from . import UnderlayError, pdf, xps

_SCALE_RE = re.compile(
    r"(?:SCALE|척도|축척|S\s*=)?\s*1\s*[:/]\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_DIM_RE = re.compile(
    r"(?:[ØΦøφR]\s*\d+(?:\.\d+)?|^\d{2,5}(?:\.\d+)?(?:\s*mm)?$)",
    re.IGNORECASE,
)


def detect_scale_and_dimensions(vec: dict[str, Any]) -> tuple[float | None, bool]:
    """Inspect extracted PDF/DWFx text items for scale notation and dimension values.

    Returns (scale_detected, has_dimensions).
    Example: "SCALE 1:50" -> scale_detected = 50.0.
    """
    scale_detected: float | None = None
    dim_hits = 0
    for item in vec.get("texts", []):
        txt = str(item.get("v", "")).strip()
        if not txt:
            continue
        if scale_detected is None:
            m = _SCALE_RE.search(txt)
            if m:
                denom = float(m.group(1))
                if 0.01 <= denom <= 10000.0:
                    scale_detected = denom
        if _DIM_RE.search(txt):
            dim_hits += 1
    return scale_detected, dim_hits > 0


def _otsu_threshold(gray: np.ndarray) -> int:
    """Compute Otsu global threshold on uint8 grayscale image."""
    hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 256))
    total = float(gray.size)
    if total <= 0:
        return 180
    sum_total = float(np.dot(np.arange(256, dtype=np.float64), hist))
    sum_b = 0.0
    w_b = 0.0
    max_var = -1.0
    best_t = 180
    for t in range(256):
        w_b += float(hist[t])
        if w_b == 0.0:
            continue
        w_f = total - w_b
        if w_f == 0.0:
            break
        sum_b += t * float(hist[t])
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            best_t = t
    return max(40, min(235, best_t))


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker polyline simplification."""
    if len(pts) < 3:
        return pts
    ax, ay = pts[0]
    bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    denom = math.hypot(dx, dy)
    max_d = -1.0
    idx = 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        if denom <= 1e-12:
            d = math.hypot(px - ax, py - ay)
        else:
            d = abs(dy * px - dx * py + bx * ay - by * ax) / denom
        if d > max_d:
            max_d = d
            idx = i
    if max_d > eps:
        left = _rdp(pts[: idx + 1], eps)
        right = _rdp(pts[idx:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def _moore_trace(
    mask: np.ndarray, start_r: int, start_c: int, max_steps: int = 20000
) -> list[tuple[int, int]]:
    """Moore-neighbor boundary walk on a 2D boolean mask (clockwise)."""
    h, w = mask.shape
    # 8-neighbors clockwise starting from West
    dirs = [(0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1)]
    contour: list[tuple[int, int]] = [(start_r, start_c)]
    cur_r, cur_c = start_r, start_c
    back_dir = 0  # came from West
    for _ in range(max_steps):
        found = False
        for k in range(8):
            d_idx = (back_dir + k) % 8
            nr = cur_r + dirs[d_idx][0]
            nc = cur_c + dirs[d_idx][1]
            if 0 <= nr < h and 0 <= nc < w and mask[nr, nc]:
                cur_r, cur_c = nr, nc
                back_dir = (d_idx + 5) % 8
                found = True
                break
        if not found:
            break
        if cur_r == start_r and cur_c == start_c:
            break
        contour.append((cur_r, cur_c))
    return contour


def _polygon_area_perimeter(pts: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(pts)
    if n < 3:
        return 0.0, 0.0
    area2 = 0.0
    perim = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        area2 += x0 * y1 - x1 * y0
        perim += math.hypot(x1 - x0, y1 - y0)
    return abs(area2) * 0.5, perim


def _fit_circle(pts: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Return (cx, cy, r, max_rel_err) if closed contour is circular, else None."""
    if len(pts) < 12:
        return None
    area, perim = _polygon_area_perimeter(pts)
    if perim <= 1e-6:
        return None
    circularity = (4.0 * math.pi * area) / (perim * perim)
    if circularity < 0.82:
        return None
    arr = np.asarray(pts, dtype=np.float64)
    cx = float(np.mean(arr[:, 0]))
    cy = float(np.mean(arr[:, 1]))
    dists = np.hypot(arr[:, 0] - cx, arr[:, 1] - cy)
    r = float(np.mean(dists))
    if r <= 1e-6:
        return None
    rel_err = float(np.max(np.abs(dists - r)) / r)
    if rel_err > 0.10:
        return None
    return cx, cy, r, rel_err


def _snap_ortho(
    pts: list[tuple[float, float]], closed: bool, tol_deg: float
) -> list[tuple[float, float]]:
    """Snap near-horizontal and near-vertical segments to exact 0° / 90°."""
    if tol_deg <= 0 or len(pts) < 2:
        return pts
    out = [list(p) for p in pts]
    n = len(out)
    count = n if closed else n - 1
    for i in range(count):
        j = (i + 1) % n
        dx = out[j][0] - out[i][0]
        dy = out[j][1] - out[i][1]
        seg_len = math.hypot(dx, dy)
        if seg_len <= 1e-6:
            continue
        ang = abs(math.degrees(math.atan2(dy, dx))) % 180.0
        if ang <= tol_deg or abs(ang - 180.0) <= tol_deg:
            ym = 0.5 * (out[i][1] + out[j][1])
            out[i][1] = ym
            out[j][1] = ym
        elif abs(ang - 90.0) <= tol_deg:
            xm = 0.5 * (out[i][0] + out[j][0])
            out[i][0] = xm
            out[j][0] = xm
    return [(p[0], p[1]) for p in out]


def _trace_raster_image(
    png_bytes: bytes,
    page_w_mm: float,
    page_h_mm: float,
    *,
    threshold: int | None = None,
    simplify_mm: float = 0.8,
    min_length_mm: float = 2.0,
    ortho_snap_deg: float = 2.0,
) -> tuple[list[list[float]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Trace dark strokes/shapes in a rendered PDF PNG into page-mm lines, polylines & circles."""
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    gray = np.asarray(img, dtype=np.uint8)
    h_px, w_px = gray.shape
    if h_px < 4 or w_px < 4:
        return [], [], []

    th = _otsu_threshold(gray) if threshold is None else int(threshold)
    # Dark ink on light background
    ink = gray < th
    # Light morphological closing to bridge 1px scan gaps
    ink = ndimage.binary_closing(ink, structure=np.ones((3, 3), dtype=bool))
    labeled, _num = ndimage.label(ink)

    sx = page_w_mm / float(w_px)
    sy = page_h_mm / float(h_px)

    lines: list[list[float]] = []
    polylines: list[dict[str, Any]] = []
    circles: list[dict[str, Any]] = []

    slices = ndimage.find_objects(labeled)
    for comp_id, sl in enumerate(slices, start=1):
        if sl is None:
            continue
        sub_mask = labeled[sl] == comp_id
        r0 = int(sl[0].start)
        c0 = int(sl[1].start)
        bh, bw = sub_mask.shape
        # Skip tiny noise specks or full-page border artifacts
        if max(bh * sy, bw * sx) < min_length_mm:
            continue
        if bh >= h_px - 2 and bw >= w_px - 2:
            continue

        # Find top-leftmost True pixel in sub_mask
        rs, cs = np.nonzero(sub_mask)
        if rs.size < 4:
            continue
        sr, sc = int(rs[0]), int(cs[0])
        raw_rc = _moore_trace(sub_mask, sr, sc)
        if len(raw_rc) < 4:
            continue

        # Convert pixel (row, col) -> PDF page mm (origin at bottom-left, Y upward)
        pts_mm = [((c0 + c + 0.5) * sx, page_h_mm - (r0 + r + 0.5) * sy) for (r, c) in raw_rc]

        # 1. Check circular shape first
        circ = _fit_circle(pts_mm)
        if circ is not None:
            cx, cy, r_mm, _ = circ
            if r_mm * 2.0 >= min_length_mm:
                circles.append({"center": [round(cx, 3), round(cy, 3)], "radius": round(r_mm, 3)})
                continue

        # 2. Simplify closed contour with RDP (split at farthest pair for closed stability)
        arr = np.asarray(pts_mm, dtype=np.float64)
        p0 = arr[0]
        d_far = np.hypot(arr[:, 0] - p0[0], arr[:, 1] - p0[1])
        far_idx = int(np.argmax(d_far))
        if far_idx <= 0 or far_idx >= len(pts_mm) - 1:
            simp = _rdp(pts_mm, simplify_mm)
        else:
            half1 = _rdp(pts_mm[: far_idx + 1], simplify_mm)
            half2 = _rdp(pts_mm[far_idx:] + [pts_mm[0]], simplify_mm)
            simp = half1[:-1] + half2[:-1]

        if len(simp) < 2:
            continue

        # Thin stroke collapse: if simplified contour has 2 vertices or tiny area/perim ratio, emit a single LINE
        area, perim = _polygon_area_perimeter(simp)
        if len(simp) == 2 or (
            perim > 1e-6 and (2.0 * area / perim) < max(simplify_mm * 1.5, 1.5) and len(simp) <= 4
        ):
            # Take farthest two endpoints as a clean straight line
            best_pair = (simp[0], simp[-1])
            best_d = 0.0
            for i in range(len(simp)):
                for j in range(i + 1, len(simp)):
                    d = math.hypot(simp[j][0] - simp[i][0], simp[j][1] - simp[i][1])
                    if d > best_d:
                        best_d = d
                        best_pair = (simp[i], simp[j])
            if best_d >= min_length_mm:
                snapped = _snap_ortho([best_pair[0], best_pair[1]], False, ortho_snap_deg)
                lines.append(
                    [
                        round(snapped[0][0], 3),
                        round(snapped[0][1], 3),
                        round(snapped[1][0], 3),
                        round(snapped[1][1], 3),
                    ]
                )
            continue

        # Closed polygon / rectangle
        snapped = _snap_ortho(simp, True, ortho_snap_deg)
        # Drop duplicate consecutive vertices
        clean_pts: list[list[float]] = []
        for x, y in snapped:
            if not clean_pts or math.hypot(x - clean_pts[-1][0], y - clean_pts[-1][1]) > 1e-3:
                clean_pts.append([round(x, 3), round(y, 3)])
        if len(clean_pts) >= 3:
            polylines.append({"points": clean_pts, "closed": True})
        elif (
            len(clean_pts) == 2
            and math.hypot(clean_pts[1][0] - clean_pts[0][0], clean_pts[1][1] - clean_pts[0][1])
            >= min_length_mm
        ):
            lines.append([clean_pts[0][0], clean_pts[0][1], clean_pts[1][0], clean_pts[1][1]])

    return lines, polylines, circles


def _trace_from_vectors(
    vec: dict[str, Any],
    *,
    min_length_mm: float = 0.5,
) -> tuple[list[list[float]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert extracted PDF/DWFx vectors into normalized trace primitives."""
    lines: list[list[float]] = []
    polylines: list[dict[str, Any]] = []
    circles: list[dict[str, Any]] = []
    arcs: list[dict[str, Any]] = []

    for pa in vec.get("paths", []):
        raw_p = pa.get("p", [])
        pts = [
            [round(float(raw_p[i]), 3), round(float(raw_p[i + 1]), 3)]
            for i in range(0, len(raw_p) - 1, 2)
        ]
        if len(pts) < 2:
            continue
        closed = bool(pa.get("z") == 1 or pa.get("f") == 1)
        if len(pts) == 2 and not closed:
            if math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) >= min_length_mm:
                lines.append([pts[0][0], pts[0][1], pts[1][0], pts[1][1]])
        else:
            polylines.append({"points": pts, "closed": closed})

    for a in vec.get("arcs", []):
        cx, cy = round(float(a["c"][0]), 3), round(float(a["c"][1]), 3)
        r = round(float(a["r"]), 3)
        if r * 2.0 < min_length_mm:
            continue
        if "a" in a and a["a"] is not None:
            arcs.append(
                {
                    "center": [cx, cy],
                    "radius": r,
                    "start_angle": round(float(a["a"][0]), 3),
                    "end_angle": round(float(a["a"][1]), 3),
                }
            )
        else:
            circles.append({"center": [cx, cy], "radius": r})

    return lines, polylines, circles, arcs


def _compute_bbox(
    lines: list[list[float]],
    polylines: list[dict[str, Any]],
    circles: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
) -> list[float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for x1, y1, x2, y2 in lines:
        xs.extend((x1, x2))
        ys.extend((y1, y2))
    for pl in polylines:
        for x, y in pl["points"]:
            xs.append(x)
            ys.append(y)
    for c in circles:
        cx, cy = c["center"]
        r = c["radius"]
        xs.extend((cx - r, cx + r))
        ys.extend((cy - r, cy + r))
    for a in arcs:
        cx, cy = a["center"]
        r = a["radius"]
        xs.extend((cx - r, cx + r))
        ys.extend((cy - r, cy + r))
    if not xs:
        return None
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


def trace_page(
    kind: str,
    path: str,
    page: int,
    *,
    mode: str = "auto",
    threshold: int | None = None,
    simplify_mm: float = 0.8,
    min_length_mm: float = 2.0,
    ortho_snap_deg: float = 2.0,
    render_width_px: int = 1600,
) -> dict[str, Any]:
    """Run hybrid vector/raster auto-trace and scale/dimension detection on one underlay page."""
    if mode not in ("auto", "vector", "raster"):
        raise UnderlayError("UNDERLAY_TRACE_MODE", f"unsupported trace mode: {mode}")

    vec = pdf.page_vectors(path, page) if kind == "pdf" else xps.page_vectors(path, page)
    page_w, page_h = float(vec["size"][0]), float(vec["size"][1])
    scale_detected, has_dimensions = detect_scale_and_dimensions(vec)
    is_raster = bool(vec.get("raster_only", False))

    use_raster = mode == "raster" or (mode == "auto" and is_raster)
    if use_raster:
        if kind != "pdf":
            raise UnderlayError(
                "UNDERLAY_TRACE_UNSUPPORTED", "래스터 자동 따라그리기는 PDF만 지원합니다"
            )
        png = pdf.render(path, page, render_width_px, [], None)
        lines, polylines, circles = _trace_raster_image(
            png,
            page_w,
            page_h,
            threshold=threshold,
            simplify_mm=simplify_mm,
            min_length_mm=min_length_mm,
            ortho_snap_deg=ortho_snap_deg,
        )
        arcs: list[dict[str, Any]] = []
        source = "raster"
    else:
        lines, polylines, circles, arcs = _trace_from_vectors(
            vec, min_length_mm=min(min_length_mm, 0.5)
        )
        source = "vector"

    bbox = _compute_bbox(lines, polylines, circles, arcs)
    # Prompt user for reference dimension if neither scale nor dimensions were detected on the page
    needs_ref = scale_detected is None and not has_dimensions

    return {
        "source": source,
        "page_size_mm": [round(page_w, 3), round(page_h, 3)],
        "scale_detected": scale_detected,
        "has_dimensions": has_dimensions,
        "needs_reference_dimension": needs_ref,
        "traced_bbox_mm": bbox,
        "lines": lines,
        "polylines": polylines,
        "circles": circles,
        "arcs": arcs,
        "stats": {
            "line_count": len(lines),
            "polyline_count": len(polylines),
            "circle_count": len(circles),
            "arc_count": len(arcs),
        },
    }
