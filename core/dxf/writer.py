"""Apply mm-based edits to a DXF in place (handles of untouched/modified entities are preserved)."""

import os
from typing import Any

import ezdxf

from core.dxf.reader import MAX_FILE_BYTES, DxfError, is_editable, unit_scale


def _set(e: Any, g: dict[str, Any], s: float) -> None:
    d = e.dxf
    if g["type"] == "LINE":
        d.start, d.end = (g["start"][0] / s, g["start"][1] / s), (g["end"][0] / s, g["end"][1] / s)
    elif g["type"] in ("CIRCLE", "ARC"):
        d.center, d.radius = (g["center"][0] / s, g["center"][1] / s), g["radius"] / s
        if g["type"] == "ARC":
            d.start_angle, d.end_angle = g["start_angle"], g["end_angle"]
    elif g["type"] == "LWPOLYLINE":
        e.set_points([(x / s, y / s, b) for x, y, b in g["points"]], format="xyb")
        e.closed = g["closed"]
    else:
        d.insert, d.height = (g["insert"][0] / s, g["insert"][1] / s), g["height"] / s
        d.text, d.rotation = g["value"], g["rotation"]


def _dim(
    msp: Any,
    g: dict[str, Any],
    layer: str,
    s: float,
    keep: tuple[str, dict[str, Any]] | None = None,
) -> None:
    """New dimension; keep = (dimstyle, overrides) of the dimension it replaces."""

    def pt(p: list[float]) -> tuple[float, float]:
        return (p[0] / s, p[1] / s)

    # ponytail: measurement text is in drawing units (inch drawings show inches); set dimlfac=1/s if mm display is required
    kw: dict[str, Any] = {
        "override": {
            "dimtxt": 2.5 / s,
            "dimasz": 2.5 / s,
            "dimdec": 2,
            "dimdsep": 46,
            "dimlfac": 1,
            "dimzin": 0,
            "dimrnd": 0.01,  # a stored dimrnd of 0 would round to integers
        },
        "dxfattribs": {"layer": layer},
    }
    if keep:
        kw["dimstyle"], kw["override"] = keep
    if g["type"] == "DIM_LINEAR":
        dim = msp.add_linear_dim(
            base=pt(g["base"]), p1=pt(g["p1"]), p2=pt(g["p2"]), angle=g["angle"], **kw
        )
    elif g["type"] == "DIM_ALIGNED":
        dim = msp.add_aligned_dim(pt(g["p1"]), pt(g["p2"]), distance=g["distance"] / s, **kw)
    elif g["type"] == "DIM_ANGULAR":
        dim = msp.add_angular_dim_3p(
            base=pt(g["base"]), center=pt(g["center"]), p1=pt(g["p1"]), p2=pt(g["p2"]), **kw
        )
    else:
        add = msp.add_radius_dim if g["type"] == "DIM_RADIUS" else msp.add_diameter_dim
        dim = add(center=pt(g["center"]), radius=g["radius"] / s, angle=g["angle"], **kw)
    dim.render()


def apply_edits(src_path: str, dst_path: str, edits: dict[str, Any]) -> None:
    doc = ezdxf.readfile(src_path)  # strict, like parsing
    s = unit_scale(doc)
    msp = doc.modelspace()

    def layer_ok(name: str) -> None:
        if not doc.layers.has_entry(name):
            raise DxfError("EDIT_LAYER_NOT_FOUND", "Layer not found", 422)
        if doc.layers.get(name).is_locked():
            raise DxfError("EDIT_LAYER_LOCKED", "Layer is locked", 422)

    def target(handle: str, deleting: bool = False) -> Any:
        e = doc.entitydb.get(handle.upper())
        if e is None or e.dxf.owner != msp.layout_key:
            raise DxfError("EDIT_HANDLE_NOT_FOUND", "Entity not found or not editable", 422)
        # any dimension may be deleted; only plain ones (no text override) are rebuilt
        if not (is_editable(e) or (deleting and e.dxftype() == "DIMENSION")):
            raise DxfError("EDIT_HANDLE_NOT_FOUND", "Entity not found or not editable", 422)
        layer_ok(e.dxf.layer)
        return e

    def delete(e: Any) -> None:
        block = e.dxf.get("geometry") if e.dxftype() == "DIMENSION" else None
        msp.delete_entity(e)
        if block and block in doc.blocks:  # rendered *D block would otherwise be orphaned
            doc.blocks.delete_block(block, safe=False)

    for h in edits.get("deleted", []):
        delete(target(h, deleting=True))
    for m in edits.get("modified", []):
        e = target(m["handle"])
        is_dim = e.dxftype() == "DIMENSION"
        kind = m["geom"]["type"]
        if (not kind.startswith("DIM_")) if is_dim else (kind != e.dxftype()):
            raise DxfError("EDIT_INVALID", "Geometry type does not match entity", 422)
        layer_ok(m["layer"])
        if is_dim:  # rebuilt with its own style (the handle changes)
            keep = (e.dxf.dimstyle, dict(e.override().dimstyle_attribs))
            delete(e)
            _dim(msp, m["geom"], m["layer"], s, keep)
            continue
        e.dxf.layer = m["layer"]
        _set(e, m["geom"], s)
    for c in edits.get("created", []):
        layer_ok(c["layer"])
        if c["geom"]["type"].startswith("DIM_"):
            _dim(msp, c["geom"], c["layer"], s)
            continue
        _set(msp.new_entity(c["geom"]["type"], {"layer": c["layer"]}), c["geom"], s)
    # ponytail: keeps source DXF version, add R2018 export option if partners need it
    doc.saveas(dst_path)
    if os.path.getsize(dst_path) > MAX_FILE_BYTES:  # chained edits must not outgrow uploads
        raise DxfError("FILE_TOO_LARGE", "Edited drawing exceeds 50 MB", 413)
