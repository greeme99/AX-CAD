"""Apply mm-based edits to a DXF in place (handles of untouched/modified entities are preserved)."""

import os
from collections import Counter
from typing import Any

import ezdxf
from ezdxf.lldxf.validator import is_valid_block_name, is_valid_layer_name
from ezdxf.math import Vec2, Vec3, fit_points_to_cad_cv
from ezdxf.render import mleader
from ezdxf.tools.pattern import ISO_PATTERN

from core.dxf import underlay as ul
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
    elif g["type"] == "MULTILEADER":  # no landing of its own: the text sits at the last vertex
        pts = [Vec3(x / s, y / s) for x, y in g["vertices"]]
        h, end = g["height"] / s, pts[-1]
        right = end.x >= pts[-2].x  # text on the side the last leg points to
        c = e.context
        leader = c.leaders[0]
        leader.lines[0].vertices, leader.last_leader_point = pts[:-1], end
        leader.dogleg_length = 0
        e.dxf.has_dogleg, e.dxf.dogleg_length = 0, 0
        e.dxf.property_override_flags |= mleader.OVERRIDE_FLAG["has_dogleg"]
        c.base_point, c.char_height, c.arrow_head_size = end, h, 2.5 / s
        e.dxf.arrow_head_size = 2.5 / s
        c.mtext.default_content = g["value"]
        c.mtext.insert = end + Vec3(h / 2 if right else -h / 2, h / 2)
        c.mtext.alignment = e.dxf.text_attachment_point = 1 if right else 3
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
    elif g["type"] == "MTEXT":  # W7a: a newline is a paragraph break (\\P) in MTEXT
        d.insert, d.char_height = (g["insert"][0] / s, g["insert"][1] / s), g["height"] / s
        d.width, d.attachment_point = g["width"] / s, g["attachment"]
        d.discard("text_direction")  # the rotation below is the one that counts
        d.rotation = g["rotation"]
        e.text = g["value"].replace("\r\n", "\n").replace("\n", "\\P")
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
    # W7b (plan §22.8)
    if op.get("lineweight") is not None:
        layer.dxf.lineweight = op["lineweight"]
    if op.get("true_color") is not None:
        layer.dxf.true_color = op["true_color"]
    if op.get("plot") is not None:
        layer.dxf.plot = 1 if op["plot"] else 0
    if op.get("off") is not None:
        layer.off() if op["off"] else layer.on()
    if op.get("frozen") is not None:
        if op["frozen"] and name.upper() == doc.header.get("$CLAYER", "0").upper():
            raise DxfError("EDIT_LAYER_INVALID", "The current layer cannot be frozen", 422)
        layer.freeze() if op["frozen"] else layer.thaw()
    if op.get("description") is not None:
        layer.description = op["description"]
    new = op.get("rename")
    if new is not None and new != name:
        if name.upper() in PROTECTED_LAYERS or not is_valid_layer_name(new):
            raise DxfError("EDIT_LAYER_INVALID", "Layer cannot be renamed to this name", 422)
        if doc.layers.has_entry(new):
            raise DxfError("EDIT_LAYER_EXISTS", "Layer already exists", 422)
        layer.rename(new)  # also updates the entities and blocks that use it


# W7b: records AutoCAD itself needs; they are never renamed or deleted
PROTECTED = {
    "linetype": {"BYLAYER", "BYBLOCK", "CONTINUOUS"},
    "textstyle": {"STANDARD"},
    "dimstyle": {"STANDARD"},
    "regapp": {"ACAD"},
}
TABLE_ATTR = {
    "linetype": "linetypes",
    "textstyle": "styles",
    "dimstyle": "dimstyles",
    "regapp": "appids",
}
DIM_LEN = ("dimtxt", "dimasz", "dimexe", "dimexo", "dimgap")


