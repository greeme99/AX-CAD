"""Track 9 Q3: sheet metal features through the API — build, flat pattern / DXF, and the 3D quote
with the default laser and press brake rules against a hand-worked reference (TC-175)."""

import io
import math
from decimal import ROUND_FLOOR, ROUND_HALF_UP
from decimal import Decimal as D

import ezdxf
import pytest

from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_api_sketch import XY, line
from tests.test_p4_parts import CENT, machining_master  # noqa: F401 - fixture

T, R, K, L = 2.0, 2.0, 0.5, 30.0


def post(client, w, ftype, params):
    r = client.post(
        f"/api/documents/{w.doc}/features",
        json={"feature_type": ftype, "params": params},
        headers=w.h["designer"],
    )
    return r


def bracket(client, w, flanges=((0, 90.0, True),)):
    ents = [
        line(1, (0, 0), (100, 0)),
        line(2, (100, 0), (100, 50)),
        line(3, (100, 50), (0, 50)),
        line(4, (0, 50), (0, 0)),
        {"id": 5, "type": "CIRCLE", "center": [20, 25], "radius": 3},
    ]
    sk = post(client, w, "SKETCH", {"plane": XY, "entities": ents, "constraints": []})
    assert sk.status_code == 200, sk.text
    base = post(
        client,
        w,
        "SHEET_BASE",
        {
            "sketch_feature_id": sk.json()["data"]["feature_id"],
            "thickness": T,
            "bend_radius": R,
            "k_factor": K,
        },
    )
    assert base.status_code == 200, base.text
    last = base.json()["data"]
    for edge, angle, up in flanges:
        r = post(
            client,
            w,
            "SHEET_FLANGE",
            {
                "target_feature_id": last["feature_id"],
                "edge": edge,
                "length": L,
                "angle": angle,
                "up": up,
            },
        )
        assert r.status_code == 200, r.text
        last = r.json()["data"]
    return last


def test_sheet_features_and_flat(client, world):
    h = world.h["designer"]
    f = bracket(client, world, ((0, 90.0, True), (2, 90.0, True)))  # a U channel
    ba = math.pi / 2 * (R + K * T)
    m = f["metrics"]
    assert m["sheet"]["bend_count"] == 2 and m["sheet"]["pierce_count"] == 1
    flat_area = 100 * (50 + 2 * (ba + L)) - math.pi * 9
    assert m["sheet"]["blank_area_mm2"] == pytest.approx(flat_area, rel=1e-9)
    assert m["volume_mm3"] == pytest.approx(flat_area * T, rel=1e-6)  # K = 0.5
    r = client.get(f"/api/features/{f['feature_id']}/sheet", headers=h).json()["data"]
    assert [e["index"] for e in r["edges"]] == [0, 1, 2, 3] and r["edges"][0][
        "length"
    ] == pytest.approx(100)
    assert [b["edge"] for b in r["flat"]["bends"]] == [0, 2]
    d = client.get(f"/api/features/{f['feature_id']}/sheet?format=dxf", headers=h)
    assert d.status_code == 200 and "flat.dxf" in d.headers["content-disposition"]
    doc = ezdxf.read(io.StringIO(d.content.decode()))
    assert (
        len([e for e in doc.modelspace() if e.dxftype() == "LINE" and e.dxf.layer == "BEND"]) == 2
    )
    # the tree: base -> flange -> flange, each consuming the one before
    items = client.get(f"/api/documents/{world.doc}/features", headers=h).json()["data"]["items"]
    assert [i["feature_type"] for i in items] == [
        "SKETCH",
        "SHEET_BASE",
        "SHEET_FLANGE",
        "SHEET_FLANGE",
    ]
    # suppressing the first flange leaves a one-flange sheet in the last feature
    first = items[2]["feature_id"]
    assert (
        client.post(
            f"/api/features/{first}/suppress", json={"suppressed": True}, headers=h
        ).status_code
        == 200
    )
    last = client.get(f"/api/documents/{world.doc}/features", headers=h).json()["data"]["items"][3]
    assert last["metrics"]["sheet"]["bend_count"] == 1


