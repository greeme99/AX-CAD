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
        {"target_feature_id": f["feature_id"], "edge": 1, "length": 10, "angle": 180},
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
