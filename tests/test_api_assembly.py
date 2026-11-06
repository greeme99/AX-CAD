"""Track 9 Q2: assembly API — components, mates, solve, interference, BOM, export (TC-171)."""

import math

import pytest


def doc(client, w, no, doc_type):
    r = client.post(
        f"/api/projects/{w.pid}/documents",
        json={"doc_no": no, "doc_type": doc_type, "title": no},
        headers=w.h["designer"],
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["document_id"]


def prim(client, w, d, **params):
    r = client.post(
        f"/api/documents/{d}/features",
        json={"feature_type": "PRIMITIVE", "params": params},
        headers=w.h["designer"],
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["feature_id"]


def faces(client, w, fid):
    m = client.get(f"/api/features/{fid}/mesh", headers=w.h["designer"]).json()["data"]
    return [f["sig"] for f in m["faces"]]


def plane_at(sigs, axis, value):
    for s in sigs:
        if s["kind"] == "PLANE" and abs(s["mid"][axis] - value) < 1e-9 and abs(s["dir"][axis]) == 1:
            return s
    raise AssertionError("no such face")


@pytest.fixture
def parts(client, world):
    plate_doc, block_doc, pin_doc = (
        doc(client, world, n, "PART") for n in ("PLATE", "BLOCK", "PIN")
    )
    plate = prim(client, world, plate_doc, kind="BOX", length=100, width=100, height=10)
    block = prim(client, world, block_doc, kind="BOX", length=40, width=40, height=20)
    pin = prim(client, world, pin_doc, kind="CYLINDER", radius=5, height=30)
    asm = doc(client, world, "ASM1", "ASSEMBLY")
    return {
        "asm": asm,
        "plate": plate,
        "block": block,
        "pin": pin,
        "docs": (plate_doc, block_doc, pin_doc),
    }


def add(client, w, asm, fid, **kw):
    r = client.post(
        f"/api/documents/{asm}/components",
        json={"source_feature_id": fid, **kw},
        headers=w.h["designer"],
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_place_mate_solve_interference_bom_export(client, world, parts):
    h, a = world.h["designer"], parts["asm"]
    c = client.get(f"/api/documents/{a}/assembly/candidates", headers=h).json()["data"]
    assert {i["feature_id"] for i in c["items"]} == {parts["plate"], parts["block"], parts["pin"]}
    plate = add(client, world, a, parts["plate"])
    block = add(client, world, a, parts["block"], placement={"t": [30, 30, 40], "r": [5, -3, 10]})
    assert plate["fixed"] and not block["fixed"] and plate["name"] == "PLATE-1"
    # block bottom on plate top
    top = plane_at(faces(client, world, parts["plate"]), 2, 10)
    bottom = plane_at(faces(client, world, parts["block"]), 2, 0)
    mate = {
        "mate_type": "COINCIDENT",
        "a": {"component_id": plate["component_id"], "face": top},
        "b": {"component_id": block["component_id"], "face": bottom},
    }
    r = client.post(f"/api/documents/{a}/mates", json=mate, headers=h)
    assert r.status_code == 200, r.text
    st = r.json()["data"]
    assert st["mates"][0]["status"] == "OK" and st["dof"] == 3
    pb = next(x for x in st["components"] if x["component_id"] == block["component_id"])[
        "placement"
    ]
    assert pb["t"][2] == pytest.approx(10, abs=1e-6)
    assert [abs(v) < 1e-6 for v in pb["r"][:2]] == [True, True]
    # resting on the plate: no clash, touching within clearance
    r = client.post(f"/api/documents/{a}/interference", json={"clearance": 1}, headers=h).json()[
        "data"
    ]
    assert r["clashes"] == [] and len(r["near"]) == 1
    # a pin pushed through the plate clashes: pi * 25 * 10 mm3
    pin = add(client, world, a, parts["pin"], placement={"t": [80, 80, -5], "r": [0, 0, 0]})
    r = client.post(f"/api/documents/{a}/interference", json={}, headers=h).json()["data"]
    clash = r["clashes"][0]
    assert {clash["a"], clash["b"]} == {plate["component_id"], pin["component_id"]}
    assert clash["volume_mm3"] == pytest.approx(math.pi * 25 * 10, rel=1e-6)
    # a second plate instance: BOM counts per part document
    add(client, world, a, parts["plate"], placement={"t": [0, 0, -50], "r": [0, 0, 0]})
    r = client.post(f"/api/documents/{a}/boms", json={"source": "MODEL"}, headers=h)
    assert r.status_code == 200, r.text
    bom = r.json()["data"]
    assert bom["source_type"] == "ASSEMBLY"
    qty = {i["part_name"]: i["qty"] for i in bom["items"]}
    assert qty == {"PLATE PLATE": 2, "BLOCK BLOCK": 1, "PIN PIN": 1}
    # STEP of the placed bodies
    r = client.get(f"/api/documents/{a}/assembly/export?format=STEP", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"ISO-10303-21")
    # deleting a component takes its mates with it
    st = client.delete(f"/api/components/{block['component_id']}", headers=h).json()["data"]
    assert st["mates"] == [] and len(st["components"]) == 3


def test_assembly_rules(client, world, parts):
    h, a = world.h["designer"], parts["asm"]
    plate = add(client, world, a, parts["plate"])
    pin = add(client, world, a, parts["pin"])
    # a cylinder cannot be COINCIDENT with a plane; nothing is stored
    side = next(s for s in faces(client, world, parts["pin"]) if s["kind"] == "CYLINDER")
    top = plane_at(faces(client, world, parts["plate"]), 2, 10)
    bad = {
        "mate_type": "COINCIDENT",
        "a": {"component_id": plate["component_id"], "face": top},
        "b": {"component_id": pin["component_id"], "face": side},
    }
    r = client.post(f"/api/documents/{a}/mates", json=bad, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "ASSEMBLY_MATE_INVALID"
    assert client.get(f"/api/documents/{a}/assembly", headers=h).json()["data"]["mates"] == []
    r = client.post(f"/api/documents/{a}/mates", json={**bad, "mate_type": "DISTANCE"}, headers=h)
    assert r.status_code == 422  # DISTANCE needs a value
    # parts only from this project, not assemblies, and only on assembly documents
    r = client.post(f"/api/documents/{a}/components", json={"source_feature_id": 999999}, headers=h)
    assert r.status_code == 422
    r = client.post(
        f"/api/documents/{parts['docs'][0]}/components",
        json={"source_feature_id": parts["plate"]},
        headers=h,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ASSEMBLY"
    # placement bounds
    r = client.patch(
        f"/api/components/{pin['component_id']}",
        json={"placement": {"t": [0, 0, 1e9], "r": [0, 0, 0]}},
        headers=h,
    )
    assert r.status_code == 422
    r = client.patch(
        f"/api/components/{pin['component_id']}",
        json={"placement": {"t": [1, 2, 3], "r": [0, 90, 0]}, "fixed": True},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["data"]["placement"] == {
        "t": [1, 2, 3],
        "r": [0, 90, 0],
    }
    # viewers read, never write
    assert client.get(f"/api/documents/{a}/assembly", headers=world.h["viewer"]).status_code == 200
    r = client.post(
        f"/api/documents/{a}/components",
        json={"source_feature_id": parts["plate"]},
        headers=world.h["viewer"],
    )
    assert r.status_code == 403


def test_component_follows_part_edits(client, world, parts):
    """A fillet added to the part later consumes the box; the assembly shows the filleted body."""
    h, a = world.h["designer"], parts["asm"]
    c = add(client, world, a, parts["block"])
    r = client.post(
        f"/api/documents/{parts['docs'][1]}/features",
        json={
            "feature_type": "FILLET",
            "params": {"target_feature_id": parts["block"], "radius": 2, "edges": "VERTICAL"},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    fillet = r.json()["data"]["feature_id"]
    got = client.get(f"/api/documents/{a}/assembly", headers=h).json()["data"]["components"][0]
    assert (
        got["component_id"] == c["component_id"]
        and got["body_feature_id"] == fillet
        and got["status"] == "OK"
    )


def test_copilot_checks_an_assembly(client, world, parts):
    h, a = world.h["designer"], parts["asm"]
    p1 = add(client, world, a, parts["plate"])
    add(client, world, a, parts["block"])
    codes = lambda: {
        f["code"]
        for f in client.get(f"/api/copilot/findings?document_id={a}", headers=h).json()["data"][
            "findings"
        ]
    }
    assert codes() == {"ASM_NO_MATES"}
    client.patch(f"/api/components/{p1['component_id']}", json={"fixed": False}, headers=h)
    assert codes() == {"ASM_NO_MATES", "ASM_NOTHING_FIXED"}


def test_limits_and_concurrency(client, world, parts, monkeypatch):
    """BUG-56: interference is capped (partial result), viewers cannot run it, a solve that races
    an edit is refused, a diverged solve is not stored."""
    from backend.api import routes_assembly
    from core.geometry import assembly as asm

    h, a = world.h["designer"], parts["asm"]
    for _ in range(4):  # four plates on top of each other: 6 overlapping pairs
        add(client, world, a, parts["plate"])
    r = client.post(f"/api/documents/{a}/interference", json={}, headers=world.h["viewer"])
    assert r.status_code == 403
    monkeypatch.setattr(asm, "MAX_PAIRS", 2)
    r = client.post(
        f"/api/documents/{a}/interference", json={}, headers=world.h["reviewer"]
    ).json()["data"]
    assert r["truncated"] and r["pairs_checked"] == 2 and len(r["clashes"]) == 2

    st = client.get(f"/api/documents/{a}/assembly", headers=h).json()["data"]
    c1, c2 = st["components"][0]["component_id"], st["components"][1]["component_id"]
    top = plane_at(faces(client, world, parts["plate"]), 2, 10)
    bottom = plane_at(faces(client, world, parts["plate"]), 2, 0)
    mate = {
        "mate_type": "COINCIDENT",
        "a": {"component_id": c1, "face": top},
        "b": {"component_id": c2, "face": bottom},
    }
    real = routes_assembly.run_kernel

    def edit_meanwhile(fn, *args, **kw):
        if fn is asm.solve:  # someone moves a part while the kernel solves
            client.patch(
                f"/api/components/{c2}",
                json={"placement": {"t": [1, 1, 1], "r": [0, 0, 0]}},
                headers=h,
            )
        return real(fn, *args, **kw)

    monkeypatch.setattr(routes_assembly, "run_kernel", edit_meanwhile)
    r = client.post(f"/api/documents/{a}/mates", json=mate, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ASSEMBLY_CHANGED"
    monkeypatch.setattr(routes_assembly, "run_kernel", real)
    r = client.post(f"/api/documents/{a}/assembly/solve", headers=h)
    assert r.status_code == 200 and r.json()["data"]["mates"][0]["status"] == "OK"

    def diverge(fn, *args, **kw):
        if fn is asm.solve:
            raise asm.GeomError("ASSEMBLY_SOLVE_DIVERGED", "x")
        return real(fn, *args, **kw)

    monkeypatch.setattr(routes_assembly, "run_kernel", diverge)
    before = client.get(f"/api/documents/{a}/assembly", headers=h).json()["data"]["components"]
    r = client.post(f"/api/documents/{a}/assembly/solve", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "ASSEMBLY_SOLVE_DIVERGED"
    assert (
        client.get(f"/api/documents/{a}/assembly", headers=h).json()["data"]["components"] == before
    )
