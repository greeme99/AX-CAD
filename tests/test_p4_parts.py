"""Track 8 P4 acceptance: the five synthetic standard parts (P2-6) quoted from 3D with the default
machining rules, each against a reference quote worked out by hand from the part's design (its
analytic volume, bounding box, turning axis and holes, not the kernel's metrics) and matching to
the won; every line traces to the part's features."""

from decimal import ROUND_FLOOR, ROUND_HALF_UP
from decimal import Decimal as D

import pytest

from tests.test_api_master import FULL
from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_p2_parts import bracket, flange, housing, plate, shaft

PI = D("3.14159265358979323846")
CENT = D("0.01")
RATIOS = FULL["cost_ratios"] | {
    "material_basis": "BBOX",
    "stock_allowance_mm": "2",
    "vat_rate": "0.1",
}
# part -> (bounding box mm, turning axis, holes: (count, drilled length mm, taps, tapped length mm))
DESIGN = {
    bracket: ((60, 30, 40), None, (2, 2 * 5, 0, 0)),  # through the 5 mm leg
    flange: ((100, 100, 30), "Z", (6, 6 * 10, 0, 0)),  # through the 10 mm disc
    shaft: ((130, 30, 30), "X", None),
    housing: ((80, 60, 40), None, (4, 4 * 6, 4, 4 * 6)),  # M3 6 deep, threaded full depth
    plate: ((100, 50, 2), None, (5, 5 * 2, 0, 0)),
}
QTY = 2


def reference(volume, size, axis, holes, qty=QTY):
    """The quote as an estimator would work it out on paper (P4-2 default rules, SS400)."""
    a = D(2)
    size = [D(v) for v in size]
    if axis:  # round bar: larger cross size + 2a, axial size + 2a
        i = "XYZ".index(axis)
        dia = max(v for j, v in enumerate(size) if j != i) + 2 * a
        stock = PI * dia * dia / 4 * (size[i] + 2 * a)
    else:
        stock = (size[0] + 2 * a) * (size[1] + 2 * a) * (size[2] + 2 * a)
    lines = {"SS400 소재": stock * D("7.85") / D(1_000_000) * D("1.05") * qty * D("1200.50")}

    def hours(name, minutes, machine):
        h = minutes / 60 * qty
        lines[f"{name} 노무"] = h * 35000
        lines[f"{name} 기계경비"] = h * machine

    removed = stock - D(str(volume))
    if axis:
        hours("선반", D(20) / qty + removed / 18000, 30000)
    else:
        hours("밀링", D(30) / qty + removed / 12000, 40000)
    if holes:
        n, drilled, taps, tapped = (D(v) for v in holes)
        hours("드릴", D(3) / qty + n * D("0.2") + drilled / 80, 15000)
        if taps:
            hours("탭", taps * D("0.3") + tapped * 2 / 200, 15000)
    lines = {k: v.quantize(CENT, rounding=ROUND_HALF_UP) for k, v in lines.items()}
    made = sum(lines.values(), D(0))
    total = made + made * D("0.06")

    def floor10(v):
        return (v.quantize(D("0.0001"), rounding=ROUND_HALF_UP) / 10).quantize(
            D(1), rounding=ROUND_FLOOR
        ) * 10

    supply = floor10(total + total * D("0.1"))
    return lines, supply, supply + floor10(supply * D("0.1"))


@pytest.fixture
def machining_master(client, world):
    admin = world.h["admin"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "P4REF", "effective_from": "2020-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    r = client.put(
        f"/api/master-versions/{vid}", json=FULL | {"cost_ratios": RATIOS}, headers=admin
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/master-versions/{vid}/machining-defaults", headers=admin)
    assert r.status_code == 200, r.text
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200


@pytest.mark.parametrize("part", list(DESIGN), ids=lambda p: p.__name__)
def test_reference_quote(client, world, estimator, machining_master, part):  # noqa: F811
    f, volume = part(client, world)
    size, axis, holes = DESIGN[part]
    body = client.get(f"/api/documents/{world.doc}/metrics3d", headers=estimator).json()["data"]
    assert [b["turn_axis"] for b in body["bodies"]] == [axis]
    q = client.post(
        "/api/quotes",
        json={"document_id": world.doc, "qty": QTY, "material_code": "SS400"},
        headers=estimator,
    )
    assert q.status_code == 200, q.text
    q = q.json()["data"]
    lines, supply, total = reference(volume, size, axis, holes)
    got = {ln["item_name"]: D(ln["calculated_amount"]) for ln in q["lines"]}
    assert got == lines
    assert D(q["supply_amount"]) == supply and D(q["total_amount"]) == total
    assert not any(lg["severity"] == "ERROR" for lg in q["logs"])
    # traces: the body for material and stock removal, the hole feature for hole work
    for ln in q["lines"]:
        (t,) = ln["traces"]
        assert t["source_kind"] == "FEATURE" and t["sources"] == [str(f["feature_id"])]
