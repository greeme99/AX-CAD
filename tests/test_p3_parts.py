"""Track 8 P3-5 acceptance: production drawings of the five P2 synthetic parts. Each gets a DXF
revision (three views + isometric) and a PDF: the overall dimensions match the model's bounding box
to 1e-6, parts with holes show hidden lines and one callout per hole group, and ezdxf's audit finds
no errors."""

import io

import ezdxf
import pytest
from pypdf import PdfReader

from tests.test_drawing import main_var
from tests.test_p2_parts import bracket, flange, housing, plate, shaft

PARTS = {"bracket": bracket, "flange": flange, "shaft": shaft, "housing": housing, "plate": plate}
HOLE_GROUPS = {"bracket": 1, "flange": 1, "shaft": 0, "housing": 1, "plate": 1}


@pytest.mark.parametrize("name", PARTS)
def test_production_drawing(client, world, name):
    f, _ = PARTS[name](client, world)
    size = f["metrics"]["bbox"]["size"]
    dz = world.h["designer"]
    body = {"material": "SS400", "roughness": 3.2}
    r = client.post(f"/api/documents/{world.doc}/drawing", json=body, headers=dz)
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["revision_id"]
    doc = ezdxf.read(io.StringIO((main_var(client) / "uploads" / f"{rid}.dxf").read_text()))
    assert not doc.audit().has_errors
    msp = doc.modelspace()
    measured = [d.get_measurement() for d in msp.query("DIMENSION")]
    for want in size:  # width, depth and height each appear as a dimension
        assert any(abs(m - want) <= 1e-6 for m in measured), (want, measured)
    assert len(msp.query("MULTILEADER")) == HOLE_GROUPS[name]
    if HOLE_GROUPS[name]:
        assert len(msp.query("*[layer=='AXGEN_HIDDEN']")) > 0
    pdf = client.post(f"/api/revisions/{rid}/pdf", json={"paper": "A3"}, headers=dz)
    assert pdf.status_code == 200, pdf.text
    text = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "SS400" in text and "제3각법" in text
