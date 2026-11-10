"""W7c (plan §22.8): XDATA, dictionaries and XRecords — group code checks, edits and reads.

Values are stored as given, in drawing units (as AutoLISP `entget` / `entmod` see them)."""

import math
import re
from typing import Any

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector

from core.dxf.reader import DxfError

MAX_XDATA_BYTES = 16_383  # per entity, all applications together (the DXF limit)
MAX_XRECORD_BYTES = 65_536
MAX_READ_TAGS = 50_000
MAX_READ_ENTRIES = 10_000
# system dictionaries (layouts, groups, styles, ezdxf's own): not for scripts
PROTECTED_KEYS = ("ACAD", "ACDB", "EZDXF")
_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f  ]*$")  # a newline would inject DXF groups
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2}){1,127}$")
_HANDLE = re.compile(r"^[0-9A-Fa-f]{1,16}$")
INT_RANGE = {"int8": 127, "int16": 32_767, "int32": 2_147_483_647}


def _xdata_kind(code: int) -> str | None:
    if code in (1000, 1003):
        return "str"
    return {
        1002: "brace",
        1004: "hex",
        1005: "handle",
        1040: "float",
        1041: "float",
        1042: "float",
        1070: "int16",
        1071: "int32",
    }.get(code, "point" if 1010 <= code <= 1013 else None)


def group_kind(code: int, where: str) -> str | None:
    """What a group code holds, or None when it is not allowed (`where`: xdata | xrecord).
    XRecords take the plain data codes; owner and pointer handles (330-369) are left out."""
    if code >= 1000:
        return _xdata_kind(code)
    if where == "xdata":
        return None
    for lo, hi, kind in (
        (1, 4, "str"),
        (6, 9, "str"),
        (10, 18, "point"),
        (38, 59, "float"),
        (60, 79, "int16"),
        (90, 99, "int32"),
        (140, 149, "float"),
        (170, 179, "int16"),
        (280, 289, "int8"),
        (290, 299, "bool"),
        (300, 309, "str"),
        (310, 319, "hex"),
        (320, 329, "handle"),
    ):
        if lo <= code <= hi:
            return kind
    return None


def _size(kind: str, value: Any) -> int:
    if kind == "str":
        return len(str(value).encode()) + 3
    if kind == "hex":
        return len(value) // 2 + 3
    return {"point": 26, "float": 10, "int32": 6, "handle": 10}.get(kind, 4)


def check_tags(tags: list[list[Any]], where: str, max_text: int) -> int:
    """Raise ValueError for a group code or value AutoCAD would not take; returns the size."""
    total, depth = 0, 0
    for tag in tags:
        code, value = tag
        kind = group_kind(code, where) if isinstance(code, int) and code > 0 else None
        if kind is None:
            raise ValueError(f"group code {code!r} is not allowed here")
        ok = True
        if kind in ("str", "brace", "hex", "handle"):
            ok = isinstance(value, str)
            if ok and kind == "str":
                ok = len(value) <= max_text and bool(_TEXT.fullmatch(value))
            elif ok and kind == "brace":
                ok = value in ("{", "}")
                depth += 1 if value == "{" else -1
                ok = ok and depth >= 0
            elif ok:
                ok = bool((_HEX if kind == "hex" else _HANDLE).fullmatch(value))
        elif kind == "point":
            ok = isinstance(value, list) and len(value) in (2, 3) and all(_finite(v) for v in value)
        elif kind == "float":
            ok = _finite(value)
        else:  # int8 / int16 / int32 / bool
            limit = 1 if kind == "bool" else INT_RANGE[kind]
            low = 0 if kind == "bool" else -limit - 1
            ok = isinstance(value, int) and not isinstance(value, bool) and low <= value <= limit
        if not ok:
            raise ValueError(f"group {code} value does not fit a {kind}")
        total += _size(kind, value)
    if depth:
        raise ValueError("1002 braces are not balanced")
    return total


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _tag(code: int, value: Any, where: str) -> tuple[int, Any]:
    kind = group_kind(code, where)
    if kind == "point":
        return code, (float(value[0]), float(value[1]), float(value[2]) if len(value) > 2 else 0.0)
    if kind == "float":
        return code, float(value)
    if kind == "hex":
        return code, bytes.fromhex(value)
    if kind == "handle":
        return code, value.upper()
    return code, value