def test_sheet_validation(client, world):
    h = world.h["designer"]
    f = bracket(client, world)
    r = post(
        client,
        world,
        "SHEET_FLANGE",
        {"target_feature_id": f["feature_id"], "edge": 0, "length": 10},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "SHEET_INVALID"  # edge 0 taken
    r = post(
        client,
        world,
        "SHEET_FLANGE",
        {"target_feature_id": f["feature_id"], "edge": 1, "length": 10, "angle": 271},
    )
    assert r.status_code == 422
    items = client.get(f"/api/documents/{world.doc}/features", headers=h).json()["data"]["items"]
    sketch = items[0]["feature_id"]
    r = client.get(f"/api/features/{sketch}/sheet", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_SHEET"
    # a flange only goes on sheet metal
    box = post(
        client, world, "PRIMITIVE", {"kind": "BOX", "length": 10, "width": 10, "height": 10}
    ).json()["data"]
    r = post(
        client,
        world,
        "SHEET_FLANGE",
        {"target_feature_id": box["feature_id"], "edge": 0, "length": 10},
    )
    assert r.status_code == 422


def test_sheet_quote_matches_hand_reference(client, world, estimator, machining_master):  # noqa: F811
    f = bracket(client, world)
    qty = 2
    q = client.post(
        "/api/quotes",
        json={"document_id": world.doc, "qty": qty, "material_code": "SS400"},
        headers=estimator,
    )
    assert q.status_code == 200, q.text
    q = q.json()["data"]
    ba = D(str(math.pi / 2 * (R + K * T)))
    depth = D(50) + ba + D(30)
    stock = (D(100) + 4) * (depth + 4) * D(2)  # flat blank box + 2 mm each side, x thickness
    cut = 2 * (D(100) + depth) + D(str(2 * math.pi * 3))
    lines = {"SS400 소재": stock * D("7.85") / D(1_000_000) * D("1.05") * qty * D("1200.50")}

    def hours(name, minutes, machine):
        hh = minutes / 60 * qty
        lines[f"{name} 노무"] = hh * 35000
        lines[f"{name} 기계경비"] = hh * machine

    hours("레이저 절단", D(10) / qty + cut / 3000 + D("1.5") / 60, 60000)
    hours("절곡", D(15) / qty + D("0.5"), 25000)
    lines = {k: v.quantize(CENT, rounding=ROUND_HALF_UP) for k, v in lines.items()}
    got = {ln["item_name"]: D(ln["calculated_amount"]) for ln in q["lines"]}
    for k, v in lines.items():
        assert got[k] == pytest.approx(v, abs=D("0.02")), k  # cut length from the kernel vs pi
    assert "밀링 노무" not in got  # a sheet is not milled
    made = sum(lines.values(), D(0))
    total = made + made * D("0.06")
    supply = ((total * D("1.1")).quantize(D("0.0001"), rounding=ROUND_HALF_UP) / 10).quantize(
        D(1), rounding=ROUND_FLOOR
    ) * 10
    assert abs(D(q["supply_amount"]) - supply) <= 10
    assert not any(lg["severity"] == "ERROR" for lg in q["logs"])
    for ln in q["lines"]:
        (t,) = ln["traces"]
        assert t["source_kind"] == "FEATURE" and t["sources"] == [str(f["feature_id"])]


def test_copilot_sheet_checks_and_command(client, world):
    h = world.h["designer"]
    f = bracket(client, world)
    base = client.get(f"/api/documents/{world.doc}/features", headers=h).json()["data"]["items"][1]
    # a short second flange right next to the first, and a hole close to its bend line
    r = post(
        client,
        world,
        "SHEET_FLANGE",
        {"target_feature_id": f["feature_id"], "edge": 1, "length": 3},
    )
    assert r.status_code == 200, r.text
    codes = {
        x["code"]
        for x in client.get(f"/api/copilot/findings?document_id={world.doc}", headers=h).json()[
            "data"
        ]["findings"]
    }
    assert {"SHEET_FLANGE_SHORT", "SHEET_FLANGE_OVERLAP"} <= codes
    assert base["feature_type"] == "SHEET_BASE"
    a = client.post(
        "/api/copilot/ask",
        json={"message": "두께 1.6 판금 만들어줘", "context": {"page": "model3d"}},
        headers=h,
    ).json()["data"]
    assert a["actions"][0] == {
        **a["actions"][0],
        "kind": "dialog",
        "dialog": "SHEET",
        "values": {"thickness": 1.6},
    }


def test_bend_table_gives_radius_and_k_when_the_feature_leaves_them_out(client, world):
    """plan §23 S1: master data 16_판금절곡 — K-factor row, BD90 row, no row (defaults), and a
    feature's own values winning over the table."""
    from tests.test_api_master import FULL

    admin, h = world.h["admin"], world.h["designer"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "BEND1", "effective_from": "2020-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    bends = [
        {
            "material_code": "SPCC",
            "thickness_mm": "1.6",
            "inner_radius_mm": "2.4",
            "k_factor": "0.38",
        },
        {"material_code": "SUS304", "thickness_mm": "2", "inner_radius_mm": "2", "bd90_mm": "3.6"},
    ]
    r = client.put(f"/api/master-versions/{vid}", json=FULL | {"sheet_bends": bends}, headers=admin)
    assert r.status_code == 200, r.text
    bad = FULL | {
        "sheet_bends": [{"material_code": "X", "thickness_mm": "1", "inner_radius_mm": "1"}]
    }
    assert client.put(f"/api/master-versions/{vid}", json=bad, headers=admin).status_code == 422
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200

    def sheet(**p):
        ents = [line(1, (0, 0), (100, 0)), line(2, (100, 0), (100, 50))]
        ents += [line(3, (100, 50), (0, 50)), line(4, (0, 50), (0, 0))]
        sk = post(client, world, "SKETCH", {"plane": XY, "entities": ents, "constraints": []})
        sid = sk.json()["data"]["feature_id"]  # a sketch feeds one sheet
        f = post(client, world, "SHEET_BASE", {"sketch_feature_id": sid, **p})
        assert f.status_code == 200, f.text
        fid = f.json()["data"]["feature_id"]
        return client.get(f"/api/features/{fid}/sheet", headers=h).json()["data"]

    s = sheet(thickness=1.6, material_code="SPCC")
    assert (s["bend_radius"], s["k_factor"], s["bend_source"]) == (2.4, 0.38, "TABLE:BEND1")
    s = sheet(thickness=2, material_code="SUS304")  # BA90 = 2 (R + T) - BD90 = 4.4 = pi/2 (R + K T)
    assert s["bend_radius"] == 2 and s["bend_source"] == "TABLE:BEND1"
    assert math.pi / 2 * (2 + s["k_factor"] * 2) == pytest.approx(2 * (2 + 2) - 3.6)
    s = sheet(thickness=3, material_code="SPCC")  # no row for t3: R = T, K = 0.44
    assert (s["bend_radius"], s["k_factor"], s["bend_source"]) == (3, 0.44, "DEFAULT")
    s = sheet(thickness=1.6, material_code="SPCC", k_factor=0.5)  # the feature's K wins
    assert (s["bend_radius"], s["k_factor"]) == (2.4, 0.5)


def test_flange_chain_and_side_flange(client, world):
    """plan §23 S2: a Z flange (two bends in a row), a hem with its own radius and a flange on the
    side of an earlier flange, through the API."""
    h = world.h["designer"]
    first = bracket(client, world)  # edge 0: up 90, L = 30
    z = post(
        client,
        world,
        "SHEET_FLANGE",
        {
            "target_feature_id": first["feature_id"],
            "edge": 2,
            "length": 20,
            "steps": [{"angle": 90, "up": False, "length": 15}],
        },
    )
    assert z.status_code == 200, z.text
    side = post(
        client,
        world,
        "SHEET_FLANGE",
        {
            "target_feature_id": z.json()["data"]["feature_id"],
            "parent_feature_id": first["feature_id"],
            "parent_side": "RIGHT",
            "length": 10,
            "radius": 1.0,
            "style": "HEM",
            "angle": 180,
        },
    )
    assert side.status_code == 200, side.text
    f = side.json()["data"]
    ba = lambda deg, r=R: math.radians(deg) * (r + K * T)
    area = 100 * 50 - math.pi * 9 + 100 * (ba(90) + L) + 100 * (2 * ba(90) + 35)
    area += L * (ba(180, 1.0) + 10)
    assert f["metrics"]["sheet"]["bend_count"] == 4
    assert f["metrics"]["sheet"]["blank_area_mm2"] == pytest.approx(area, rel=1e-9)
    assert f["metrics"]["volume_mm3"] == pytest.approx(area * T, rel=1e-6)
    r = client.get(f"/api/features/{f['feature_id']}/sheet", headers=h).json()["data"]
    assert r["flanges"][0]["feature_id"] == first["feature_id"]
    assert r["flanges"][2]["parent"] == {"flange": 0, "step": 0, "side": "RIGHT"}
    assert [(b["flange"], b["step"], b["style"]) for b in r["flat"]["bends"]] == [
        (0, 0, "FLANGE"),
        (1, 0, "FLANGE"),
        (1, 1, "FLANGE"),
        (2, 0, "HEM"),
    ]
    # an edge and a parent together, a side without a parent, a parent from another sheet: refused
    for bad in (
        {"edge": 1, "parent_feature_id": first["feature_id"], "parent_side": "LEFT"},
        {"parent_side": "LEFT"},
        {"parent_feature_id": 999_999, "parent_side": "LEFT"},
    ):
        r = post(
            client,
            world,
            "SHEET_FLANGE",
            {"target_feature_id": f["feature_id"], "length": 5, **bad},
        )
        assert r.status_code in (400, 409, 422), r.text
    # suppressing the parent is refused: the flange on its side could no longer be built
    r = client.post(
        f"/api/features/{first['feature_id']}/suppress", json={"suppressed": True}, headers=h
    )
    assert r.status_code == 422 and r.json()["error"]["details"]["feature_id"] == f["feature_id"]
