"""Track 8 P4-5: purchased standard parts of a 3D document (KS metric fasteners). They have no
body: the BOM lists them under their item code and a quote prices them at the master data
purchase price with the same code (price item type PURCHASE)."""

from typing import Any

# kind -> (code prefix, name, standard, has a length)
KINDS: dict[str, tuple[str, str, str, bool]] = {
    "HEX_BOLT": ("HB", "육각볼트", "KS B 1002", True),
    "SOCKET_BOLT": ("SB", "육각 구멍붙이 볼트", "KS B 1003", True),
    "HEX_NUT": ("HN", "육각너트", "KS B 1012", False),
    "WASHER": ("PW", "평와셔", "KS B 1326", False),
    "SPRING_WASHER": ("SW", "스프링와셔", "KS B 1324", False),
}


def item_code(p: dict[str, Any]) -> str:
    """Purchase code of a standard part, e.g. HB-M8x25 (bolts carry their length)."""
    prefix, _, _, long = KINDS[p["kind"]]
    return f"{prefix}-{p['size']}" + (f"x{p['length']:g}" if long else "")


def label(p: dict[str, Any]) -> str:
    """e.g. 육각볼트 M8×25 (KS B 1002)."""
    _, name, std, long = KINDS[p["kind"]]
    return f"{name} {p['size']}" + (f"×{p['length']:g}" if long else "") + f" ({std})"
