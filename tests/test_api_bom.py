"""S12 FN-23 BOM API: TC-90 (qty = expanded instances), TC-91 (manual mapping), TC-92 (export)."""

import csv
import io
import json

import pytest
from sqlalchemy import text

from backend.db.session import engine
from tests.test_api_model import box_and_hole
from tests.test_bom import assembly


def upload_assembly(client, w, tmp_path):
    with open(assembly(tmp_path), "rb") as f:
        r = client.post(
            f"/api/documents/{w.doc}/dxf",
            files={"file": ("asm.dxf", f.read())},
            headers=w.h["designer"],
        )
    assert r.status_code == 200, r.text
    return r.json()["data"]["revision_id"]


def test_tc90_92_bom_from_revision(client, world, tmp_path, make_user, headers):
    rid = upload_assembly(client, world, tmp_path)
    r = client.post(
        f"/api/documents/{world.doc}/boms", json={"source": "REVISION"}, headers=world.h["designer"]
    )
    assert r.status_code == 200, r.text
    b = r.json()["data"]
    assert b["revision_id"] == rid and b["source_type"] == "DXF_BLOCK"
    assert b["bom_no"].endswith(f"-{b['bom_id']:06d}")
    by = {i["source_name"]: i for i in b["items"]}
    assert (by["BRACKET"]["qty"], by["BOLT"]["qty"], by["NUT"]["qty"]) == (8, 34, 1)  # TC-90
    assert b["unmapped"] == 1 and by["NUT"]["mapping_status"] == "UNMAPPED"

    # TC-91: the unlabelled block gets a part number by hand; formula-like input is refused
    url = f"/api/bom-items/{by['NUT']['bom_item_id']}"
    bad = client.patch(url, json={"part_no": "=cmd|' /C calc'!A0"}, headers=world.h["designer"])
    assert bad.status_code == 400
    r = client.patch(
        url, json={"part_no": "N-M6", "part_name": "=SUM(1+1)"}, headers=world.h["designer"]
    )
    nut = next(i for i in r.json()["data"]["items"] if i["source_name"] == "NUT")
    assert nut["mapping_status"] == "MANUAL" and nut["mapped_by"] == world.ids["designer"]
    assert r.json()["data"]["unmapped"] == 0

    # TC-92: CSV/JSON carry the same rows; a formula-like cell is defused in CSV
    exp = f"/api/boms/{b['bom_id']}/export"
    c = client.get(exp, headers=world.h["designer"])
    assert c.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(c.content.decode("utf-8-sig"))))
    assert {r["source_name"]: int(r["qty"]) for r in rows} == {"BRACKET": 8, "BOLT": 34, "NUT": 1}
    assert next(r for r in rows if r["source_name"] == "NUT")["part_name"] == "'=SUM(1+1)"
    j = json.loads(client.get(f"{exp}?format=json", headers=world.h["designer"]).content)
    assert j["bom_no"] == b["bom_no"] and len(j["items"]) == 3
    assert set(j["items"][0]) == {
        "item_no",
        "part_no",
        "part_name",
        "qty",
        "unit",
        "level",
        "mapping_status",
        "source_name",
    }
    assert [x["bom_id"] for x in client.get(
        f"/api/documents/{world.doc}/boms", headers=world.h["designer"]
    ).json()["data"]["items"]] == [b["bom_id"]]  # fmt: skip

    # access: estimator has no BOM role, outsiders see nothing
    est = make_user("est", "ESTIMATOR")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": est}, headers=world.h["admin"]
    )
    assert client.get(f"/api/boms/{b['bom_id']}", headers=headers("est")).status_code == 403
    make_user("outsider", "DESIGNER")
    assert client.get(f"/api/boms/{b['bom_id']}", headers=headers("outsider")).status_code == 404
    assert client.patch(url, json={"part_no": "X"}, headers=headers("outsider")).status_code == 404

    # extracted quantities are write-once
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), engine().begin() as conn:
        conn.execute(text("UPDATE bom_items SET qty = 1"))


def test_bom_from_3d_model(client, world):
    box_and_hole(client, world)
    r = client.post(
        f"/api/documents/{world.doc}/boms", json={"source": "MODEL"}, headers=world.h["designer"]
    )
    assert r.status_code == 200, r.text
    b = r.json()["data"]
    assert b["source_type"] == "STEP_ASSEMBLY" and b["revision_id"] is None
    assert all(i["qty"] >= 1 and i["mapping_status"] == "UNMAPPED" for i in b["items"])


def test_empty_and_missing_sources(client, world, dxf):
    r = client.post(
        f"/api/documents/{world.doc}/boms", json={"source": "REVISION"}, headers=world.h["designer"]
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_REVISION"
    client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("p.dxf", dxf(3))},
        headers=world.h["designer"],
    )
    r = client.post(
        f"/api/documents/{world.doc}/boms", json={"source": "REVISION"}, headers=world.h["designer"]
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "BOM_EMPTY"