def xdata_bytes(e: Any) -> int:
    total = 0
    for app, tags in e.xdata.data.items() if e.xdata else ():
        total += len(app.encode()) + 3
        for t in tags:
            if t.code == 1001:
                continue
            kind = _xdata_kind(t.code) or "str"
            v = t.value.hex() if isinstance(t.value, (bytes, bytearray)) else t.value
            total += _size(kind, v)
    return total


def set_xdata(doc: Any, e: Any, xdata: dict[str, list[list[Any]]]) -> None:
    """Replace each given application's XDATA on the entity (an empty list removes it)."""
    for app, tags in xdata.items():
        if _protected(app):  # dimension overrides, dynamic blocks, AutoCAD's / ezdxf's own data
            raise DxfError("EDIT_XDATA_PROTECTED", "This application's XDATA is AutoCAD's", 422)
        if not doc.appids.has_entry(app):
            raise DxfError("EDIT_XDATA_APP", "Application is not registered", 422)
        if tags:
            _refs_ok(doc, tags)
            e.set_xdata(app, [_tag(c, v, "xdata") for c, v in tags])
        else:
            e.discard_xdata(app)
    if xdata_bytes(e) > MAX_XDATA_BYTES:
        raise DxfError("EDIT_XDATA_TOO_LARGE", "XDATA exceeds 16 KB per entity", 422)


def _protected(key: str) -> bool:
    return key.upper().startswith(PROTECTED_KEYS)


def _refs_ok(doc: Any, tags: list[list[Any]]) -> None:
    """Handles and layer names must point at something that exists (no dangling references)."""
    for code, value in tags:
        kind = _xdata_kind(code) if code >= 1000 else group_kind(code, "xrecord")
        if kind == "handle" and doc.entitydb.get(value.upper()) is None:
            raise DxfError("EDIT_DICT_REFERENCE", "Handle does not exist", 422)
        if code == 1003 and not doc.layers.has_entry(value):
            raise DxfError("EDIT_DICT_REFERENCE", "Layer does not exist", 422)


def _owned(x: Any, parent: Any, types: tuple[str, ...]) -> bool:
    """Only an XRecord / dictionary the parent owns is edited: an entry may merely point at
    another object (a system dictionary, a layout, an entity) that must not be touched."""
    return (
        hasattr(x, "dxftype")
        and x.is_alive
        and x.dxftype() in types
        and x.dxf.get("owner") == parent.dxf.handle
    )


def _drop(doc: Any, e: Any) -> None:
    """Delete an object and, for a dictionary, the XRecords and dictionaries it owns."""
    stack = [e]
    while stack:
        x = stack.pop()
        if x.dxftype() == "DICTIONARY":
            stack.extend(c for c in x.values() if _owned(c, x, ("DICTIONARY", "XRECORD")))
        doc.entitydb.delete_entity(x)


