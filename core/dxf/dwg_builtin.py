"""Built-in DWG translator: no converter to install (plan §25). Run as its own process by
core.dxf.dwg (`python -I dwg_builtin.py read|write ...`) under the converter limits, because it
parses an untrusted file with native code. Imports nothing from this project.

read:  DWG R13..2018 -> DXF R2018. Model space, block definitions and the first paper space are
       rebuilt by ownership; what cannot be carried over is counted by type in the report.
write: DXF -> DWG 2000 (AC1015, opened by every later AutoCAD). Dimensions, leaders and other
       composite objects are saved as their drawn geometry. ezdwg's 2018 writer is not offered:
       LibreDWG, used as an independent check, could not read even a one-line file from it.

ezdwg (MIT) reads and writes the file. Its own `to_dxf` is not used: it copies block contents
into model space or drops most of a 2010+ drawing, so only its per-entity writer is.
# ponytail: relies on private ezdwg.convert helpers, so ezdwg is pinned to one version;
# when upgrading, re-run tests/test_dwg_builtin.py and the sample comparison in plan §25."""

import json
import math
import re
import sys
from collections import Counter
from typing import Any

DWG_VERSIONS = {"2000": "AC1015"}
MAX_EXPLODE_DEPTH = 4
FLATTEN_MM = 0.01
BUILTIN_LTYPES = {"BYLAYER", "BYBLOCK", "CONTINUOUS"}
BAD_NAME = re.compile(r'[<>/\\":;?*|=,`\x00-\x1f]')


def _safe(name: str, block: bool = False) -> str:
    """A table name the DWG writer accepts (one bad name would fail the whole save)."""
    star = "*" if block and name.startswith("*") else ""
    return star + (BAD_NAME.sub("_", name[len(star) :])[:200] or "_")


