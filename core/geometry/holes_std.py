"""Track 8 P2-4: the hole wizard's metric table (pure Python, no kernel).

- Threads: KS B 0201 / ISO 261·262 metric coarse pitches M1.6-M64 and the fine pitches of M8 and up.
  Tap drill = major diameter - pitch (the usual ISO 2306 6H rule).
- Bolt clearance holes: KS B 1007 / ISO 273 fine, medium, coarse series.
- Counterbores for hexagon socket head cap screws (KS B 1003 / ISO 4762), the usual JIS B 1176 style
  reference sizes; countersinks at 90 deg to 2.24 d (ISO 10642 head diameter). Both are defaults the
  user may override.
"""

from typing import Any

# size -> coarse pitch
COARSE: dict[float, float] = {
    1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75,
    14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 33: 3.5, 36: 4.0,
    39: 4.0, 42: 4.5, 45: 4.5, 48: 5.0, 52: 5.0, 56: 5.5, 60: 5.5, 64: 6.0,
}  # fmt: skip
FINE: dict[float, tuple[float, ...]] = {
    8: (1.0,), 10: (1.25, 1.0), 12: (1.5, 1.25), 14: (1.5,), 16: (1.5,), 18: (2.0, 1.5),
    20: (2.0, 1.5), 22: (2.0, 1.5), 24: (2.0,), 27: (2.0,), 30: (2.0,), 33: (2.0,), 36: (3.0,),
    39: (3.0,), 42: (3.0,), 45: (3.0,), 48: (3.0,), 52: (4.0,), 56: (4.0,), 60: (4.0,), 64: (4.0,),
}  # fmt: skip
# size -> clearance hole (fine, medium, coarse)
CLEARANCE: dict[float, tuple[float, float, float]] = {
    1.6: (1.7, 1.8, 2.0), 2: (2.2, 2.4, 2.6), 2.5: (2.7, 2.9, 3.1), 3: (3.2, 3.4, 3.6),
    4: (4.3, 4.5, 4.8), 5: (5.3, 5.5, 5.8), 6: (6.4, 6.6, 7.0), 8: (8.4, 9.0, 10.0),
    10: (10.5, 11.0, 12.0), 12: (13.0, 13.5, 14.5), 14: (15.0, 15.5, 16.5), 16: (17.0, 17.5, 18.5),
    18: (19.0, 20.0, 21.0), 20: (21.0, 22.0, 24.0), 22: (23.0, 24.0, 26.0), 24: (25.0, 26.0, 28.0),
    27: (28.0, 30.0, 32.0), 30: (31.0, 33.0, 35.0), 33: (34.0, 36.0, 38.0), 36: (37.0, 39.0, 42.0),
    39: (40.0, 42.0, 45.0), 42: (43.0, 45.0, 48.0), 45: (46.0, 48.0, 52.0), 48: (50.0, 52.0, 56.0),
    52: (54.0, 56.0, 62.0), 56: (58.0, 62.0, 66.0), 60: (62.0, 66.0, 70.0), 64: (66.0, 70.0, 74.0),
}  # fmt: skip
FITS = ("FINE", "MEDIUM", "COARSE")
# size -> counterbore (diameter, depth) for a socket head cap screw
COUNTERBORE: dict[float, tuple[float, float]] = {
    3: (6.5, 3.3), 4: (8.0, 4.4), 5: (9.5, 5.4), 6: (11.0, 6.5), 8: (14.0, 8.6), 10: (17.5, 10.8),
    12: (20.0, 13.0), 14: (23.0, 15.2), 16: (26.0, 17.5), 20: (32.0, 21.5), 24: (39.0, 25.5),
    30: (48.0, 32.0),
}  # fmt: skip
COUNTERSINK_SHARE = 2.24  # x the bolt size, at 90 degrees
SIZES = tuple(f"M{s:g}" for s in COARSE)


class SpecError(ValueError):
    pass


def _size(name: str) -> float:
    if name not in SIZES:
        raise SpecError(f"size must be one of {', '.join(SIZES)}")
    return next(s for s in COARSE if f"M{s:g}" == name)


def resolve(spec: dict[str, Any]) -> dict[str, Any]:
    """Diameters of a hole spec: drill, thread (TAPPED) or clearance, and the head."""
    d = _size(spec["size"])
    coarse = COARSE[d]
    pitch = spec.get("pitch") or coarse
    if pitch != coarse and pitch not in FINE.get(d, ()):
        raise SpecError(f"{spec['size']} has pitch {coarse} or fine {FINE.get(d, ())}")
    out: dict[str, Any] = {"size": spec["size"], "kind": spec["kind"]}
    if spec["kind"] == "TAPPED":
        out |= {
            "diameter": round(d - pitch, 3),
            "thread": {
                "label": spec["size"] if pitch == coarse else f"{spec['size']}x{pitch:g}",
                "major": d,
                "pitch": pitch,
                "fine": pitch != coarse,
            },
        }
    else:
        out["diameter"] = CLEARANCE[d][FITS.index(spec.get("fit") or "MEDIUM")]
    head = spec.get("head") or "NONE"
    if head == "COUNTERBORE":
        std = COUNTERBORE.get(d)
        dia = spec.get("head_diameter") or (std and std[0])
        depth = spec.get("head_depth") or (std and std[1])
        if not dia or not depth:
            raise SpecError(f"no standard counterbore for {spec['size']}: give its size")
        out["head"] = {"kind": head, "diameter": dia, "depth": depth}
    elif head == "COUNTERSINK":
        dia = spec.get("head_diameter") or round(COUNTERSINK_SHARE * d, 2)
        out["head"] = {"kind": head, "diameter": dia}
    if "head" in out and out["head"]["diameter"] <= out["diameter"]:
        raise SpecError("the head must be wider than the hole")
    return out
