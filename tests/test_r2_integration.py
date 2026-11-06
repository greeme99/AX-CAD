"""Track 9 Q5: the R2.0 pieces together, driven from outside through the SDK (TC-180).

A sheet metal bracket (Q3) is placed three times in an assembly (Q2): two copies on top of each
other clash, the third stands clear; the assembly BOM counts three brackets. The bracket is
quoted with laser and bending lines traced to its sheet feature, Copilot (Q1) checks the
assembly, the sheet part and the quote, and a parametric block (Q4) comes from the same client.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "sdk" / "examples"))

import sheet_flat
from axcad import Client

from tests.test_p4_parts import machining_master  # noqa: F401 - fixture
from tests.test_sdk import transport  # noqa: F401 - fixture


def test_r2_end_to_end(client, world, transport, tmp_path, make_user, machining_master):  # noqa: F811
    from tests.conftest import PASSWORD

    uid = make_user("r2user", "DESIGNER", "ESTIMATOR")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": uid}, headers=world.h["admin"]
    )
    cad = Client(
        "http://localhost",
        token=Client.login_token("http://localhost", "r2user", PASSWORD, transport=transport),
        transport=transport,
    )

    # Q3: sheet metal bracket and its flat pattern
    metrics = sheet_flat.main(cad, world.pid, tmp_path / "flat.dxf")
    assert metrics["bend_count"] == 1
    part = next(
        d
        for d in cad.get(f"/api/projects/{world.pid}/documents")["items"]
        if d["doc_no"] == "BRK-SDK"
    )
    flange = cad.features(part["document_id"])[-1]
    assert flange["feature_type"] == "SHEET_FLANGE" and flange["status"] == "OK"

    # Q2: three instances of the bracket, two of them in the same place
    asm = cad.create_document(world.pid, "ASM-R2", "ASSEMBLY", "R2 조립")
    a = cad.add_component(asm["document_id"], flange["feature_id"])
    cad.add_component(asm["document_id"], flange["feature_id"])
    cad.add_component(asm["document_id"], flange["feature_id"], t=(300, 0, 0))
    res = cad.interference(asm["document_id"])
    assert len(res["clashes"]) == 1 and res["clashes"][0]["volume_mm3"] > 0
    bom = cad.post(f"/api/documents/{asm['document_id']}/boms", {"source": "MODEL"})
    assert bom["source_type"] == "ASSEMBLY" and [i["qty"] for i in bom["items"]] == [3]

    # Q1: Copilot on the assembly (no mates yet) and on the sheet part
    def codes(**q):
        key, val = next(iter(q.items()))
        return {f["code"] for f in cad.get(f"/api/copilot/findings?{key}={val}")["findings"]}

    assert "ASM_NO_MATES" in codes(document_id=asm["document_id"])
    assert a["fixed"] is True
    codes(document_id=part["document_id"])  # a valid bracket: the sheet checks run without error
    ans = cad.post(
        "/api/copilot/ask",
        {
            "context": {"page": "model3d", "document_id": part["document_id"]},
            "message": "두께 2 판금",
        },
    )
    assert ans["source"] == "RULE" and ans["actions"]

    # quote of the bracket: laser and bending, every line traced to the sheet feature
    q = cad.quote_model(part["document_id"], qty=3, material_code="SS400")
    names = {ln["item_name"] for ln in q["lines"]}
    assert {"레이저 절단 노무", "절곡 노무"} <= names and "밀링 노무" not in names
    assert all(
        t["source_kind"] == "FEATURE" and t["sources"] == [str(flange["feature_id"])]
        for ln in q["lines"]
        for t in ln["traces"]
    )
    codes(quote_id=q["quote_id"])
    assert cad.quote_report(q["quote_id"], "xlsx")[:2] == b"PK"

    # Q4: a parametric block from the same client
    blk = cad.param_block("BOLT_CIRCLE", {"N": 8, "PCD": 120}, "관통")
    assert blk["name"].startswith("PB-BOLT_CIRCLE-") and len(blk["geoms"]) >= 8


@pytest.mark.parametrize(
    "page", ["home", "project", "document", "cad2d", "model3d", "quote", "admin"]
)
def test_copilot_help_on_every_screen(client, world, page):
    r = client.get(f"/api/copilot/help?page={page}", headers=world.h["designer"])
    assert r.status_code == 200 and r.json()["data"]["topics"]
