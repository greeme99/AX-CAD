"""Synthetic 2D sample drawings (Track 8 P1): every display feature a shop drawing uses, built
with ezdxf so no real (confidential) drawing is needed in the repository."""

from pathlib import Path

import ezdxf
from ezdxf import units


def hatch_doc():
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    h = msp.add_hatch(color=1, dxfattribs={"layer": "HATCH"})
    h.paths.add_polyline_path([(0, 0), (40, 0), (40, 20), (0, 20)], is_closed=True)
    h.set_pattern_fill("ANSI31", scale=2)
    s = msp.add_hatch(color=3)
    s.paths.add_polyline_path([(50, 0), (70, 0), (70, 20)], is_closed=True)  # solid fill
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
    return doc


def block_doc():
    doc = ezdxf.new("R2018", setup=True)
    blk = doc.blocks.new("TAG")
    blk.add_circle((0, 0), 5)
    blk.add_attdef("NO", (6, 0), dxfattribs={"height": 2.5})
    msp = doc.modelspace()
    for i in range(3):
        ins = msp.add_blockref("TAG", (i * 20, 0))
        ins.add_auto_attribs({"NO": f"P-{i + 1}"})
    return doc


def annotation_doc():
    doc = ezdxf.new("R2018", setup=True)
    std = doc.dimstyles.get("Standard")
    std.dxf.dimtxt, std.dxf.dimasz, std.dxf.dimexe, std.dxf.dimexo = 2.5, 2.5, 1.25, 0.625  # mm
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_linear_dim(base=(0, 10), p1=(0, 0), p2=(100, 0), dimstyle="Standard").render()
    msp.add_leader([(50, 0), (60, 15), (75, 15)], dimstyle="Standard")
    msp.add_point((30, 30))
    msp.add_mtext("NOTE\\PSECOND LINE", dxfattribs={"insert": (0, 40), "char_height": 3})
    return doc


def linetype_doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("CENTER", linetype="CENTER", lineweight=25)
    doc.layers.add("HIDDEN", linetype="DASHDOT")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "CENTER"})  # BYLAYER linetype + weight
    msp.add_line((0, 10), (100, 10), dxfattribs={"layer": "HIDDEN", "lineweight": 50})
    msp.add_line((0, 20), (100, 20), dxfattribs={"linetype": "DASHED", "ltscale": 2})
    return doc


def sheet_doc():
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_circle((50, 50), 20)
    ps = doc.paperspace()
    ps.page_setup(size=(420, 297), margins=(10, 10, 10, 10), units="mm")
    ps.add_lwpolyline([(10, 10), (410, 10), (410, 287), (10, 287)], close=True)  # frame
    ps.add_text("TITLE: BRACKET", dxfattribs={"insert": (300, 20), "height": 5})
    ps.add_viewport(center=(150, 150), size=(200, 200), view_center_point=(50, 50), view_height=100)
    return doc


SAMPLES = {
    "hatch": hatch_doc,
    "blocks": block_doc,
    "annotations": annotation_doc,
    "linetypes": linetype_doc,
    "sheet": sheet_doc,
}


def write_all(folder: Path) -> dict[str, Path]:
    out = {}
    for name, make in SAMPLES.items():
        doc = make()
        doc.units = units.MM  # ezdxf.new defaults to metres
        out[name] = folder / f"{name}.dxf"
        doc.saveas(out[name])
    return out
