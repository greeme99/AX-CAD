"""W4 (plan §22.4): dimension variables a script may override on one dimension (AutoCAD's
ActiveX Dim* properties map onto them). Lengths are mm at the API and drawing units in the file."""

from typing import Any

from core.dxf.reader import DxfError

LEN = {
    "dimasz",
    "dimexo",
    "dimdli",
    "dimexe",
    "dimrnd",
    "dimdle",
    "dimfxl",
    "dimtxt",
    "dimtsz",
    "dimgap",
}
SIGNED_LEN = {"dimcen", "dimtp", "dimtm"}  # centre mark < 0 = centre lines; tolerances either sign
FLOAT = {
    "dimscale": (1e-6, 1e6),
    "dimaltf": (1e-6, 1e6),
    "dimlfac": (-1e6, 1e6),
    "dimtvp": (-1e6, 1e6),
    "dimtfac": (1e-6, 1e6),
    "dimaltrnd": (0, 1e6),
    "dimjogang": (0.0872, 1.5708),  # 5..90 degrees
}
FLAGS = [
    "dimtol",
    "dimlim",
    "dimtih",
    "dimtoh",
    "dimse1",
    "dimse2",
    "dimalt",
    "dimtofl",
    "dimsah",
    "dimtix",
    "dimsoxd",
    "dimsd1",
    "dimsd2",
    "dimupt",
    "dimfxlon",
]
INT = {
    **{k: (0, 1) for k in FLAGS},
    "dimtfill": (0, 2),
    "dimtfillclr": (0, 256),
    "dimtad": (0, 4),
    "dimzin": (0, 15),
    "dimazin": (0, 3),
    "dimarcsym": (0, 2),
    "dimaltd": (0, 8),
    "dimdec": (0, 8),
    "dimadec": (0, 8),
    "dimtdec": (0, 8),
    "dimalttd": (0, 8),
    "dimclrd": (0, 256),
    "dimclre": (0, 256),
    "dimclrt": (0, 256),
    "dimunit": (1, 8),
    "dimaltu": (1, 8),
    "dimlunit": (1, 6),
    "dimaunit": (0, 4),
    "dimfrac": (0, 2),
    "dimdsep": (32, 0xFFFF),  # a printable character code
    "dimtmove": (0, 2),
    "dimjust": (0, 4),
    "dimtolj": (0, 2),
    "dimtzin": (0, 15),
    "dimaltz": (0, 15),
    "dimalttz": (0, 15),
    "dimfit": (0, 5),
    "dimatfit": (0, 3),
}
LINEWEIGHT = {"dimlwd", "dimlwe"}
TEXT = {"dimpost", "dimapost"}
ARROW = {"dimblk", "dimblk1", "dimblk2", "dimldrblk"}
LINETYPE = {"dimltype", "dimltex1", "dimltex2"}
STYLE = {"dimtxsty"}
NAMES = LEN | SIGNED_LEN | set(FLOAT) | set(INT) | LINEWEIGHT | TEXT | ARROW | LINETYPE | STYLE
LINEWEIGHTS = {
    -3,
    -2,
    -1,
    0,
    5,
    9,
    13,
    15,
    18,
    20,
    25,
    30,
    35,
    40,
    50,
    53,
    60,
    70,
    80,
    90,
    100,
    106,
    120,
    140,
    158,
    200,
    211,
}
ARROWS = {
    "",
    "ARCHTICK",
    "BOXBLANK",
    "BOXFILLED",
    "CLOSED",
    "CLOSEDBLANK",
    "DATUMBLANK",
    "DATUMFILLED",
    "DOT",
    "DOTBLANK",
    "DOTSMALL",
    "INTEGRAL",
    "NONE",
    "OBLIQUE",
    "OPEN",
    "OPEN30",
    "OPEN90",
    "ORIGIN",
    "ORIGIN2",
    "SMALL",
}
_BAD = set(range(32)) | set(range(0x7F, 0xA0)) | {0x2028, 0x2029}


def check(name: str, v: Any) -> None:
    """Raise ValueError when a value does not fit its variable (the request model calls this)."""
    if name not in NAMES:
        raise ValueError(f"{name} is not a dimension variable that can be set")
    if name in LEN | SIGNED_LEN | set(FLOAT):
        lo, hi = FLOAT.get(name, (0, 1e6) if name in LEN else (-1e6, 1e6))
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi
    elif name in INT:
        lo, hi = INT[name]
        ok = isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    elif name in LINEWEIGHT:
        ok = v in LINEWEIGHTS and not isinstance(v, bool)
    else:
        ok = isinstance(v, str) and len(v) <= 255 and not any(ord(c) in _BAD for c in v)
    if not ok:
        raise ValueError(f"{name} value out of range")


def to_file(doc: Any, overrides: dict[str, Any], s: float) -> dict[str, Any]:
    """API values -> the drawing's (lengths / s); named records must exist in the drawing."""
    out: dict[str, Any] = {}
    for k, v in overrides.items():
        if k in ARROW and v.upper() not in ARROWS:
            b = doc.blocks.get(v)
            if b is None or v.startswith("*") or b.is_any_layout:
                raise DxfError("EDIT_BLOCK_NOT_FOUND", "Arrowhead block not found", 422)
        if k in LINETYPE and v.upper() not in ("BYLAYER", "BYBLOCK") and v not in doc.linetypes:
            raise DxfError("EDIT_LINETYPE_NOT_FOUND", "Linetype not found", 422)
        if k in STYLE and not doc.styles.has_entry(v):
            raise DxfError("EDIT_TABLE_NOT_FOUND", "Text style not found", 422)
        out[k] = v / s if k in LEN | SIGNED_LEN else v
    return out


def from_file(attribs: dict[str, Any], s: float) -> dict[str, Any]:
    """A dimension's own overrides, as the API gives them (mm)."""
    out: dict[str, Any] = {}
    for k, v in attribs.items():
        if k not in NAMES or v is None:
            continue
        if k in LEN | SIGNED_LEN:
            out[k] = round(float(v) * s, 6)
        elif isinstance(v, float) and k in INT:
            out[k] = int(v)
        elif isinstance(v, (int, float, str)) and not isinstance(v, bool):
            out[k] = v if not isinstance(v, str) else v[:255]
    return out
