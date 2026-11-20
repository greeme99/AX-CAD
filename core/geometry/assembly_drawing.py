"""Assembly A7: Exploded Assembly 2D Drawing DXF with Auto-Balloons & Parts List Table."""

from __future__ import annotations

import io
import math
from typing import Any

import ezdxf


def generate_assembly_drawing_dxf(
    components: list[dict[str, Any]],
    *,
    explode_steps: list[dict[str, Any]] | None = None,
    explode_factor: float = 1.0,
    title: str = "ASSEMBLY DRAWING",
) -> bytes:
    """Generate an R2018 DXF assembly drawing with optional exploded projection, numbered balloons, and Parts List BOM."""
    doc = ezdxf.new("R2018")
    doc.units = 4  # mm
    for lname, color in (("OUTLINE", 7), ("EXPLODE_LINE", 8), ("BALLOON", 1), ("BOM_TABLE", 3)):
        if lname not in doc.layers:
            doc.layers.add(lname, color=color)

    msp = doc.modelspace()
    f = max(0.0, min(1.0, float(explode_factor)))
    steps = explode_steps or []

    # Compute exploded XY projection rectangles & balloon anchor points
    drawn_items: list[dict[str, Any]] = []
    max_x = 100.0
    max_y = 100.0

    for idx, comp in enumerate(components, start=1):
        cid = int(comp["component_id"])
        name = str(comp.get("name", f"Part-{cid}"))
        pl = comp.get("placement", {"t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]})
        tx, ty, tz = float(pl["t"][0]), float(pl["t"][1]), float(pl["t"][2])
        ox, oy = tx, ty

        # Apply explode steps
        for st in steps:
            if cid in [int(x) for x in st.get("component_ids", [])]:
                dx, dy, dz = (float(v) for v in st.get("direction", [0.0, 0.0, 0.0]))
                nrm = math.sqrt(dx * dx + dy * dy + dz * dz)
                if nrm > 1e-12:
                    dist = float(st.get("distance_mm", 0.0)) * f
                    tx += (dx / nrm) * dist
                    ty += (dy / nrm) * dist
                    tz += (dz / nrm) * dist

        bb = comp.get("bbox")
        if bb and "min" in bb and "max" in bb:
            w = max(5.0, float(bb["max"][0]) - float(bb["min"][0]))
            h = max(5.0, float(bb["max"][1]) - float(bb["min"][1]))
            d = max(5.0, float(bb["max"][2]) - float(bb["min"][2]))
        else:
            w, h, d = 40.0, 25.0, 15.0

        # Draw projected front-view rectangle
        rect = [(tx, ty), (tx + w, ty), (tx + w, ty + h), (tx, ty + h)]
        msp.add_lwpolyline(rect, close=True, dxfattribs={"layer": "OUTLINE"})

        # Draw explode trail centerline when displaced
        if math.hypot(tx - ox, ty - oy) > 1e-3:
            msp.add_line(
                (ox + 0.5 * w, oy + 0.5 * h),
                (tx + 0.5 * w, ty + 0.5 * h),
                dxfattribs={"layer": "EXPLODE_LINE"},
            )

        # Draw numbered Balloon (leader + circle + item number text)
        anchor = (tx + w, ty + h)
        balloon_center = (tx + w + 18.0, ty + h + 15.0 + (idx % 3) * 12.0)
        balloon_r = 5.0
        msp.add_line(anchor, balloon_center, dxfattribs={"layer": "BALLOON"})
        msp.add_circle(balloon_center, balloon_r, dxfattribs={"layer": "BALLOON"})
        msp.add_text(
            str(idx),
            height=4.0,
            dxfattribs={
                "layer": "BALLOON",
                "insert": (balloon_center[0] - 1.5, balloon_center[1] - 2.0),
            },
        )

        max_x = max(max_x, balloon_center[0] + 20.0)
        max_y = max(max_y, balloon_center[1] + 20.0)
        drawn_items.append(
            {"item_no": idx, "name": name, "qty": 1, "size": f"{w:.1f}x{h:.1f}x{d:.1f}"}
        )

    # Draw Parts List (BOM) table to the right of the view
    tbl_x = max_x + 20.0
    tbl_y = max_y
    row_h = 8.0
    col_w = [16.0, 56.0, 16.0, 42.0]
    total_w = sum(col_w)

    # Title banner
    msp.add_text(
        title, height=6.0, dxfattribs={"layer": "BOM_TABLE", "insert": (tbl_x, tbl_y + 4.0)}
    )

    rows = [["ITEM", "PART NAME", "QTY", "SIZE (mm)"]] + [
        [str(it["item_no"]), it["name"][:24], str(it["qty"]), it["size"]] for it in drawn_items
    ]
    for r_idx, row in enumerate(rows):
        y_top = tbl_y - r_idx * row_h
        y_bot = y_top - row_h
        msp.add_lwpolyline(
            [(tbl_x, y_top), (tbl_x + total_w, y_top), (tbl_x + total_w, y_bot), (tbl_x, y_bot)],
            close=True,
            dxfattribs={"layer": "BOM_TABLE"},
        )
        cx = tbl_x
        for c_idx, cell in enumerate(row):
            if c_idx > 0:
                msp.add_line((cx, y_top), (cx, y_bot), dxfattribs={"layer": "BOM_TABLE"})
            msp.add_text(
                cell,
                height=3.2,
                dxfattribs={"layer": "BOM_TABLE", "insert": (cx + 2.0, y_bot + 2.2)},
            )
            cx += col_w[c_idx]

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