def _uses(doc: Any, kind: str, name: str) -> list[Any]:
    """Entities and table records that point at a linetype / text style / dimension style by name
    (regapps: entities with that application's XDATA)."""
    up = name.upper()
    hits: list[Any] = []
    for e in doc.entitydb.values():
        if not e.is_alive or e.dxftype() in (
            "TABLE",
            "LAYER",
            "LTYPE",
            "STYLE",
            "DIMSTYLE",
            "APPID",
        ):
            continue  # graphical entities only: tables and their records answer below
        d = e.dxf
        if kind == "linetype" and not d.is_supported("linetype"):
            continue
        if (
            kind == "linetype"
            and str(d.get("linetype", "")).upper() == up
            or (
                kind == "textstyle"
                and e.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")
                and str(d.get("style", "")).upper() == up
            )
            or (
                kind == "dimstyle"
                and e.dxftype() in ("DIMENSION", "LEADER", "TOLERANCE", "ARC_DIMENSION")
                and str(d.get("dimstyle", "")).upper() == up
            )
            or kind == "regapp"
            and e.has_xdata(name)
        ):
            hits.append(e)
    if kind == "linetype":
        hits += [lay for lay in doc.layers if str(lay.dxf.linetype).upper() == up]
    if kind == "textstyle":
        hits += [s for s in doc.dimstyles if str(s.dxf.get("dimtxsty", "")).upper() == up]
    return hits


def _table_op(doc: Any, op: dict[str, Any], s: float) -> None:
    kind, name = op["kind"], op["name"]
    table = getattr(doc, TABLE_ATTR[kind])
    exists = table.has_entry(name)
    if op["op"] == "create":
        if exists:
            raise DxfError("EDIT_TABLE_EXISTS", "Table record already exists", 422)
        if kind == "linetype":
            dashes = [v / s for v in op.get("pattern") or []]
            pattern: Any = [sum(abs(v) for v in dashes), *dashes] if dashes else 0.0
            table.add(name, pattern=pattern, description=op.get("description") or "")
        elif kind == "textstyle":
            table.add(name, font=op.get("font") or "txt")
        elif kind == "dimstyle":
            table.duplicate_entry(
                "Standard" if table.has_entry("Standard") else next(iter(table)).dxf.name, name
            )
        else:
            table.add(name)
    elif not exists:
        raise DxfError("EDIT_TABLE_NOT_FOUND", "Table record not found", 422)
    rec = table.get(name)
    if op["op"] in ("create", "modify"):
        if kind == "linetype":
            if op.get("description") is not None:
                rec.dxf.description = op["description"]
            if op["op"] == "modify" and op.get("pattern") is not None:
                dashes = [v / s for v in op["pattern"]]
                rec.setup_pattern([sum(abs(v) for v in dashes), *dashes] if dashes else 0.0)
        elif kind == "textstyle":
            for k in ("font", "width", "oblique"):
                if op.get(k) is not None:
                    rec.dxf.set(k, op[k])
            if op.get("height") is not None:
                rec.dxf.height = op["height"] / s
        elif kind == "dimstyle" and op.get("dim"):
            for k, v in op["dim"].items():
                if v is None:
                    continue
                if k == "dimtxsty" and not doc.styles.has_entry(v):
                    raise DxfError("EDIT_TABLE_NOT_FOUND", "Text style not found", 422)
                rec.dxf.set(k, v / s if k in DIM_LEN else v)
        return
    if name.upper() in PROTECTED[kind]:
        raise DxfError("EDIT_TABLE_PROTECTED", "This record cannot be renamed or deleted", 422)
    uses = _uses(doc, kind, name)
    if op["op"] == "delete":
        if uses:
            raise DxfError("EDIT_TABLE_IN_USE", "Table record is in use", 409)
        table.remove(name)
        return
    new = op["new_name"]
    if table.has_entry(new):
        raise DxfError("EDIT_TABLE_EXISTS", "Table record already exists", 422)
    table.duplicate_entry(name, new)  # same settings under the new name, then repoint the users
    for u in uses:
        key = (
            "linetype"
            if kind == "linetype"
            else "dimtxsty"
            if u.dxftype() == "DIMSTYLE"
            else "style"
            if kind == "textstyle"
            else "dimstyle"
        )
        u.dxf.set(key, new)
    table.remove(name)


