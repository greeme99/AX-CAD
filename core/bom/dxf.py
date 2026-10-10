"""FN-23 BOM from a DXF revision: one item per block, qty = expanded INSERT/MINSERT instances.

Nested blocks multiply (a block placed 3x that holds 4 bolts gives 12 bolts). Block contents are
counted once per block definition (memoized, never expanded) and the counter merges are budgeted
(MAX_OPS), so deep, repeated or wide nesting fails fast instead of eating the worker.
Part number/name come from the instance ATTRIBs (title-tag patterns); otherwise UNMAPPED.
"""

import hashlib
import json
import re
from collections import Counter
from typing import Any

import ezdxf

from core.dxf.reader import MAX_DEPTH, DxfError

MAX_QTY = 1_000_000_000  # a "BOM" beyond this is a block bomb, not a product
MAX_ITEMS = 5_000
MAX_HANDLES = 50  # source handles kept per item (count is always complete)
MAX_ATTR_CHARS = 200
MAX_OPS = 5_000_000  # counter merges across the block graph: bounds CPU, not just depth
# the one part-number rule (API PATCH uses it too): starts with a letter/digit, no quotes/control
PART_NO = r"^\w[\w.\-/ ]{0,63}$"
PART_NO_RE = re.compile(PART_NO)
CONTROL = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")  # incl. bidi overrides
DEFAULT_TAGS = {
    "part_no": ["PART_NO", "PARTNO", "P/N", "품번"],
    "part_name": ["PART_NAME", "NAME", "DESC", "품명"],
}


def clean(v: str) -> str:
    return CONTROL.sub("", v).strip()[:MAX_ATTR_CHARS]


def _attrs(e: Any, tags: dict[str, list[str]]) -> dict[str, str]:
    by_tag = {t.upper(): k for k, ts in tags.items() for t in ts}
    out: dict[str, str] = {}
    for a in e.attribs:
        key = by_tag.get(a.dxf.tag.upper())
        if key and key not in out and (v := clean(a.dxf.text)):
            out[key] = v
    return out