def dict_op(doc: Any, op: dict[str, Any], owner: Any | None) -> None:
    """Set an XRecord at `path` (missing dictionaries are made) or delete the entry there;
    the root is the named object dictionary, or the owner entity's extension dictionary."""
    path, deleting = op["path"], op["op"] == "delete"
    if _protected(path[0]):
        raise DxfError("EDIT_DICT_PROTECTED", "System dictionaries cannot be changed", 422)
    if owner is None:
        d = doc.rootdict
    elif owner.has_extension_dict:
        d = owner.get_extension_dict().dictionary
    elif deleting:
        raise DxfError("EDIT_DICT_NOT_FOUND", "Dictionary entry not found", 422)
    else:
        d = owner.new_extension_dict().dictionary
    for key in path[:-1]:
        child = d.get(key)
        if child is None:
            if deleting:
                raise DxfError("EDIT_DICT_NOT_FOUND", "Dictionary entry not found", 422)
            child = d.add_new_dict(key)
        elif not _owned(child, d, ("DICTIONARY",)):
            raise DxfError("EDIT_DICT_TYPE", "Path goes through a non-dictionary", 422)
        d = child
    key = path[-1]
    cur = d.get(key)
    if deleting:
        if cur is None:
            raise DxfError("EDIT_DICT_NOT_FOUND", "Dictionary entry not found", 422)
        if not _owned(cur, d, ("DICTIONARY", "XRECORD")):
            raise DxfError("EDIT_DICT_TYPE", "Entry is not an owned XRecord or dictionary", 422)
        d.discard(key)
        _drop(doc, cur)
        return
    if cur is None:
        cur = d.add_xrecord(key)
    elif not _owned(cur, d, ("XRECORD",)):
        raise DxfError("EDIT_DICT_TYPE", "Entry is not an XRecord", 422)
    _refs_ok(doc, op["xrecord"])
    cur.reset([_tag(c, v, "xrecord") for c, v in op["xrecord"]])


# --- reads (run in the isolated worker) ---


def _value(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return v.hex().upper()
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, tuple):
        return [_value(x) for x in v]
    return v


def entity_tags(path: str, handle: str) -> dict[str, Any]:
    """`entget`-level group list of one object (XDATA and the extension dictionary included)."""
    doc = ezdxf.readfile(path)
    e = doc.entitydb.get(handle.upper())
    if e is None or not e.is_alive:
        raise DxfError("ENTITY_NOT_FOUND", "Entity not found", 404)
    tc = TagCollector(dxfversion=doc.dxfversion)
    e.export_dxf(tc)
    if len(tc.tags) > MAX_READ_TAGS:
        raise DxfError("ENTITY_TOO_LARGE", "Entity has too many groups to list", 413)
    return {
        "handle": e.dxf.handle,
        "type": e.dxftype(),
        "tags": [[t.code, _value(t.value)] for t in tc.tags],
    }


def dictionary_entry(path: str, names: list[str], handle: str | None) -> dict[str, Any]:
    """A dictionary's entries or an XRecord's groups, `names` down from the root."""
    doc = ezdxf.readfile(path)
    if handle is None:
        x: Any = doc.rootdict
    else:
        owner = doc.entitydb.get(handle.upper())
        if owner is None or not owner.is_alive or not owner.has_extension_dict:
            raise DxfError("DICT_NOT_FOUND", "Dictionary entry not found", 404)
        x = owner.get_extension_dict().dictionary
    for key in names:
        if not hasattr(x, "dxftype") or x.dxftype() != "DICTIONARY" or x.get(key) is None:
            raise DxfError("DICT_NOT_FOUND", "Dictionary entry not found", 404)
        x = x.get(key)
    if not hasattr(x, "dxftype"):  # a handle ezdxf could not resolve
        raise DxfError("DICT_NOT_FOUND", "Dictionary entry not found", 404)
    t = x.dxftype()
    out: dict[str, Any] = {"type": t, "handle": x.dxf.handle}
    if t == "DICTIONARY":
        items = list(x.items())
        out["entries"] = [
            {
                "name": k,
                "type": v.dxftype() if hasattr(v, "dxftype") else None,
                "handle": v.dxf.handle if hasattr(v, "dxf") else str(v),
            }
            for k, v in items[:MAX_READ_ENTRIES]
        ]
        out["truncated"] = len(items) > MAX_READ_ENTRIES
    elif t == "XRECORD":
        tags = list(x.tags)
        if len(tags) > MAX_READ_TAGS:
            raise DxfError("ENTITY_TOO_LARGE", "XRecord has too many groups to list", 413)
        out["tags"] = [[tag.code, _value(tag.value)] for tag in tags]
    return out