def read(src: str, out_path: str, report_path: str) -> None:
    import ezdwg
    import ezdxf
    from ezdwg import convert as conv
    from ezdwg import raw

    doc = ezdwg.read(src)
    path = doc.decode_path or doc.path
    structural = conv._BLOCK_EXCLUDED_ENTITY_TYPES | {"ATTRIB"}
    # the inventory the loss report is counted against: this list also names objects ezdwg only
    # recognises (tables, lights, other vendors' objects)
    source = {
        e.handle: e.dxftype
        for e in conv._resolve_dwg_export_entities(doc.entities(), None)
        if e.dxftype not in structural
    }
    entities = conv._resolve_export_entities(
        doc.entities(),
        None,
        include_unsupported=True,
        include_styles=True,
        modelspace_only=False,
        decode_cache=conv._ConvertDecodeCache(),
    )
    attributes = conv._insert_attributes_by_owner(doc.entities(), include_styles=True)
    if attributes:
        entities = conv._attach_insert_attributes(entities, attributes)

    out = ezdxf.new("R2018")
    out.header["$INSUNITS"] = int(doc.header_variables().get("insunits") or 0)
    layer_names = conv._prepare_dxf_layers(
        out, conv._layer_styles_by_handle(path), conv._layer_names_by_handle(path)
    )
    for name, lt in doc.linetypes().items():
        dashes = [float(d) for d in lt.get("dashes") or []]
        if dashes and isinstance(name, str) and name and name not in out.linetypes:
            total = float(lt.get("pattern_length") or sum(abs(d) for d in dashes))
            out.linetypes.add(name, pattern=[total, *dashes], description=lt.get("description", ""))
    for layer in doc.layers:
        if not isinstance(layer.name, str) or layer.name not in out.layers:
            continue
        target = out.layers.get(layer.name)
        props = layer.to_dict()
        if isinstance(props.get("linetype"), str) and props["linetype"] in out.linetypes:
            target.dxf.linetype = props["linetype"]
        if props.get("off"):
            target.off()
        if props.get("frozen"):
            target.freeze()
        if props.get("locked"):
            target.lock()

    # block records by handle: every definition exists before anything refers to it
    names = dict(raw.decode_block_header_names(path))
    bases = {row["handle"]: row.get("base_point") for row in raw.decode_write_blocks(path)}
    blocks: dict[int, Any] = {}
    for handle, name in names.items():
        if not isinstance(name, str) or not name:
            continue
        if name.upper().startswith(("*MODEL_SPACE", "*PAPER_SPACE")):
            continue
        blocks[handle] = out.blocks.get(name) if name in out.blocks else out.blocks.new(name)
        if bases.get(handle):
            blocks[handle].block.dxf.base_point = bases[handle]

    def space(e: Any) -> Any:
        placement = doc.entity_placement(e.handle)
        mode, owner = placement if placement else (None, None)
        owner = e.dxf.get("owner_handle") or owner
        name = (names.get(owner) or "").upper() if owner else ""
        if mode == 2 or name == "*MODEL_SPACE":
            return out.modelspace()
        if mode == 1 or name == "*PAPER_SPACE":
            return out.layout("Layout1")
        return (
            blocks.get(owner) if owner else None
        )  # None: another paper space, or an owner that is not a block

    context = conv._DimensionWriteContext()
    block_ids = {id(b) for b in blocks.values()}
    written: set[int] = set()
    # block contents first: a dimension or insert in model space may look its block up
    for in_block in (True, False):
        for e in entities:
            if e.dxftype in structural:
                continue
            layout = space(e)
            if layout is None or (id(layout) in block_ids) != in_block:
                continue
            before = len(layout)
            if conv._write_entity_to_modelspace(
                layout,
                e,
                explode_dimensions=False,
                layer_name_by_handle=layer_names,
                dim_block_policy="smart",
                dimension_context=context,
            ):
                written.add(e.handle)
                _line_style(out, layout.entity_space.entities[before:], e.dxf)

    # a dimension is drawn again from its definition: its old picture (*D block) is left unused
    used = {
        str(e.dxf.get("name" if e.dxftype() == "INSERT" else "geometry") or "").upper()
        for layout in (*out.layouts, *out.blocks)
        for e in layout.query("INSERT DIMENSION")
    }
    for name in {b.name for b in blocks.values()}:  # two records may share one definition
        if name.upper().startswith("*D") and name.upper() not in used:
            out.blocks.delete_block(name, safe=False)

    # LF on every platform: a CRLF drawing read back from memory comes out empty in ezdxf
    with open(out_path, "w", encoding="utf-8", errors="dxfreplace", newline="\n") as f:
        out.write(f)
    skipped = Counter(t for h, t in source.items() if h not in written)
    _report(report_path, skipped)


def _line_style(out: Any, new: list[Any], dxf: dict[str, Any]) -> None:
    """ezdwg's writer keeps layer and colour only: linetype, its scale and lineweight follow."""
    linetype = dxf.get("linetype")
    scale = dxf.get("linetype_scale")
    weight = dxf.get("lineweight")
    for e in new:
        named = isinstance(linetype, str) and linetype.upper() != "BYLAYER"
        if named and linetype in out.linetypes:
            e.dxf.linetype = linetype
        if isinstance(scale, (int, float)) and scale > 0 and scale != 1.0:
            e.dxf.ltscale = float(scale)
        if isinstance(weight, int) and weight >= 0:
            e.dxf.lineweight = weight