def apply_edits(src_path: str, dst_path: str, edits: dict[str, Any]) -> None:
    doc = ezdxf.readfile(src_path)  # strict, like parsing
    s = unit_scale(doc)
    msp = doc.modelspace()
    # layers this save creates: its new entities may already use them (the layer ops still run
    # after the entity edits, in order; a create that fails fails the whole save)
    pending = {op["name"].upper() for op in edits.get("layers", []) if op.get("create")}

    def has_layer(name: str) -> bool:
        return doc.layers.has_entry(name) or name.upper() in pending

    def layer_ok(name: str) -> None:
        if not has_layer(name):
            raise DxfError("EDIT_LAYER_NOT_FOUND", "Layer not found", 422)
        if not doc.layers.has_entry(name):
            return  # created later in this save, so not locked yet
        if doc.layers.get(name).is_locked():
            raise DxfError("EDIT_LAYER_LOCKED", "Layer is locked", 422)

    # W7a: new entities may go on a paper space sheet; modify/delete stay in model space until the
    # reader shows editable paper space geometry (a handle alone must not reach a sheet entity)
    spaces = {msp.layout_key}

    def space(name: str | None) -> Any:
        if not name or name.lower() == "model":
            return msp
        if name not in doc.layouts:
            raise DxfError("EDIT_LAYOUT_NOT_FOUND", "Layout not found", 422)
        return doc.layouts.get(name)

    def target(handle: str, deleting: bool = False) -> Any:
        e = doc.entitydb.get(handle.upper())
        if e is None or e.dxf.owner not in spaces:
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
        """Entity properties the edit sets (none given: left as is). W7a adds lineweight, true
        colour, transparency (percent), linetype scale and visibility (plan §22.8)."""
        linetype_ok(item.get("linetype"))
        keys = (
            "color",
            "linetype",
            "lineweight",
            "true_color",
            "transparency",
            "ltscale",
            "visible",
        )
        out = {k: item[k] for k in keys if item.get(k) is not None}
        if "lineweight" in out and doc.dxfversion < "AC1015":
            raise DxfError("EDIT_INVALID", "Lineweights need DXF R2000 or later", 422)
        if ("true_color" in out or "transparency" in out) and doc.dxfversion < "AC1018":
            raise DxfError("EDIT_INVALID", "True colour and transparency need DXF R2004", 422)
        return out

    def style(e: Any, attribs: dict[str, Any]) -> None:
        if "color" in attribs:
            e.dxf.discard("true_color")  # a true colour would hide the ACI choice
        for k, v in attribs.items():  # color comes first, so a true colour given with it wins
            if k == "transparency":
                e.transparency = v / 100
            elif k == "visible":
                e.dxf.invisible = 0 if v else 1
            else:
                e.dxf.set(k, v)

    dim_blocks = Counter(d.dxf.get("geometry") for d in doc.entitydb.query("DIMENSION"))

    def delete(e: Any) -> None:
        block = e.dxf.get("geometry") if e.dxftype() == "DIMENSION" else None
        doc.layouts.get_layout_for_entity(e).delete_entity(e)
        if not block:
            return
        # its rendered *D block would otherwise be orphaned; a name taken from the file may point
        # anywhere, so only an anonymous dimension block no other dimension uses goes (BUG-48)
        dim_blocks[block] -= 1
        if block.upper().startswith("*D") and block in doc.blocks and dim_blocks[block] <= 0:
            doc.blocks.delete_block(block, safe=False)

    def underlay_op(op: dict[str, Any]) -> None:
        if op["op"] == "create":
            layer_ok(op["layer"])
            g = op["geom"]
            definition = ul.ensure_definition(doc, g["format"], g["sha256"], g["name"], g["page"])
            new = msp.add_underlay(definition, dxfattribs={"layer": op["layer"]})
            ul.apply(new, doc, g, s)
            return
        old = doc.entitydb.get(op["handle"].upper())
        if old is None or old.dxf.owner != msp.layout_key or old.dxftype() not in ul.UNDERLAY_TYPES:
            raise DxfError("EDIT_HANDLE_NOT_FOUND", "Underlay not found", 422)
        layer_ok(old.dxf.layer)
        if op["op"] == "delete":
            msp.delete_entity(old)  # type: ignore[arg-type]  # a PDFUNDERLAY is graphic
            return
        if op.get("layer"):
            layer_ok(op["layer"])
            old.dxf.layer = op["layer"]
        try:
            ul.apply(old, doc, op["geom"], s)
        except ValueError:
            raise DxfError("EDIT_INVALID", "An underlay keeps its format", 422) from None

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
        if g["type"] == "MULTILEADER":
            if doc.dxfversion < "AC1015":
                raise DxfError("EDIT_INVALID", "Multileaders need DXF R2000 or later", 422)
            b = layout.add_multileader_mtext("Standard", dxfattribs={"layer": c["layer"]})
            b.set_content(g["value"])
            b.add_leader_line(mleader.ConnectionSide.left, [Vec2(0, 0)])
            b.build(insert=Vec2(1, 1))  # placeholder layout, replaced by the geom
            _set(b.multileader, g, s)
            style(b.multileader, attribs)
            return
        if g["type"] == "INSERT":
            _insertable(doc, g["name"])
            e = layout.add_blockref(g["name"], (0, 0), dxfattribs={"layer": c["layer"]})
            if any(True for _ in doc.blocks.get(g["name"]).attdefs()):
                e.add_auto_attribs({})  # attribute defaults, then moved with the block
            _place(e, g, s)
            style(e, attribs)
            return
        if g["type"] == "MTEXT" and doc.dxfversion < "AC1012":
            raise DxfError("EDIT_INVALID", "MTEXT needs DXF R13 or later", 422)
        e = layout.new_entity(g["type"], {"layer": c["layer"]})
        _set(e, g, s)
        style(e, attribs)

    tables = edits.get("tables", [])
    for op in tables:  # W7b: new / changed records first, the entities below may use them
        if op["op"] in ("create", "modify"):
            _table_op(doc, op, s)
    # new blocks first: the INSERTs created below may use them
    for b in edits.get("blocks", []):
        name = b["name"]
        if name.startswith("*") or not is_valid_block_name(name):
            raise DxfError("EDIT_BLOCK_INVALID", "Invalid block name", 422)
        if name in doc.blocks:
            raise DxfError("EDIT_BLOCK_EXISTS", "Block already exists", 422)
        layout = doc.blocks.new(name, base_point=(b["base"][0] / s, b["base"][1] / s))
        if b.get("description") and layout.block is not None:
            layout.block.dxf.description = b["description"]
        for c in b["entities"]:
            if not has_layer(c["layer"]):
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
            old = {"color": e.dxf.color, "linetype": e.dxf.linetype}
            for k in ("lineweight", "true_color", "ltscale"):  # W7a: kept through the rebuild
                if e.dxf.hasattr(k):
                    old[k] = e.dxf.get(k)
            if e.dxf.hasattr("transparency") and e.transparency:
                old["transparency"] = round(e.transparency * 100)
            if e.dxf.get("invisible", 0):
                old["visible"] = False
            attribs = {**old, **attribs}
            owner = doc.layouts.get_layout_for_entity(e)
            delete(e)
            style(_dim(owner, m["geom"], m["layer"], s, keep), attribs)
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
        create(space(c.get("space")), c)
    for u in edits.get("underlays", []):  # 2D underlay U2
        underlay_op(u)
    for op in edits.get("layers", []):
        _layer_op(doc, op)
    for op in tables:  # renames and deletes see the drawing as edited
        if op["op"] in ("rename", "delete"):
            _table_op(doc, op, s)
    # ponytail: keeps source DXF version, add R2018 export option if partners need it
    doc.saveas(dst_path)
    if os.path.getsize(dst_path) > MAX_FILE_BYTES:  # chained edits must not outgrow uploads
        raise DxfError("FILE_TOO_LARGE", "Edited drawing exceeds 50 MB", 413)
