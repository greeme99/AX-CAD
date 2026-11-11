"""Apply mm-based edits to a DXF in place (handles of untouched/modified entities are preserved)."""

import os
from collections import Counter
from typing import Any

import ezdxf
from ezdxf.lldxf.validator import is_valid_block_name, is_valid_layer_name
from ezdxf.math import Vec2, Vec3, fit_points_to_cad_cv
from ezdxf.render import mleader
from ezdxf.tools.pattern import ISO_PATTERN

from core.dxf import dimvars
from core.dxf import underlay as ul
from core.dxf.objects import dict_op, set_xdata
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
    if g.get("overrides"):  # W4: the script's own dimension variables, over the kept ones
        kw["override"] = {**kw["override"], **dimvars.to_file(msp.doc, g["overrides"], s)}
    if g.get("text") is not None:  # W4: the measurement text replaced ("<>" = the measurement)
        kw["text"] = g["text"]
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
    flags = b.block.dxf.get("flags", 0) if b is not None and b.block is not None else 0
    if (
        b is None or name.startswith("*") or b.is_any_layout or flags & 28
    ):  # XREF, overlay, dependent
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


def _set_attribs(e: Any, values: dict[str, str]) -> None:
    """W7d: an INSERT's attribute values by tag (a tag the reference does not carry is refused)."""
    for tag, value in values.items():
        a = next((x for x in e.attribs if x.dxf.tag.upper() == tag.upper()), None)
        if a is None:
            raise DxfError("EDIT_ATTRIB_NOT_FOUND", "Block reference has no such attribute", 422)
        a.dxf.text = value


MAX_NEW_ATTRIBS = 100_000
ATTDEF_FLAGS = {"invisible": 1, "constant": 2, "verify": 4, "preset": 8}


def _add_attdef(layout: Any, a: dict[str, Any], base: Vec2, s: float) -> None:
    flags = sum(bit for k, bit in ATTDEF_FLAGS.items() if a.get(k))
    if len(a["tag"].upper()) > 255:  # upper-casing can lengthen (ß -> SS)
        raise DxfError("EDIT_INVALID", "Attribute tag too long", 422)
    x, y = a["insert"]
    layout.add_attdef(
        a["tag"].upper(),
        (x / s - base.x, y / s - base.y),
        a.get("default", ""),
        height=a["height"] / s,
        rotation=a.get("rotation", 0),
        dxfattribs={"layer": a.get("layer", "0"), "prompt": a.get("prompt", ""), "flags": flags},
    )


def _references(doc: Any, name: str) -> list[Any]:
    """Every live block reference to `name`, in any layout or block."""
    up = name.upper()
    return [e for e in doc.entitydb.query("INSERT") if e.is_alive and str(e.dxf.name).upper() == up]


