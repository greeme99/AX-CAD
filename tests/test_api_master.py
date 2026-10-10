"""S8 FN-16 master data: versioned bundles, completeness gate, freeze, CHECKs (TC-65, TC-66)."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.db.session import engine

FULL = {
    "materials": [
        {
            "material_code": "SS400",
            "density_g_cm3": "7.85",
            "unit_price_per_kg": "1200.50",
            "scrap_rate": "0.05",
        }
    ],
    "price_items": [
        {"item_code": "LASER-LAB", "item_type": "LABOR", "unit": "h", "unit_price": "35000"},
        {"item_code": "LASER-MC", "item_type": "MACHINE", "unit": "h", "unit_price": "60000"},
    ],
    "process_rules": [
        {
            "rule_code": "LASER-SS",
            "process_code": "LASER",
            "input_metric": "cutting_length_mm",
            "formula_text": "setup_min + cutting_length_mm / speed_mm_per_min",
            "params": {"setup_min": 5, "speed_mm_per_min": 3000},
            "labor_item_code": "LASER-LAB",
            "machine_item_code": "LASER-MC",
        }
    ],
    "cost_ratios": {
        "overhead_basis": "MACHINE_HOUR",
        "admin_rate": "0.06",
        "profit_rate": "0.1",
        "rounding_rule": "FLOOR",
        "rounding_unit": 10,
        "rounding_scope": "TOTAL",
    },
    "mapping_rules": [
        {"rule_type": "LAYER", "target": "BEND", "pattern": "BEND*"},
        {"rule_type": "PUNCH_MAX_DIA", "target": "HOLE", "pattern": "6"},
    ],
}


@pytest.fixture
def admin(world):
    return world.h["admin"]


def create(client, h, code="V2027-01", **kw):
    r = client.post(
        "/api/master-versions",
        json={"version_code": code, "effective_from": "2027-01-01", **kw},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["version_id"]


def test_lifecycle_and_freeze(client, world, admin):
    vid = create(client, admin)
    r = client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    assert r.status_code == 200, r.text
    b = r.json()["data"]
    assert b["status"] == "DRAFT" and b["materials"][0]["unit_price_per_kg"] == "1200.50"
    assert b["cost_ratios"]["vat_rate"] == "0.100000"  # default, Decimal kept as string
    r = client.post(f"/api/master-versions/{vid}/activate", headers=admin)
    assert r.status_code == 200 and r.json()["data"]["status"] == "ACTIVE"

    # TC-65: an active bundle never changes; the change is a new version copied from it
    r = client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MASTER_FROZEN"
    assert client.delete(f"/api/master-versions/{vid}", headers=admin).status_code == 409
    v2 = create(client, admin, "V2027-02", copy_from=vid)
    changed = FULL | {"materials": [FULL["materials"][0] | {"unit_price_per_kg": "1300"}]}
    assert client.put(f"/api/master-versions/{v2}", json=changed, headers=admin).status_code == 200
    old = client.get(f"/api/master-versions/{vid}", headers=world.h["admin"]).json()["data"]
    assert old["materials"][0]["unit_price_per_kg"] == "1200.50"
    copy = client.get(f"/api/master-versions/{v2}", headers=admin).json()["data"]
    assert copy["process_rules"][0]["params"] == {"setup_min": 5, "speed_mm_per_min": 3000}

    # the DB itself refuses edits of an active bundle (not only the API)
    with pytest.raises(DBAPIError), engine().begin() as conn:
        conn.execute(
            text("UPDATE materials SET unit_price_per_kg = 1 WHERE version_id = :v"), {"v": vid}
        )

    for sql in (
        "UPDATE master_versions SET status = 'DRAFT' WHERE version_id = :v",
        "DELETE FROM master_versions WHERE version_id = :v",
    ):
        with pytest.raises(DBAPIError), engine().begin() as conn:
            conn.execute(text(sql), {"v": vid})
    r = client.get("/api/master-versions/current", params={"on": "2027-06-01"}, headers=admin)
    assert r.json()["data"]["version_id"] == vid  # v2 is still a draft
    r = client.get("/api/master-versions/current", params={"on": "2026-12-31"}, headers=admin)
    assert r.status_code == 404


def test_activation_requires_complete_prices(client, admin):
    vid = create(client, admin)
    partial = FULL | {
        "materials": [{"material_code": "SUS304", "density_g_cm3": "7.93"}],
        "process_rules": [FULL["process_rules"][0] | {"labor_item_code": "NOPE"}],
    }
    assert client.put(f"/api/master-versions/{vid}", json=partial, headers=admin).status_code == 200
    r = client.post(f"/api/master-versions/{vid}/activate", headers=admin)
    msg = r.json()["error"]["message"]
    assert r.status_code == 422 and r.json()["error"]["code"] == "MASTER_INCOMPLETE"
    assert (
        "materials.SUS304.unit_price_per_kg" in msg
        and "process_rules.LASER-SS.labor_item_code" in msg
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"cost_ratios": FULL["cost_ratios"] | {"profit_rate": "1.2"}},  # TC-66: 120 %
        {"cost_ratios": FULL["cost_ratios"] | {"admin_rate": "-0.01"}},
        {"cost_ratios": FULL["cost_ratios"] | {"rounding_unit": 5}},
        {"materials": [FULL["materials"][0] | {"density_g_cm3": "0"}]},
        {"materials": [FULL["materials"][0] | {"unit_price_per_kg": "NaN"}]},
        {"materials": [FULL["materials"][0] | {"thickness_min_mm": "3", "thickness_max_mm": "1"}]},
        {"process_rules": [FULL["process_rules"][0] | {"input_metric": "price"}]},
        {"process_rules": [FULL["process_rules"][0] | {"formula_text": "__import__('os')"}]},
        {"process_rules": [FULL["process_rules"][0] | {"formula_text": "setup_min + unknown"}]},
        {"cost_ratios": FULL["cost_ratios"] | {"material_basis": "GROSS"}},
        {
            "process_rules": [
                FULL["process_rules"][0]
                | {"params": {"setup_min": 5, "speed_mm_per_min": 3000, "cutting_length_mm": 1}}
            ]
        },
        {
            "process_rules": [
                FULL["process_rules"][0]
                | {"params": {"setup_min": 1e300, "speed_mm_per_min": 3000}}
            ]
        },
        {"mapping_rules": [{"rule_type": "LAYER", "target": "DELETE", "pattern": "*"}]},
        {"mapping_rules": [{"rule_type": "PUNCH_MAX_DIA", "target": "HOLE", "pattern": "x"}]},
    ],
)
def test_tc66_invalid_values_rejected(client, admin, patch):
    vid = create(client, admin)
    r = client.put(f"/api/master-versions/{vid}", json=FULL | patch, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_INVALID"


def test_tc66_db_check_constraint(client, admin):
    vid = create(client, admin)
    client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    with pytest.raises(DBAPIError), engine().begin() as conn:
        conn.execute(
            text("UPDATE cost_ratios SET profit_rate = 1.2 WHERE version_id = :v"), {"v": vid}
        )


def test_rbac_and_audit(client, world, admin, make_user, headers):
    make_user("estimator", "ESTIMATOR")
    est = headers("estimator")
    vid = create(client, admin)
    assert client.get(f"/api/master-versions/{vid}", headers=est).status_code == 200
    assert client.get("/api/master-versions", headers=world.h["designer"]).status_code == 403
    new = {"version_code": "X1", "effective_from": "2027-01-01"}
    for method, url, payload in (
        ("post", "/api/master-versions", new),
        ("put", f"/api/master-versions/{vid}", FULL),
        ("post", f"/api/master-versions/{vid}/activate", None),
        ("delete", f"/api/master-versions/{vid}", None),
    ):
        kw = {"json": payload} if payload is not None else {}
        assert getattr(client, method)(url, headers=est, **kw).status_code == 403, url
    client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    r = client.get(
        f"/api/audit-logs?object_type=materials&object_id={vid}", headers=world.h["reviewer"]
    )
    items = r.json()["data"]["items"]
    assert items and items[0]["action"] == "INSERT" and items[0]["user_id"] == world.ids["admin"]


def test_g1_workbook_import(client, world, admin, tmp_path, make_user, headers):
    from tests.test_g1 import fill

    vid = create(client, admin)
    url = f"/api/master-versions/{vid}/import-xlsx"
    xlsx = Path(fill(tmp_path)).read_bytes()
    r = client.post(url, files={"file": ("g1.xlsx", xlsx)}, headers=admin)
    assert r.status_code == 200, r.text
    b = r.json()["data"]
    assert [m["material_code"] for m in b["materials"]] == ["SS400", "SUS304"]
    assert b["cost_ratios"]["rounding_scope"] == "TOTAL" and b["warnings"]
    # complete enough to become the active version
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200
    r = client.post(url, files={"file": ("g1.xlsx", xlsx)}, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MASTER_FROZEN"

    v2 = create(client, admin, "V2027-02")
    url2 = f"/api/master-versions/{v2}/import-xlsx"
    make_user("estimator", "ESTIMATOR")
    assert (
        client.post(
            url2, files={"file": ("g1.xlsx", xlsx)}, headers=headers("estimator")
        ).status_code
        == 403
    )
    for name, data in (("g1.csv", xlsx), ("g1.xlsx", b"not a zip")):
        r = client.post(url2, files={"file": (name, data)}, headers=admin)
        assert r.status_code == 422 and r.json()["error"]["code"] == "G1_INVALID", name
    # parser passes, schema validation fails: reported per sheet/item, nothing written
    bad = fill(tmp_path, {"1_재질": [("SS 400", None, 7.85, 1, 1, "전체", None)]}, {}, "bad.xlsx")
    r = client.post(url2, files={"file": ("bad.xlsx", Path(bad).read_bytes())}, headers=admin)
    assert (
        r.status_code == 422 and "1_재질 1번째 항목 material_code" in r.json()["error"]["message"]
    )
    assert client.get(f"/api/master-versions/{v2}", headers=admin).json()["data"]["materials"] == []


def test_g1_template_download(client, world, admin):
    r = client.get("/api/master-data/g1-template.xlsx", headers=admin)
    assert r.status_code == 200 and r.content.startswith(b"PK\x03\x04")
    assert (
        client.get("/api/master-data/g1-template.xlsx", headers=world.h["designer"]).status_code
        == 403
    )