def bom_from_dxf(path: str, tags: dict[str, list[str]] | None = None) -> dict[str, Any]:
    tags = tags or DEFAULT_TAGS
    doc = ezdxf.readfile(path)
    warnings: list[str] = []
    memo: dict[str, Counter[str]] = {}
    first_attrs: dict[str, dict[str, str]] = {}
    skipped: Counter[str] = Counter()

    ops = 0

    def placed(e: Any) -> str | None:
        if e.dxf.name.startswith("*"):  # anonymous: dimensions, hatches, dynamic-block copies
            skipped["ANONYMOUS"] += 1
            return None
        blk = doc.blocks.get(e.dxf.name)
        if blk is None or blk.block is None or blk.block.dxf.flags & 4:  # missing or XREF
            skipped["XREF_OR_MISSING"] += 1
            return None
        name: str = blk.name  # the definition's own spelling: "bolt" and "BOLT" are one block
        if name not in first_attrs:
            a = _attrs(e, tags)
            if "part_no" in a and not PART_NO_RE.match(a["part_no"]):
                skipped[f"PARTNO_INVALID:{name[:40]}"] += 1
                del a["part_no"]  # a malformed label never reaches the ERP as AUTO
            first_attrs[name] = a
        return name

    def spend(n: int) -> None:
        nonlocal ops
        ops += n
        if ops > MAX_OPS:
            raise DxfError("DXF_BLOCK_LIMIT", "Block structure too complex", 422)

    def contents(name: str, stack: tuple[str, ...]) -> Counter[str]:
        """Every block placed inside `name` (all levels), with counts per one `name`."""
        if name in memo:
            return memo[name]
        if name in stack or len(stack) >= MAX_DEPTH:
            raise DxfError("DXF_BLOCK_LIMIT", "Block nesting too deep or circular", 422)
        total: Counter[str] = Counter()
        e: Any
        for e in doc.blocks.get(name):
            if e.dxftype() != "INSERT" or (child := placed(e)) is None:
                continue
            n = e.mcount
            total[child] += n
            sub = contents(child, (*stack, name))
            spend(len(sub) + 1)
            for k, v in sub.items():
                total[k] += v * n
            if len(total) > MAX_ITEMS or any(v > MAX_QTY for v in total.values()):
                raise DxfError("DXF_BLOCK_LIMIT", "Too many block instances", 422)
        memo[name] = total
        return total

    qty: Counter[str] = Counter()
    level: dict[str, int] = {}
    handles: dict[str, list[str]] = {}
    e: Any
    for e in doc.modelspace().query("INSERT"):
        if (name := placed(e)) is None:
            continue
        n = e.mcount
        qty[name] += n
        level[name] = 1
        handles.setdefault(name, [])
        if len(handles[name]) < MAX_HANDLES:
            handles[name].append(e.dxf.handle)
        sub = contents(name, ())
        spend(len(sub) + 1)
        for child, v in sub.items():
            qty[child] += v * n
            level[child] = min(level.get(child, 99), 2)  # ponytail: nested = level 2, no tree
            if len(handles.setdefault(child, [])) < MAX_HANDLES:
                handles[child].append(e.dxf.handle)  # the top-level placement that holds it
        if len(qty) > MAX_ITEMS or any(v > MAX_QTY for v in qty.values()):
            raise DxfError("DXF_BLOCK_LIMIT", "Too many block instances", 422)

    warnings += [f"BOM_SKIPPED:{k} x{v}" for k, v in sorted(skipped.items())][:50]
    if not qty:
        warnings.append("BOM_NO_BLOCKS")
    items = []
    for name in sorted(qty, key=lambda k: (level[k], k)):
        a = first_attrs.get(name, {})
        items.append(
            {
                "source_name": name[:MAX_ATTR_CHARS],
                "part_no": a.get("part_no"),
                "part_name": a.get("part_name") or name[:MAX_ATTR_CHARS],
                "qty": qty[name],
                "unit": "EA",
                "level": level[name],
                "mapping_status": "AUTO" if a.get("part_no") else "UNMAPPED",
                "source_refs": handles[name],
            }
        )
    return {"source_type": "DXF_BLOCK", "items": items, "warnings": warnings}


def bom_job(path: str, tags: dict[str, list[str]] | None = None) -> str:
    """Worker entry (run_isolated): the parent only json.loads a string."""
    return json.dumps(bom_from_dxf(path, tags))


def bom_from_bodies(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    """FN-23 for the 3D model: STEP/IGES parts with instance counts, native bodies as 1 each."""
    agg: dict[str, dict[str, Any]] = {}
    for b in bodies:
        rows = b.get("parts") or [{"name": b["name"], "instance_count": 1}]
        for p in rows:
            name = clean(str(p["name"])) or "(이름 없음)"
            if int(p["instance_count"]) < 1:
                continue
            it = agg.setdefault(
                name,
                {
                    "source_name": name,
                    "part_no": None,
                    "part_name": name,
                    "qty": 0,
                    "unit": "EA",
                    "level": 1,
                    "mapping_status": "UNMAPPED",  # ponytail: part-name -> part-no master is G1
                    "source_refs": [],
                },
            )
            it["qty"] += int(p["instance_count"])
            if len(agg) > MAX_ITEMS or it["qty"] > MAX_QTY:
                raise DxfError("BOM_TOO_LARGE", "Too many parts", 422)
            if len(it["source_refs"]) < MAX_HANDLES:
                it["source_refs"].append(str(b["feature_id"]))
    warnings = [] if agg else ["BOM_NO_BODIES"]
    return {"source_type": "STEP_ASSEMBLY", "items": list(agg.values()), "warnings": warnings}


def source_hash(bodies: list[dict[str, Any]]) -> str:
    """What the 3D BOM was built from: a later model edit gives another hash (BOM_OUTDATED)."""
    key = [(b["feature_id"], b["name"], b.get("parts"), b.get("volume_mm3")) for b in bodies]
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()
