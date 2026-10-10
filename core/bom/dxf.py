"""FN-23 BOM from a DXF revision: one item per block, qty = expanded INSERT/MINSERT instances.

Nested blocks multiply (a block placed 3x that holds 4 bolts gives 12 bolts). Block contents are
counted once per block definition (memoized), so deep or repeated nesting cannot blow up the work.
Part number/name come from the instance ATTRIBs (title-tag patterns); otherwise UNMAPPED.
"""

import json
from collections import Counter
from typing import Any

import ezdxf

from core.dxf.reader import MAX_DEPTH, DxfError

MAX_QTY = 1_000_000_000  # a "BOM" beyond this is a block bomb, not a product
MAX_ITEMS = 5_000
MAX_HANDLES = 50  # source handles kept per item (count is always complete)
MAX_ATTR_CHARS = 200
DEFAULT_TAGS = {
    "part_no": ["PART_NO", "PARTNO", "P/N", "품번"],
    "part_name": ["PART_NAME", "NAME", "DESC", "품명"],
}


def _attrs(e: Any, tags: dict[str, list[str]]) -> dict[str, str]:
    by_tag = {t.upper(): k for k, ts in tags.items() for t in ts}
    out: dict[str, str] = {}
    for a in e.attribs:
        key = by_tag.get(a.dxf.tag.upper())
        if key and key not in out and a.dxf.text.strip():
            out[key] = a.dxf.text.strip()[:MAX_ATTR_CHARS]
    return out


def bom_from_dxf(path: str, tags: dict[str, list[str]] | None = None) -> dict[str, Any]:
    tags = tags or DEFAULT_TAGS
    doc = ezdxf.readfile(path)
    warnings: list[str] = []
    memo: dict[str, Counter[str]] = {}
    first_attrs: dict[str, dict[str, str]] = {}
    skipped: Counter[str] = Counter()

    def placed(e: Any) -> str | None:
        name = e.dxf.name
        if name.startswith("*"):  # anonymous: dimensions, hatches, dynamic-block copies
            skipped["ANONYMOUS"] += 1
            return None
        blk = doc.blocks.get(name)
        if blk is None or blk.block is None or blk.block.dxf.flags & 4:  # missing or XREF
            skipped["XREF_OR_MISSING"] += 1
            return None
        if name not in first_attrs:
            first_attrs[name] = _attrs(e, tags)
        return name

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
            for k, v in contents(child, (*stack, name)).items():
                total[k] += v * n
            if any(v > MAX_QTY for v in total.values()):
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
        for child, v in contents(name, ()).items():
            qty[child] += v * n
            level[child] = min(level.get(child, 99), 2)  # ponytail: nested = level 2, no tree
            if len(handles.setdefault(child, [])) < MAX_HANDLES:
                handles[child].append(e.dxf.handle)  # the top-level placement that holds it
        if len(qty) > MAX_ITEMS or any(v > MAX_QTY for v in qty.values()):
            raise DxfError("DXF_BLOCK_LIMIT", "Too many block instances", 422)

    warnings += [f"BOM_SKIPPED:{k} x{v}" for k, v in sorted(skipped.items())]
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
            name = str(p["name"])[:MAX_ATTR_CHARS]
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
            if len(it["source_refs"]) < MAX_HANDLES:
                it["source_refs"].append(str(b["feature_id"]))
    warnings = [] if agg else ["BOM_NO_BODIES"]
    return {"source_type": "STEP_ASSEMBLY", "items": list(agg.values()), "warnings": warnings}
