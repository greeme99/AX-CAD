"""Apply mm-based edits to a DXF in place (handles of untouched/modified entities are preserved)."""

import os
from collections import Counter
from typing import Any

import ezdxf
from ezdxf.lldxf.validator import is_valid_block_name, is_valid_layer_name
from ezdxf.math import fit_points_to_cad_cv
from ezdxf.tools.pattern import ISO_PATTERN

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
    elif g["type"] == "POINT":
        d.location = (g["point"][0] / s, g["point"][1] / s)
    elif g["type"] == "ELLIPSE":
        d.center = (g["center"][0] / s, g["center"][1] / s)
        d.major_axis = (g["major"][0] / s, g["major"][1] / s)
        d.ratio, d.start_param, d.end_param = g["ratio"], g["start_param"], g["end_param"]
    elif g["type"] == "SPLINE":  # the curve CAD programs draw through the fit points
        fit = [(x / s, y / s) for x, y in g["fit"]]
        e.apply_construction_tool(fit_points_to_cad_cv(fit))
        e.fit_points = fit
    elif g["type"] == "LEADER":
        e.set_vertices([(x / s, y / s) for x, y in g["vertices"]])
    elif g["type"] == "HATCH":
        if g["pattern"] != "SOLID" and g["pattern"] not in ISO_PATTERN:
            raise DxfError("EDIT_PATTERN_NOT_FOUND", "Hatch pattern not found", 422)
        e.paths.clear()  # also drops the link to boundary objects (no associativity kept)
        e.dxf.associative = 0
        e.paths.add_polyline_path([(x / s, y / s, b) for x, y, b in g["boundary"]], is_closed=True)
        if g["pattern"] == "SOLID":
            e.set_solid_fill(style=0)
        else:  # pattern scale is a factor, its unit is the drawing's
            e.set_pattern_fill(g["pattern"], scale=g["scale"], angle=g["angle"], style=0)
    else:
        d.insert, d.height = (g["insert"][0] / s, g["insert"][1] / s), g["height"] / s
        d.text, d.rotation = g["value"], g["rotation"]


def _dim(
    msp: Any,
    g: dict[str, Any],
    layer: str,
    s: float,
    keep: tuple[str, dict[str, Any]] | None = None,
) -> Any:
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
    return dim.dimension


PROTECTED_LAYERS = ("0", "DEFPOINTS")  # names DXF readers rely on


def _insertable(doc: Any, name: str) -> Any:
    b = doc.blocks.get(name)
    if b is None or name.startswith("*") or b.is_any_layout or b.block.is_xref:
        raise DxfError("EDIT_BLOCK_NOT_FOUND", "Block not found", 422)
    return b


def _place(e: Any, g: dict[str, Any], s: float) -> None:
    """Set a block reference to an INSERT geom (mm). Attributes keep their place on the block:
    ezdxf's own transform misplaces them on mirrored references, so they are mapped here."""
    undo = e.matrix44()
    undo.inverse()  # in place
    keep = [(a, undo.transform(a.dxf.insert), a.dxf.get("align_point")) for a in e.attribs]
    keep = [(a, p, undo.transform(q) if q is not None else None) for a, p, q in keep]
    turn, grow = g["rotation"] - e.dxf.rotation, abs(g["yscale"] / e.dxf.yscale)
    e.dxf.insert = (g["insert"][0] / s, g["insert"][1] / s)
    e.dxf.xscale, e.dxf.yscale, e.dxf.rotation = g["xscale"], g["yscale"], g["rotation"]
    m = e.matrix44()
    for a, p, q in keep:
        a.dxf.insert = m.transform(p)
        if q is not None:
            a.dxf.align_point = m.transform(q)
        a.dxf.rotation = (a.dxf.rotation + turn) % 360  # text stays readable (MIRRTEXT 0)
        a.dxf.height *= grow