def _report(path: str, skipped: Counter[str], exploded: Counter[str] | None = None) -> None:
    data = {
        "skipped": dict(sorted(skipped.items())),
        "exploded": dict(sorted((exploded or {}).items())),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def write(src: str, out_path: str, report_path: str, version: str) -> None:
    import ezdwg
    import ezdxf

    doc = ezdxf.readfile(src)
    drawing = ezdwg.new(DWG_VERSIONS[version])
    drawing.header["insunits"] = int(doc.units)
    drawing.header["ltscale"] = float(doc.header.get("$LTSCALE", 1.0))
    for lt in doc.linetypes:
        dashes = [float(t.value) for t in lt.pattern_tags.tags if t.code == 49]
        if lt.dxf.name.upper() not in BUILTIN_LTYPES and dashes:
            drawing.linetypes[_safe(lt.dxf.name)] = {
                "description": lt.dxf.get("description", ""),
                "dashes": dashes,
            }
    known = {n.upper() for n in drawing.linetypes} | BUILTIN_LTYPES
    for layer in doc.layers:
        linetype = _safe(layer.dxf.get("linetype", "Continuous"))
        drawing.layers[_safe(layer.dxf.name)] = {
            "color_index": abs(layer.dxf.get("color", 7)) or 7,
            "linetype": linetype if linetype.upper() in known else "Continuous",
            "off": layer.is_off(),
            "frozen": layer.is_frozen(),
            "locked": layer.is_locked(),
        }
    for style in doc.styles:
        if style.dxf.name and style.dxf.get("font"):
            drawing.text_styles[_safe(style.dxf.name)] = {
                "font": style.dxf.font,
                "bigfont": style.dxf.get("bigfont", ""),
                "height": float(style.dxf.get("height", 0.0)),
                "width": float(style.dxf.get("width", 1.0)),
            }

    skipped: Counter[str] = Counter()
    exploded: Counter[str] = Counter()
    put = _Writer(drawing, known, skipped, exploded)
    named = [b for b in doc.blocks if not b.is_any_layout and not b.name.upper().startswith("*D")]
    defined = []
    for block in named:  # all definitions first: an insert may refer to a later one
        if _safe(block.name, True).upper() in put.blocks:
            continue  # two names that became one: the first definition stands
        base = (
            block.block.dxf.get("base_point", (0, 0, 0)) if block.block is not None else (0, 0, 0)
        )
        drawing.blocks.new(_safe(block.name, True), base_point=tuple(base))
        put.blocks.add(_safe(block.name, True).upper())
        defined.append(block)
    for block in defined:
        put.all(drawing.blocks[_safe(block.name, True)], block)
    put.all(drawing.modelspace(), doc.modelspace())
    paper = next((lay for lay in doc.layouts if lay.name != "Model"), None)
    if paper is not None:
        put.all(drawing.paperspace(), paper)
    result = drawing.save(out_path, strict=False)
    skipped.update(result.skipped_by_type)
    _report(report_path, skipped, exploded)


class _Writer:
    """ezdxf entities into an ezdwg layout; what has no DWG form here is exploded or counted."""

    def __init__(
        self, drawing: Any, linetypes: set[str], skipped: Counter[str], exploded: Counter[str]
    ) -> None:
        self.drawing = drawing
        self.linetypes = linetypes
        self.skipped = skipped
        self.exploded = exploded
        self.blocks: set[str] = set()  # defined block names, upper case

    def all(self, layout: Any, entities: Any) -> None:
        for e in entities:
            self.one(layout, e, 0)

    def one(self, layout: Any, e: Any, depth: int) -> None:
        kind = e.dxftype()
        handler = getattr(self, "_" + kind.lower().replace("3d", "three_d"), None)
        try:
            if handler is not None:
                handler(layout, e, self._common(e))
                return
        except Exception:  # noqa: BLE001 - one bad entity must not lose the drawing
            self.skipped[kind] += 1
            return
        # composite objects (dimension, leader, ...): their drawn geometry is saved instead
        if depth >= MAX_EXPLODE_DEPTH:
            self.skipped[kind] += 1
            return
        try:
            parts = list(e.virtual_entities()) if hasattr(e, "virtual_entities") else []
        except Exception:  # noqa: BLE001
            parts = []
        if not parts:
            self.skipped[kind] += 1
            return
        self.exploded[kind] += 1
        for part in parts:
            self.one(layout, part, depth + 1)

    def _common(self, e: Any) -> dict[str, Any]:
        dxf = e.dxf
        layer = _safe(dxf.get("layer", "0"))
        if layer not in self.drawing.layers:
            self.drawing.layers[layer] = {"color_index": 7}
        out: dict[str, Any] = {"layer": layer}
        if dxf.hasattr("color") and dxf.color != 256:
            out["color_index"] = dxf.color
        linetype = _safe(dxf.get("linetype", "BYLAYER"))
        if linetype.upper() != "BYLAYER" and linetype.upper() in self.linetypes:
            out["linetype"] = linetype
        if dxf.hasattr("ltscale"):
            out["linetype_scale"] = float(dxf.ltscale)
        if dxf.hasattr("lineweight") and dxf.lineweight >= 0:
            out["lineweight"] = dxf.lineweight
        if dxf.get("invisible", 0):
            out["invisible"] = True
        return out

    @staticmethod
    def _ocs(e: Any, a: dict[str, Any]) -> dict[str, Any]:
        if e.dxf.hasattr("extrusion"):
            a["extrusion"] = tuple(e.dxf.extrusion)
        if e.dxf.get("thickness", 0):
            a["thickness"] = float(e.dxf.thickness)
        return a

    def _line(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_line(tuple(e.dxf.start), tuple(e.dxf.end), **a)

    def _ray(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_ray(tuple(e.dxf.start), tuple(e.dxf.unit_vector), **a)

    def _xline(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_xline(tuple(e.dxf.start), tuple(e.dxf.unit_vector), **a)

    def _point(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_point(tuple(e.dxf.location), **a)

    def _circle(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_circle(tuple(e.dxf.center), float(e.dxf.radius), **self._ocs(e, a))

    def _arc(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        d = e.dxf
        lay.add_arc(
            tuple(d.center),
            float(d.radius),
            float(d.start_angle),
            float(d.end_angle),
            **self._ocs(e, a),
        )

    def _ellipse(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        d = e.dxf
        if d.hasattr("extrusion"):
            a["extrusion"] = tuple(d.extrusion)
        lay.add_ellipse(
            tuple(d.center),
            tuple(d.major_axis),
            float(d.ratio),
            float(d.get("start_param", 0.0)),
            float(d.get("end_param", math.tau)),
            **a,
        )

    def _lwpolyline(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        rows = list(e.get_points("xyseb"))
        if len(rows) < 2:
            raise ValueError("degenerate polyline")
        a.update(closed=bool(e.closed), bulges=[float(r[4]) for r in rows])
        if any(r[2] or r[3] for r in rows):
            a["widths"] = [(float(r[2]), float(r[3])) for r in rows]
        if e.dxf.get("elevation", 0):
            a["elevation"] = float(e.dxf.elevation)
        lay.add_lwpolyline([(float(r[0]), float(r[1])) for r in rows], **self._ocs(e, a))

    def _polyline(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        if e.is_2d_polyline:
            pts = [tuple(v.dxf.location)[:2] for v in e.vertices]
            if len(pts) < 2:
                raise ValueError("degenerate polyline")
            a.update(
                closed=bool(e.is_closed), bulges=[float(v.dxf.get("bulge", 0)) for v in e.vertices]
            )
            elev = e.dxf.get("elevation", None)
            if elev is not None:
                z = float(elev.z) if hasattr(elev, "z") else float(elev)
                if z:
                    a["elevation"] = z
            lay.add_lwpolyline(pts, **self._ocs(e, a))
        elif e.is_3d_polyline:
            lay.add_polyline3d(
                [tuple(v.dxf.location) for v in e.vertices], closed=bool(e.is_closed), **a
            )
        else:
            raise ValueError("mesh polyline")

    def _spline(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        control = [tuple(p) for p in e.control_points]
        if control:
            weights = [float(w) for w in e.weights] or None
            lay.add_spline(
                control, int(e.dxf.degree), knots=[float(k) for k in e.knots], weights=weights, **a
            )
        else:
            lay.add_spline_fit([tuple(p) for p in e.fit_points], **a)

    def _solid(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_solid(self._corners(e), **self._ocs(e, a))

    def _trace(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_trace(self._corners(e), **self._ocs(e, a))

    def _three_dface(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        lay.add_3dface(self._corners(e), **a)

    @staticmethod
    def _corners(e: Any) -> list[tuple[float, ...]]:
        d = e.dxf
        pts = [tuple(d.get(k)) for k in ("vtx0", "vtx1", "vtx2", "vtx3") if d.hasattr(k)]
        return pts[:3] if len(pts) == 4 and pts[2] == pts[3] else pts

    def _text_fields(self, e: Any, a: dict[str, Any]) -> dict[str, Any]:
        d = e.dxf
        a.update(
            rotation=float(d.get("rotation", 0.0)), style=self._style(d.get("style", "Standard"))
        )
        if d.get("width", 1.0) != 1.0:
            a["width"] = float(d.width)
        if d.get("oblique", 0.0):
            a["oblique"] = float(d.oblique)
        if d.get("halign", 0) or d.get("valign", 0):
            a.update(halign=int(d.get("halign", 0)), valign=int(d.get("valign", 0)))
            if d.hasattr("align_point"):
                a["align_point"] = tuple(d.align_point)
        return self._ocs(e, a)

    def _style(self, name: str) -> str:
        return _safe(name) if _safe(name) in self.drawing.text_styles else "Standard"

    def _text(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        d = e.dxf
        lay.add_text(d.text, tuple(d.insert), float(d.height), **self._text_fields(e, a))

    def _attdef(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        d = e.dxf
        lay.add_attdef(
            d.tag,
            d.get("text", ""),
            tuple(d.insert),
            float(d.height),
            prompt=d.get("prompt", ""),
            **self._text_fields(e, a),
        )

    def _mtext(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        d = e.dxf
        if d.hasattr("text_direction"):
            a["text_direction"] = tuple(d.text_direction)
        elif d.get("rotation", 0.0):
            r = math.radians(d.rotation)
            a["text_direction"] = (math.cos(r), math.sin(r), 0.0)
        a.update(
            attachment_point=int(d.get("attachment_point", 1)),
            rect_width=float(d.get("width", 0.0)),
            style=self._style(d.get("style", "Standard")),
        )
        lay.add_mtext(e.text, tuple(d.insert), float(d.char_height), **a)

    def _insert(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        d = e.dxf
        name = _safe(d.name, True)
        if name.upper() not in self.blocks:
            raise ValueError("undefined block")
        if d.get("column_count", 1) > 1 or d.get("row_count", 1) > 1:
            raise ValueError("block array")
        scale = (float(d.get("xscale", 1)), float(d.get("yscale", 1)), float(d.get("zscale", 1)))
        if d.hasattr("extrusion"):
            a["extrusion"] = tuple(d.extrusion)
        ref = lay.add_blockref(
            name, tuple(d.insert), scale=scale, rotation=float(d.get("rotation", 0.0)), **a
        )
        for at in e.attribs:
            try:
                lay.add_attrib(
                    ref,
                    at.dxf.tag,
                    at.dxf.text,
                    tuple(at.dxf.insert),
                    float(at.dxf.height),
                    **self._text_fields(at, self._common(at)),
                )
            except Exception:  # noqa: BLE001
                self.skipped["ATTRIB"] += 1

    def _hatch(self, lay: Any, e: Any, a: dict[str, Any]) -> None:
        from ezdxf import path as ezpath

        paths = []
        for boundary in e.paths:
            pts = [
                (float(p.x), float(p.y))
                for p in ezpath.from_hatch_boundary_path(boundary).flattening(FLATTEN_MM)
            ]
            if len(pts) >= 3:
                paths.append(pts)
        if not paths:
            raise ValueError("hatch without boundary")
        d = e.dxf
        if d.get("solid_fill", 1):
            lay.add_hatch(paths, **a)
        else:
            lay.add_hatch(
                paths,
                pattern=d.get("pattern_name", "ANSI31"),
                pattern_angle=float(d.get("pattern_angle", 0.0)),
                pattern_scale=float(d.get("pattern_scale", 1.0)),
                **a,
            )


def main(argv: list[str]) -> int:
    if len(argv) == 5 and argv[1] == "read":
        read(argv[2], argv[3], argv[4])
    elif len(argv) == 6 and argv[1] == "write" and argv[5] in DWG_VERSIONS:
        write(argv[2], argv[3], argv[4], argv[5])
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
