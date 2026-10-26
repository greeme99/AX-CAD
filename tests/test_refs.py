"""Track 8 P2-4: edges and faces picked by signature survive a rebuild; lost ones say so."""

import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE

from core.geometry import dressup as d
from core.geometry.errors import GeomError
from core.geometry.mesh import tessellate
from core.geometry.metrics import measure
from core.geometry.refs import _items, edge_sig, face_sig, resolve

REL = 1e-6


def box(w=40.0, h=30.0, t=10.0):
    return BRepPrimAPI_MakeBox(w, h, t).Shape()


def vol(s):
    return measure(s)["volume_mm3"]


def sig_of(shape, kind, pred):
    sig = edge_sig if kind == TopAbs_EDGE else face_sig
    return [sig(x) for x in _items(shape, kind) if pred(sig(x))]


def test_signatures_of_a_box():
    e = sig_of(box(), TopAbs_EDGE, lambda s: s["mid"] == [40.0, 30.0, 5.0])[0]
    assert e == {"kind": "LINE", "mid": [40.0, 30.0, 5.0], "length": 10.0, "dir": [0.0, 0.0, 1.0]}
    top = sig_of(box(), TopAbs_FACE, lambda s: s["mid"][2] == 10.0)[0]
    assert top["kind"] == "PLANE" and top["area"] == 1200.0 and top["dir"] == [0.0, 0.0, 1.0]


def test_one_picked_edge_fillets_only_that_corner():
    r = 2.0
    ref = sig_of(box(), TopAbs_EDGE, lambda s: s["mid"] == [40.0, 30.0, 5.0])
    b = box()
    res = d.fillet(b, r, d.picked(b, {"edge_refs": ref}))
    assert vol(res) == pytest.approx(12000 - (r * r - math.pi * r * r / 4) * 10, rel=REL)


def test_pick_survives_a_changed_earlier_feature():
    """Picked on a 40-wide plate; the plate becomes 44 wide: the same corner is found again."""
    ref = sig_of(box(), TopAbs_EDGE, lambda s: s["mid"] == [40.0, 30.0, 5.0])
    wider = box(44.0)  # 4 mm on a 51 mm body
    (e,) = resolve(wider, ref, "EDGE")
    assert edge_sig(e)["mid"] == [44.0, 30.0, 5.0]


def test_lost_reference():
    ref = sig_of(box(), TopAbs_EDGE, lambda s: s["mid"] == [40.0, 30.0, 5.0])
    with pytest.raises(GeomError) as e:
        resolve(box(200.0, 200.0, 50.0), ref, "EDGE")  # nothing near any more
    assert e.value.code == "GEOM_REF_LOST" and e.value.details == {"refs": [0]}
    ref[0]["kind"] = "CIRCLE"
    with pytest.raises(GeomError):
        resolve(box(), ref, "EDGE")


def test_picked_face_chamfers_its_edges():
    top = sig_of(box(), TopAbs_FACE, lambda s: s["mid"][2] == 10.0)
    b = box()
    res = d.chamfer(b, 1.0, d.picked(b, {"face_refs": top}))
    assert vol(res) == pytest.approx(vol(d.chamfer(box(), 1.0, "TOP")), rel=REL)


def test_shell_and_draft_on_picked_faces():
    side = sig_of(box(), TopAbs_FACE, lambda s: s["mid"][0] == 40.0)  # the +X wall
    out = d.dressup_job(
        "SHELL", d.to_brep(box()), {"thickness": 1.0, "open_face": "NONE", "face_refs": side}
    )
    assert out[1]["volume_mm3"] == pytest.approx(12000 - 39 * 28 * 8, rel=REL)
    b = box()
    one = d.draft(b, 5.0, "+Z", "BOTTOM", resolve(b, side, "FACE"))
    assert vol(one) == pytest.approx(12000 - 30 * 10 * 10 * math.tan(math.radians(5)) / 2, rel=REL)


def test_mesh_carries_signatures_and_edges():
    m = tessellate(box())
    assert len(m["edges"]) == 12 and all(len(e["points"]) == 6 for e in m["edges"])
    assert {f["sig"]["kind"] for f in m["faces"]} == {"PLANE"}
