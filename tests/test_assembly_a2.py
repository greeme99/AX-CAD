"""Assembly A2 tests: rigid sub-assemblies, linear/circular patterns, mirror, replace (ASM-T05~T06)."""

from __future__ import annotations

import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from core.geometry import assembly as asm
from core.geometry.errors import GeomError
from core.geometry.refs import face_sig


def test_asm_t05_two_level_subassembly_and_circular_pattern_6() -> None:
    """2-level rigid sub-assembly expansion matches hand-calculated transforms; 6x circular pattern works."""
    # Level 0 (doc 1): places SubAsm (doc 2) at t=[100, 0, 0], rotated +90 deg about Z
    # Level 1 (doc 2): places Part A (doc 3) at t=[10, 0, 5], rotated 0
    # World position of Part A = [100, 0, 0] + Rz(90)*[10, 0, 5] = [100, 10, 5]
    tree = {
        1: [
            {
                "component_id": 10,
                "name": "Sub1",
                "component_kind": "SUBASSEMBLY",
                "source_document_id": 2,
                "source_feature_id": None,
                "placement": {"t": [100.0, 0.0, 0.0], "r": [0.0, 0.0, 90.0]},
                "suppressed": False,
                "visible": True,
            }
        ],
        2: [
            {
                "component_id": 20,
                "name": "PartA",
                "component_kind": "PART",
                "source_document_id": 3,
                "source_feature_id": 301,
                "placement": {"t": [10.0, 0.0, 5.0], "r": [0.0, 0.0, 0.0]},
                "suppressed": False,
                "visible": True,
            },
            {
                "component_id": 21,
                "name": "SuppressedPart",
                "component_kind": "PART",
                "source_document_id": 3,
                "source_feature_id": 301,
                "placement": {"t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
                "suppressed": True,
                "visible": True,
            },
        ],
    }
    flat = asm.expand_hierarchy(1, lambda did: tree.get(did, []))
    assert len(flat) == 1
    assert flat[0]["path"] == "Sub1/PartA"
    assert flat[0]["world_placement"]["t"] == pytest.approx([100.0, 10.0, 5.0], abs=1e-6)
    assert flat[0]["world_placement"]["r"] == pytest.approx([0.0, 0.0, 90.0], abs=1e-6)

    # Circular pattern of 6 equal-spaced instances around Z axis at radius 50
    src = {"t": [50.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]}
    derived = asm.pattern_placements(
        src,
        "CIRCULAR",
        {
            "axis_origin": [0.0, 0.0, 0.0],
            "axis_dir": [0.0, 0.0, 1.0],
            "angle_deg": 360.0,
            "count": 6,
            "equal_spacing": True,
        },
    )
    assert len(derived) == 5
    # Instance 1 is at 60 deg: [50*cos(60), 50*sin(60), 0] = [25, 25*sqrt(3), 0]
    assert derived[0]["t"] == pytest.approx([25.0, 25.0 * math.sqrt(3.0), 0.0], abs=1e-6)
    # Instance 3 is at 180 deg: [-50, 0, 0]
    assert derived[2]["t"] == pytest.approx([-50.0, 0.0, 0.0], abs=1e-6)

    # Linear 2D pattern (3 x 2 -> 5 derived instances)
    lin = asm.pattern_placements(
        {"t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
        "LINEAR",
        {
            "dir": [1, 0, 0],
            "spacing": 20.0,
            "count": 3,
            "dir2": [0, 1, 0],
            "spacing2": 15.0,
            "count2": 2,
        },
    )
    assert len(lin) == 5
    assert lin[-1]["t"] == pytest.approx([40.0, 15.0, 0.0], abs=1e-6)


def test_asm_t05_cycle_depth_and_limit_guards() -> None:
    """Cycle raises ASSEMBLY_CYCLE; depth > 8 raises ASSEMBLY_DEPTH_EXCEEDED; > 1000 raises LIMIT."""
    # Cycle: 1 -> 2 -> 1
    cyc_tree = {
        1: [
            {
                "component_id": 1,
                "name": "S2",
                "component_kind": "SUBASSEMBLY",
                "source_document_id": 2,
                "placement": {"t": [0, 0, 0], "r": [0, 0, 0]},
                "suppressed": False,
            }
        ],
        2: [
            {
                "component_id": 2,
                "name": "S1",
                "component_kind": "SUBASSEMBLY",
                "source_document_id": 1,
                "placement": {"t": [0, 0, 0], "r": [0, 0, 0]},
                "suppressed": False,
            }
        ],
    }
    with pytest.raises(GeomError) as exc_c:
        asm.expand_hierarchy(1, lambda d: cyc_tree.get(d, []))
    assert exc_c.value.code == "ASSEMBLY_CYCLE"

    # Depth > 8 (chain of 10 docs)
    deep_tree = {
        i: [
            {
                "component_id": i,
                "name": f"L{i}",
                "component_kind": "SUBASSEMBLY",
                "source_document_id": i + 1,
                "placement": {"t": [0, 0, 0], "r": [0, 0, 0]},
                "suppressed": False,
            }
        ]
        for i in range(1, 11)
    }
    with pytest.raises(GeomError) as exc_d:
        asm.expand_hierarchy(1, lambda d: deep_tree.get(d, []))
    assert exc_d.value.code == "ASSEMBLY_DEPTH_EXCEEDED"

    # Limit > 1000 expanded parts
    wide_parts = [
        {
            "component_id": i,
            "name": f"P{i}",
            "component_kind": "PART",
            "source_document_id": 99,
            "source_feature_id": 1,
            "placement": {"t": [float(i), 0, 0], "r": [0, 0, 0]},
            "suppressed": False,
        }
        for i in range(1001)
    ]
    with pytest.raises(GeomError) as exc_l:
        asm.expand_hierarchy(1, lambda d: wide_parts if d == 1 else [])
    assert exc_l.value.code == "ASSEMBLY_LIMIT_EXCEEDED"


def test_asm_t06_mirror_placement_and_replace_signature_resolution() -> None:
    """Mirror reflects position across plane; similar box keeps face ref, cylinder breaks it."""
    mir = asm.mirror_placement(
        {"t": [30.0, 10.0, 5.0], "r": [0.0, 0.0, 0.0]},
        plane_origin=[0.0, 0.0, 0.0],
        plane_normal=[1.0, 0.0, 0.0],
    )
    assert mir["t"] == pytest.approx([-30.0, 10.0, 5.0], abs=1e-6)

    # Pick top face signature of a 20x20x10 box
    box1 = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20.0, 20.0, 10.0).Shape()
    ex = TopExp_Explorer(box1, TopAbs_FACE)
    top_sig = None
    while ex.More():
        sig = face_sig(TopoDS.Face(ex.Current()))
        if sig["kind"] == "PLANE" and sig["mid"][2] == pytest.approx(10.0):
            top_sig = sig
            break
        ex.Next()
    assert top_sig is not None

    # Similar box (same top face) resolves cleanly
    box2 = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20.0, 20.0, 10.0).Shape()
    g_ok = asm.ref_geom(box2, top_sig, "FACE")
    assert g_ok["kind"] == "PLANE"

    # Completely different cylinder fails signature resolution -> API marks mate BROKEN
    cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(500, 500, 500), gp_Dir(0, 0, 1)), 5.0, 5.0).Shape()
    with pytest.raises(GeomError):
        asm.ref_geom(cyl, top_sig, "FACE")