def _layer_op(doc: Any, op: dict[str, Any]) -> None:
    name = op["name"]
    if op.get("create"):
        if not is_valid_layer_name(name):
            raise DxfError("EDIT_LAYER_INVALID", "Invalid layer name", 422)
        if doc.layers.has_entry(name):
            raise DxfError("EDIT_LAYER_EXISTS", "Layer already exists", 422)
        doc.layers.add(name)
    elif not doc.layers.has_entry(name):
        raise DxfError("EDIT_LAYER_NOT_FOUND", "Layer not found", 422)
    layer = doc.layers.get(name)
    if op.get("linetype") is not None:
        if op["linetype"] not in doc.linetypes:
            raise DxfError("EDIT_LINETYPE_NOT_FOUND", "Linetype not found", 422)
        layer.dxf.linetype = op["linetype"]
    if op.get("color") is not None:
        layer.color = op["color"]  # keeps the layer's on/off state (sign of the stored colour)
        layer.dxf.discard("true_color")
    if op.get("locked") is not None:
        if op["locked"]:
            layer.lock()
        else:
            layer.unlock()
    new = op.get("rename")
    if new is not None and new != name:
        if name.upper() in PROTECTED_LAYERS or not is_valid_layer_name(new):
            raise DxfError("EDIT_LAYER_INVALID", "Layer cannot be renamed to this name", 422)
        if doc.layers.has_entry(new):
            raise DxfError("EDIT_LAYER_EXISTS", "Layer already exists", 422)
        layer.rename(new)  # also updates the entities and blocks that use it


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

    def linetype_ok(name: str | None) -> None:
        if name and name.upper() not in ("BYLAYER", "BYBLOCK") and name not in doc.linetypes:
            raise DxfError("EDIT_LINETYPE_NOT_FOUND", "Linetype not found", 422)

    def props(item: dict[str, Any]) -> dict[str, Any]:
        """Entity colour/linetype the edit sets (none given: left as is)."""
        linetype_ok(item.get("linetype"))
        return {k: item[k] for k in ("color", "linetype") if item.get(k) is not None}

    def style(e: Any, attribs: dict[str, Any]) -> None:
        if "color" in attribs:
            e.dxf.discard("true_color")  # a true colour would hide the ACI choice
        for k, v in attribs.items():
            e.dxf.set(k, v)

    dim_blocks = Counter(d.dxf.get("geometry") for d in doc.entitydb.query("DIMENSION"))

    def delete(e: Any) -> None:
        block = e.dxf.get("geometry") if e.dxftype() == "DIMENSION" else None
        msp.delete_entity(e)
        if not block:
            return
        # its rendered *D block would otherwise be orphaned; a name taken from the file may point
        # anywhere, so only an anonymous dimension block no other dimension uses goes (BUG-48)
        dim_blocks[block] -= 1
        if block.upper().startswith("*D") and block in doc.blocks and dim_blocks[block] <= 0:
            doc.blocks.delete_block(block, safe=False)

    def create(layout: Any, c: dict[str, Any]) -> None:
        g, attribs = c["geom"], props(c)
        if g["type"].startswith("DIM_"):
            style(_dim(layout, g, c["layer"], s), attribs)
            return
        if g["type"] == "LEADER":  # arrowhead sized like the editor's dimensions
            style_name = "Standard" if "Standard" in doc.dimstyles else "EZDXF"
            e = layout.add_leader(
                [(x / s, y / s) for x, y in g["vertices"]],
                dimstyle=style_name,
                override={"dimasz": 2.5 / s},
                dxfattribs={"layer": c["layer"]},
            )
            style(e, attribs)
            return
        if g["type"] == "INSERT":
            _insertable(doc, g["name"])
            e = layout.add_blockref(g["name"], (0, 0), dxfattribs={"layer": c["layer"]})
            if any(True for _ in doc.blocks.get(g["name"]).attdefs()):
                e.add_auto_attribs({})  # attribute defaults, then moved with the block
            _place(e, g, s)
            style(e, attribs)
            return
        e = layout.new_entity(g["type"], {"layer": c["layer"]})
        _set(e, g, s)
        style(e, attribs)

    # new blocks first: the INSERTs created below may use them
    for b in edits.get("blocks", []):
        name = b["name"]
        if name.startswith("*") or not is_valid_block_name(name):
            raise DxfError("EDIT_BLOCK_INVALID", "Invalid block name", 422)
        if name in doc.blocks:
            raise DxfError("EDIT_BLOCK_EXISTS", "Block already exists", 422)
        layout = doc.blocks.new(name, base_point=(b["base"][0] / s, b["base"][1] / s))
        for c in b["entities"]:
            if not doc.layers.has_entry(c["layer"]):
                raise DxfError("EDIT_LAYER_NOT_FOUND", "Layer not found", 422)
            if c["geom"]["type"] == "INSERT" and c["geom"]["name"].upper() == name.upper():
                raise DxfError("EDIT_BLOCK_INVALID", "A block cannot contain itself", 422)
            create(layout, c)
    for h in edits.get("deleted", []):
        delete(target(h, deleting=True))
    for m in edits.get("modified", []):
        e = target(m["handle"])
        is_dim = e.dxftype() == "DIMENSION"
        kind = m["geom"]["type"]
        if (not kind.startswith("DIM_")) if is_dim else (kind != e.dxftype()):
            raise DxfError("EDIT_INVALID", "Geometry type does not match entity", 422)
        layer_ok(m["layer"])
        attribs = props(m)
        if is_dim:  # rebuilt with its own style and properties (the handle changes)
            keep = (e.dxf.dimstyle, dict(e.override().dimstyle_attribs))
            attribs = {"color": e.dxf.color, "linetype": e.dxf.linetype, **attribs}
            delete(e)
            style(_dim(msp, m["geom"], m["layer"], s, keep), attribs)
            continue
        e.dxf.layer = m["layer"]
        if kind == "INSERT":  # moved as a whole: its attributes follow
            if m["geom"]["name"] != e.dxf.name:
                raise DxfError("EDIT_INVALID", "A block reference keeps its block", 422)
            _place(e, m["geom"], s)
        else:
            _set(e, m["geom"], s)
        style(e, attribs)
    for c in edits.get("created", []):
        layer_ok(c["layer"])
        create(msp, c)
    for op in edits.get("layers", []):
        _layer_op(doc, op)
    # ponytail: keeps source DXF version, add R2018 export option if partners need it
    doc.saveas(dst_path)
    if os.path.getsize(dst_path) > MAX_FILE_BYTES:  # chained edits must not outgrow uploads
        raise DxfError("FILE_TOO_LARGE", "Edited drawing exceeds 50 MB", 413)
