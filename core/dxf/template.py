"""A blank drawing to start drafting in AX-CAD without an outside CAD file.

mm units, a sheet size as the drawing limits (KS B ISO 5457 A0-A4, landscape unless portrait) and
layers for the line types of KS B ISO 128 drawings (thick outline, thin hidden/centre/dimension
lines). The drafting layer starts on OUTLINE."""

import io

import ezdxf
from ezdxf import units

SHEETS = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841)}
# name, ACI colour, linetype, lineweight (1/100 mm), what it is for
LAYERS = [
    ("OUTLINE", 7, "Continuous", 50, "외형선 (굵은 실선)"),
    ("HIDDEN", 2, "DASHED", 25, "숨은선 (가는 파선)"),
    ("CENTER", 1, "CENTER", 25, "중심선 (가는 1점 쇄선)"),
    ("PHANTOM", 6, "PHANTOM", 25, "가상선 (가는 2점 쇄선)"),
    ("DIM", 3, "Continuous", 25, "치수선·지시선"),
    ("TEXT", 7, "Continuous", 25, "문자·주서"),
    ("HATCH", 8, "Continuous", 18, "해칭"),
]
CONFIG_KEY = "AXCAD_DRAWING"  # $CUSTOMPROPERTY holding the settings of a drawing made from 3D (P3)
# ezdxf's standard patterns are drawn in inch-sized steps; x2.5 gives ISO-like 3 mm dashes
LTSCALE = 2.5


def blank_dxf(sheet: str = "A3", portrait: bool = False) -> bytes:
    w, h = SHEETS[sheet]
    if portrait:
        w, h = h, w
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.MM
    doc.header["$MEASUREMENT"] = 1  # metric
    doc.header["$LTSCALE"] = LTSCALE
    layout = doc.modelspace().dxf_layout.dxf  # ezdxf writes $LIMMIN/$LIMMAX from the layout
    layout.limmin, layout.limmax = (0, 0), (w, h)
    for name, color, linetype, lineweight, description in LAYERS:
        layer = doc.layers.add(name, color=color, linetype=linetype, lineweight=lineweight)
        layer.description = description
    doc.header["$CLAYER"] = "OUTLINE"
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
