"""Assembly A5: Large-Assembly BVH AABB Culling (1,000+ parts), Lightweight LOD & Rigid Sub-Assembly Grouping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AABB:
    comp_id: int
    min_pt: tuple[float, float, float]
    max_pt: tuple[float, float, float]

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            0.5 * (self.min_pt[0] + self.max_pt[0]),
            0.5 * (self.min_pt[1] + self.max_pt[1]),
            0.5 * (self.min_pt[2] + self.max_pt[2]),
        )


def _overlaps(
    a_min: tuple[float, float, float],
    a_max: tuple[float, float, float],
    b_min: tuple[float, float, float],
    b_max: tuple[float, float, float],
    pad: float,
) -> bool:
    return (
        a_min[0] - pad <= b_max[0]
        and a_max[0] + pad >= b_min[0]
        and a_min[1] - pad <= b_max[1]
        and a_max[1] + pad >= b_min[1]
        and a_min[2] - pad <= b_max[2]
        and a_max[2] + pad >= b_min[2]
    )


@dataclass
class _BVHNode:
    min_pt: tuple[float, float, float]
    max_pt: tuple[float, float, float]
    left: _BVHNode | None = None
    right: _BVHNode | None = None
    leaf_items: list[AABB] | None = None


def _build_bvh(items: list[AABB], leaf_size: int = 8) -> _BVHNode:
    min_x = min(it.min_pt[0] for it in items)
    min_y = min(it.min_pt[1] for it in items)
    min_z = min(it.min_pt[2] for it in items)
    max_x = max(it.max_pt[0] for it in items)
    max_y = max(it.max_pt[1] for it in items)
    max_z = max(it.max_pt[2] for it in items)

    if len(items) <= leaf_size:
        return _BVHNode((min_x, min_y, min_z), (max_x, max_y, max_z), leaf_items=items)

    ext = (max_x - min_x, max_y - min_y, max_z - min_z)
    axis = 0 if (ext[0] >= ext[1] and ext[0] >= ext[2]) else (1 if ext[1] >= ext[2] else 2)
    ordered = sorted(items, key=lambda it: it.center[axis])
    mid = len(ordered) // 2
    return _BVHNode(
        (min_x, min_y, min_z),
        (max_x, max_y, max_z),
        left=_build_bvh(ordered[:mid], leaf_size),
        right=_build_bvh(ordered[mid:], leaf_size),
    )


def bvh_overlapping_pairs(
    boxes: list[AABB],
    *,
    clearance: float = 0.0,
    max_pairs: int = 100_000,
) -> list[tuple[int, int]]:
    """O(N log N) BVH broad-phase AABB overlap query for 1,000+ part assemblies."""
    if len(boxes) < 2:
        return []
    pad = max(0.0, float(clearance))
    root = _build_bvh(boxes)
    pairs: set[tuple[int, int]] = set()

    def query(node: _BVHNode, target: AABB) -> None:
        if len(pairs) >= max_pairs:
            return
        if not _overlaps(node.min_pt, node.max_pt, target.min_pt, target.max_pt, pad):
            return
        if node.leaf_items is not None:
            for it in node.leaf_items:
                if it.comp_id > target.comp_id and _overlaps(
                    it.min_pt, it.max_pt, target.min_pt, target.max_pt, pad
                ):
                    pairs.add((target.comp_id, it.comp_id))
                    if len(pairs) >= max_pairs:
                        return
            return
        if node.left is not None:
            query(node.left, target)
        if node.right is not None:
            query(node.right, target)

    for b in boxes:
        query(root, b)
        if len(pairs) >= max_pairs:
            break

    return sorted(pairs)


def lightweight_lod_plan(
    components: list[dict[str, Any]],
    *,
    triangle_budget: int = 200_000,
) -> dict[str, Any]:
    """Assign FULL vs LIGHTWEIGHT_BBOX rendering LOD to stay within WebGL triangle budget on 1,000+ part assemblies."""
    # Prioritize larger bounding-box diagonal components for FULL mesh, small fasteners become BBOX proxies when over budget
    scored: list[tuple[float, int, int]] = []
    for c in components:
        cid = int(c["component_id"])
        tris = max(12, int(c.get("triangle_count", 500)))
        bbox = c.get("bbox")
        if bbox and "min" in bbox and "max" in bbox:
            dx = float(bbox["max"][0]) - float(bbox["min"][0])
            dy = float(bbox["max"][1]) - float(bbox["min"][1])
            dz = float(bbox["max"][2]) - float(bbox["min"][2])
            diag = (dx * dx + dy * dy + dz * dz) ** 0.5
        else:
            diag = 10.0
        scored.append((diag, cid, tris))

    scored.sort(key=lambda x: -x[0])
    used = 0
    full_ids: list[int] = []
    proxy_ids: list[int] = []
    for _diag, cid, tris in scored:
        if used + tris <= triangle_budget:
            full_ids.append(cid)
            used += tris
        else:
            proxy_ids.append(cid)
    return {
        "total_components": len(components),
        "full_count": len(full_ids),
        "proxy_count": len(proxy_ids),
        "estimated_triangles": used + 12 * len(proxy_ids),
        "full_ids": full_ids,
        "proxy_ids": proxy_ids,
    }