def _check_acyclic(doc: Any) -> None:
    """W7d review M2: no block may insert itself, directly or through others (a create, rename or
    redefine can close a loop through a reference to a block that did not exist yet)."""
    graph = {
        b.name.upper(): {str(e.dxf.name).upper() for e in b.query("INSERT")}
        for b in doc.blocks
        if not b.is_any_layout
    }
    state: dict[str, int] = {}  # 1 = on the path, 2 = done
    for root, children in graph.items():
        if state.get(root):
            continue
        stack = [(root, iter(children))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                raise DxfError("EDIT_BLOCK_INVALID", "A block cannot contain itself", 422)
            elif nxt in graph and not state.get(nxt):
                state[nxt] = 1
                stack.append((nxt, iter(graph[nxt])))


def _holders(doc: Any, name: str) -> set[str]:
    """Upper-case names of the blocks that show `name`: itself and every block inserting it."""
    inside: dict[str, set[str]] = {}
    for b in doc.blocks:
        if not b.is_any_layout:
            for e in b.query("INSERT"):
                inside.setdefault(str(e.dxf.name).upper(), set()).add(b.name.upper())
    out, stack = set(), [name.upper()]
    while stack:
        n = stack.pop()
        if n not in out:
            out.add(n)
            stack.extend(inside.get(n, ()))
    return out


def _block_used(doc: Any, b: Any) -> bool:
    """References by name (INSERT, dimension arrowheads) or by handle (multileaders)."""
    if _references(doc, b.name):
        return True
    up, handle = b.name.upper(), b.block_record.dxf.handle
    for ds in doc.dimstyles:
        if any(str(ds.dxf.get(k, "")).upper() == up for k in DIM_BLOCKS):
            return True
        if any(ds.dxf.get(f"{k}_handle") == handle for k in DIM_BLOCKS):
            return True
    return any(
        e.dxf.get("block_record_handle") == handle
        for e in doc.entitydb.values()
        if e.is_alive and e.dxftype() in ("MULTILEADER", "MLEADERSTYLE")
    )


DIM_BLOCKS = ("dimblk", "dimblk1", "dimblk2", "dimldrblk")


def _sync_attribs(doc: Any, layout: Any) -> None:
    """ATTSYNC: references keep the values of tags still defined, gain the new ones (defaults, at
    the definition's place) and lose the ones no longer defined."""
    defs = {a.dxf.tag.upper(): a for a in layout.attdefs() if not a.is_const}
    for ref in _references(doc, layout.name):
        have = {a.dxf.tag.upper(): a.dxf.tag for a in ref.attribs}
        for tag in have.keys() - defs.keys():
            ref.delete_attrib(have[tag])
        m = ref.matrix44()
        for tag in defs.keys() - have:
            d = defs[tag]
            ref.add_attrib(
                d.dxf.tag,
                d.dxf.text,
                insert=m.transform(d.dxf.insert),
                dxfattribs={
                    "height": d.dxf.height * abs(ref.dxf.yscale),
                    "rotation": (d.dxf.get("rotation", 0) + ref.dxf.rotation) % 360,
                    "layer": d.dxf.layer,
                    "flags": d.dxf.get("flags", 0),
                },
            )


def _block_op(doc: Any, b: dict[str, Any]) -> None:
    """W7d: rename / delete a block after the entity edits."""
    name = b["name"]
    _insertable(doc, name)  # not a layout, an anonymous / dimension block or an XREF
    if b["op"] == "delete":
        if _block_used(doc, doc.blocks.get(name)):
            raise DxfError("EDIT_BLOCK_IN_USE", "Block is in use", 409)
        try:
            doc.blocks.delete_block(name, safe=True)  # ezdxf's own check: special (arrow) blocks
        except ezdxf.DXFBlockInUseError:
            raise DxfError("EDIT_BLOCK_IN_USE", "Block is in use", 409) from None
        return
    new = b["new_name"]
    if new.startswith("*") or not is_valid_block_name(new):
        raise DxfError("EDIT_BLOCK_INVALID", "Invalid block name", 422)
    if new.upper() != name.upper() and new in doc.blocks:
        raise DxfError("EDIT_BLOCK_EXISTS", "Block already exists", 422)
    refs = _references(doc, name)
    doc.blocks.rename_block(doc.blocks.get(name).name, new)
    for r in refs:
        r.dxf.name = new


def _layout_op(doc: Any, op: dict[str, Any]) -> None:
    """W7d: paper space layouts (the model space is never one of them)."""
    name = op["name"]
    if op["op"] == "create":
        if name in doc.layouts:
            raise DxfError("EDIT_LAYOUT_EXISTS", "Layout already exists", 422)
        doc.layouts.new(name)
        return
    if name not in doc.layouts:
        raise DxfError("EDIT_LAYOUT_NOT_FOUND", "Layout not found", 422)
    if op["op"] == "delete":
        if len([n for n in doc.layouts.names() if n.lower() != "model"]) <= 1:
            raise DxfError("EDIT_LAYOUT_PROTECTED", "The last layout cannot be deleted", 422)
        if any(
            e.dxf.layer in doc.layers and doc.layers.get(e.dxf.layer).is_locked()
            for e in doc.layouts.get(name)
        ):
            raise DxfError("EDIT_LAYER_LOCKED", "The layout holds entities on a locked layer", 422)
        doc.layouts.delete(name)
        return
    new = op["new_name"]
    if new.lower() != name.lower() and new in doc.layouts:
        raise DxfError("EDIT_LAYOUT_EXISTS", "Layout already exists", 422)
    doc.layouts.rename(name, new)


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
        current = name.upper() == str(doc.header.get("$CLAYER", "0")).upper()
        layer.rename(new)  # also updates the entities and blocks that use it
        if current:
            doc.header["$CLAYER"] = new


# W7b: records AutoCAD itself needs; they are never renamed or deleted
PROTECTED = {
    "linetype": {"BYLAYER", "BYBLOCK", "CONTINUOUS"},
    "textstyle": {"STANDARD", "ANNOTATIVE"},
    "dimstyle": {"STANDARD", "ANNOTATIVE"},
    "regapp": {"ACAD"},  # and every ACAD* / ACAD_* application (below)
}
# header variables that name the current record of a table (kept valid on rename / delete)
CURRENT = {"linetype": "$CELTYPE", "textstyle": "$TEXTSTYLE", "dimstyle": "$DIMSTYLE"}
DIM_LINETYPES = ("dimltype", "dimltex1", "dimltex2")


def _protected(kind: str, name: str) -> bool:
    up = name.upper()
    return up in PROTECTED[kind] or (kind == "regapp" and up.startswith("ACAD"))


TABLE_ATTR = {
    "linetype": "linetypes",
    "textstyle": "styles",
    "dimstyle": "dimstyles",
    "regapp": "appids",
}
DIM_LEN = ("dimtxt", "dimasz", "dimexe", "dimexo", "dimgap")


TABLE_RECORDS = ("TABLE", "LAYER", "LTYPE", "STYLE", "DIMSTYLE", "APPID")
TEXT_TYPES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")
DIM_TYPES = ("DIMENSION", "LEADER", "TOLERANCE", "ARC_DIMENSION")


def _uses(doc: Any, kind: str, name: str) -> list[Any]:
    """Entities and table records that point at a linetype / text style / dimension style by name
    (regapps: any object, table records too, with that application's XDATA)."""
    up = name.upper()
    hits: list[Any] = []
    for e in doc.entitydb.values():
        if not e.is_alive:
            continue
        t = e.dxftype()
        if kind == "regapp":
            if t != "APPID" and e.has_xdata(name):
                hits.append(e)
            continue
        if t in TABLE_RECORDS:  # graphical entities only: the table records answer below
            continue
        d = e.dxf
        if kind == "linetype":
            if d.is_supported("linetype") and str(d.get("linetype", "")).upper() == up:
                hits.append(e)
        elif kind == "textstyle":
            if t in TEXT_TYPES and str(d.get("style", "")).upper() == up:
                hits.append(e)
        elif t in DIM_TYPES and str(d.get("dimstyle", "")).upper() == up:
            hits.append(e)
    if kind == "linetype":
        hits += [lay for lay in doc.layers if str(lay.dxf.linetype).upper() == up]
        # dimension styles name linetypes too (dimension and extension lines)
        hits += [
            ds
            for ds in doc.dimstyles
            if any(str(ds.dxf.get(k, "")).upper() == up for k in DIM_LINETYPES)
        ]
    if kind == "textstyle":
        hits += [ds for ds in doc.dimstyles if str(ds.dxf.get("dimtxsty", "")).upper() == up]
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
    if _protected(kind, name):
        raise DxfError("EDIT_TABLE_PROTECTED", "This record cannot be renamed or deleted", 422)
    uses = _uses(doc, kind, name)
    var = CURRENT.get(kind)
    current = var is not None and str(doc.header.get(var, "")).upper() == name.upper()
    if op["op"] == "delete":
        if uses or current:  # the current style / linetype counts as in use
            raise DxfError("EDIT_TABLE_IN_USE", "Table record is in use", 409)
        table.remove(name)
        return
    new = op["new_name"]
    if table.has_entry(new):
        raise DxfError("EDIT_TABLE_EXISTS", "Table record already exists", 422)
    table.duplicate_entry(name, new)  # same settings under the new name, then repoint the users
    up = name.upper()
    for u in uses:
        if u.dxftype() == "DIMSTYLE":  # names a linetype (3 slots) or the text style
            for k in DIM_LINETYPES if kind == "linetype" else ("dimtxsty",):
                if str(u.dxf.get(k, "")).upper() == up:
                    u.dxf.set(k, new)
            continue
        key = "linetype" if kind == "linetype" else "style" if kind == "textstyle" else "dimstyle"
        u.dxf.set(key, new)
    if current and var:
        doc.header[var] = new
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
        e = make(layout, c)
        if c.get("attributes"):  # W7d
            _set_attribs(e, c["attributes"])
        if c.get("xdata"):  # W7c
            set_xdata(doc, e, c["xdata"])

    def make(layout: Any, c: dict[str, Any]) -> Any:
        g, attribs = c["geom"], props(c)
        if g["type"].startswith("DIM_"):
            e = _dim(layout, g, c["layer"], s)
            style(e, attribs)
            return e
        if g["type"] == "LEADER":  # arrowhead sized like the editor's dimensions
            style_name = "Standard" if "Standard" in doc.dimstyles else "EZDXF"
            e = layout.add_leader(
                [(x / s, y / s) for x, y in g["vertices"]],
                dimstyle=style_name,
                override={"dimasz": 2.5 / s},
                dxfattribs={"layer": c["layer"]},
            )
            style(e, attribs)
            return e
        if g["type"] == "MULTILEADER":
            if doc.dxfversion < "AC1015":
                raise DxfError("EDIT_INVALID", "Multileaders need DXF R2000 or later", 422)
            b = layout.add_multileader_mtext("Standard", dxfattribs={"layer": c["layer"]})
            b.set_content(g["value"])
            b.add_leader_line(mleader.ConnectionSide.left, [Vec2(0, 0)])
            b.build(insert=Vec2(1, 1))  # placeholder layout, replaced by the geom
            _set(b.multileader, g, s)
            style(b.multileader, attribs)
            return b.multileader
        if g["type"] == "INSERT":
            _insertable(doc, g["name"])
            e = layout.add_blockref(g["name"], (0, 0), dxfattribs={"layer": c["layer"]})
            n_defs = sum(1 for _ in doc.blocks.get(g["name"]).attdefs())
            if n_defs:
                spend(n_defs)
                e.add_auto_attribs({})  # attribute defaults, then moved with the block
            _place(e, g, s)
            style(e, attribs)
            return e
        if g["type"] == "MTEXT" and doc.dxfversion < "AC1012":
            raise DxfError("EDIT_INVALID", "MTEXT needs DXF R13 or later", 422)
        e = layout.new_entity(g["type"], {"layer": c["layer"]})
        _set(e, g, s)
        style(e, attribs)
        return e

    budget = [MAX_NEW_ATTRIBS]

    def spend(n: int) -> None:
        """Review M3: attributes one save may create (references x definitions)."""
        budget[0] -= n
        if budget[0] < 0:
            raise DxfError("EDIT_INVALID", "Too many block attributes in one save", 422)

    def member(handle: str) -> Any:
        """A group member: any model space entity on an unlocked layer (it gains a reactor)."""
        e = doc.entitydb.get(handle)
        if e is None or not e.is_alive or e.dxf.get("owner") not in spaces:
            raise DxfError("EDIT_HANDLE_NOT_FOUND", "Entity not found or not editable", 422)
        layer_ok(e.dxf.layer)
        return e

    def group_op(op: dict[str, Any]) -> None:
        name, groups = op["name"], doc.groups
        group = groups.get(name)
        if op["op"] == "create":
            if group is not None:
                raise DxfError("EDIT_GROUP_EXISTS", "Group already exists", 422)
            group = groups.new(name, op.get("description") or "", op.get("selectable", True))
        elif group is None:
            raise DxfError("EDIT_GROUP_NOT_FOUND", "Group not found", 422)
        if op["op"] == "delete":
            groups.delete(group)
            return
        if op.get("handles") is not None:  # members: editable model space entities
            members = [member(h) for h in dict.fromkeys(x.upper() for x in op["handles"])]
            group.set_data(members)
        if op.get("description") is not None:
            group.dxf.description = op["description"]
        if op.get("selectable") is not None:
            group.dxf.selectable = 1 if op["selectable"] else 0
        new = op.get("new_name")
        if new and new != name:
            if groups.get(new) is not None:
                raise DxfError("EDIT_GROUP_EXISTS", "Group already exists", 422)
            group_data = list(group)
            desc, sel = group.dxf.description, group.dxf.selectable
            groups.delete(group)
            groups.new(new, desc, bool(sel)).set_data(group_data)

    tables = edits.get("tables", [])
    for op in tables:  # W7b: new / changed records first, the entities below may use them
        if op["op"] in ("create", "modify"):
            _table_op(doc, op, s)
    layout_ops = edits.get("layouts", [])
    for op in layout_ops:  # W7d: new sheets first, the entities below may go on them
        if op["op"] == "create":
            _layout_op(doc, op)
    # new / redefined blocks first: the INSERTs created below may use them
    blocks = edits.get("blocks", [])
    for b in blocks:
        if b.get("op", "create") not in ("create", "redefine"):
            continue
        name = b["name"]
        if b.get("op", "create") == "create":
            if name.startswith("*") or not is_valid_block_name(name):
                raise DxfError("EDIT_BLOCK_INVALID", "Invalid block name", 422)
            if name in doc.blocks:
                raise DxfError("EDIT_BLOCK_EXISTS", "Block already exists", 422)
            layout = doc.blocks.new(name, base_point=(b["base"][0] / s, b["base"][1] / s))
        else:  # W7d: same name, new contents; its references follow
            layout = _insertable(doc, name)
            shown_by = _holders(doc, name)  # review M1: what is shown on a locked layer stays
            for e in doc.entitydb.query("INSERT"):
                if e.is_alive and str(e.dxf.name).upper() in shown_by:
                    where = doc.blocks.get_block_layout_by_handle(e.dxf.owner)
                    if where is None or where.is_any_layout:
                        layer_ok(e.dxf.layer)
            layout.delete_all_entities()
            layout.block.dxf.base_point = (b["base"][0] / s, b["base"][1] / s, 0)
        if b.get("description") and layout.block is not None:
            layout.block.dxf.description = b["description"]
        for c in b["entities"]:
            if not has_layer(c["layer"]):
                raise DxfError("EDIT_LAYER_NOT_FOUND", "Layer not found", 422)
            if c["geom"]["type"] == "INSERT" and c["geom"]["name"].upper() == name.upper():
                raise DxfError("EDIT_BLOCK_INVALID", "A block cannot contain itself", 422)
            create(layout, c)
        base = Vec2(layout.block.dxf.base_point) if layout.block is not None else Vec2()
        for a in b.get("attdefs", []):
            if not has_layer(a.get("layer", "0")):
                raise DxfError("EDIT_LAYER_NOT_FOUND", "Layer not found", 422)
            _add_attdef(layout, a, base, s)
        if b.get("op") == "redefine":
            spend(len(_references(doc, layout.name)) * len(b.get("attdefs", [])))
            _sync_attribs(doc, layout)
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
            old_text = e.dxf.get("text", "<>") or "<>"
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
            # W7c: the rebuilt dimension keeps its XDATA — but not AutoCAD's own ("ACAD" holds the
            # dimension style overrides, which `keep` carries and the new ones may change)
            xdata = (
                {app: list(e.get_xdata(app)) for app in e.xdata.data if app.upper() != "ACAD"}
                if e.xdata
                else {}
            )
            delete(e)
            g = m["geom"]
            if g.get("text") is None:  # W4: no text given: its own text override stays
                g = {**g, "text": old_text}
            new = _dim(owner, g, m["layer"], s, keep)
            style(new, attribs)
            for app, tags in xdata.items():  # W7c: the rebuilt dimension keeps its XDATA
                new.set_xdata(app, tags)
            if m.get("xdata"):
                set_xdata(doc, new, m["xdata"])
            continue
        e.dxf.layer = m["layer"]
        if kind == "INSERT":  # moved as a whole: its attributes follow
            if m["geom"]["name"] != e.dxf.name:
                raise DxfError("EDIT_INVALID", "A block reference keeps its block", 422)
            _place(e, m["geom"], s)
            if m.get("attributes"):  # W7d
                _set_attribs(e, m["attributes"])
        else:
            _set(e, m["geom"], s)
        style(e, attribs)
        if m.get("xdata"):
            set_xdata(doc, e, m["xdata"])
    for c in edits.get("created", []):
        layer_ok(c["layer"])
        create(space(c.get("space")), c)
    for u in edits.get("underlays", []):  # 2D underlay U2
        underlay_op(u)
    for op in edits.get("dictionaries", []):  # W7c: after the entities, so they see them
        holder: Any = None
        if op.get("handle"):
            holder = doc.entitydb.get(op["handle"].upper())
            if holder is None or holder.dxf.owner not in spaces:
                raise DxfError("EDIT_HANDLE_NOT_FOUND", "Entity not found or not editable", 422)
            layer_ok(holder.dxf.layer)
        dict_op(doc, op, holder)
    for op in edits.get("groups", []):  # W7d: named groups of model space entities
        group_op(op)
    for op in edits.get("layers", []):
        _layer_op(doc, op)
    for b in blocks:  # W7d: renames and deletes see the drawing as edited
        if b.get("op") in ("rename", "delete"):
            _block_op(doc, b)
    for op in layout_ops:
        if op["op"] != "create":
            _layout_op(doc, op)
    for op in tables:  # renames and deletes see the drawing as edited
        if op["op"] in ("rename", "delete"):
            _table_op(doc, op, s)
    if blocks:
        _check_acyclic(doc)
    # ponytail: keeps source DXF version, add R2018 export option if partners need it
    doc.saveas(dst_path)
    if os.path.getsize(dst_path) > MAX_FILE_BYTES:  # chained edits must not outgrow uploads
        raise DxfError("FILE_TOO_LARGE", "Edited drawing exceeds 50 MB", 413)
