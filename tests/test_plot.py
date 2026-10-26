"""Track 8 P1-4: drawing PDF output (paper, scale, title block, layers); P3-4 (C-2): a
paper-space sheet printed as laid out."""

import io

import pytest
from pypdf import PdfReader

from core.dxf.plot import PT, fit_scale, plot_pdf, plot_sheet, scale_text
from core.dxf.reader import DxfError, parse_dxf
from tests.samples.drawings import write_all

TITLE = {
    "doc_no": "D-100",
    "title": "브래킷 조립도",
    "revision": "B",
    "date": "2026-10-25",
    "author": "홍길동",
}


@pytest.fixture(scope="module")
def annotations(tmp_path_factory):
    return parse_dxf(str(write_all(tmp_path_factory.mktemp("s"))["annotations"]))


def test_standard_scale_choice():
    assert fit_scale((100, 50), (380, 240)) == 2  # 2:1 still fits
    assert fit_scale((1000, 400), (380, 240)) == 1 / 5  # 1:2.5 is not standard: 1:5
    assert fit_scale((1e9, 1), (380, 240)) == 1 / 10000  # nothing fits: the smallest scale
    assert [scale_text(s) for s in (5, 1, 0.02)] == ["5:1", "1:1", "1:50"]


def text_of(pdf: bytes) -> str:
    return "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages)


def test_sheet_size_title_block_and_drawing_text(annotations):
    pdf, s = plot_pdf(annotations, "A3", True, None, TITLE, set())
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    assert (
        round(float(page.mediabox.width) / 72 * 25.4),
        round(float(page.mediabox.height) / 72 * 25.4),
    ) == (420, 297)
    text = text_of(pdf)
    for value in ("D-100", "브래킷 조립도", "홍길동", "A3", scale_text(s), "NOTE"):
        assert value in text
    portrait, _ = plot_pdf(annotations, "A4", False, 0.5, TITLE, set())
    assert "1:2" in text_of(portrait)


def test_hidden_layers_are_left_out(tmp_path):
    import ezdxf

    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    doc.layers.add("SECRET")
    doc.modelspace().add_text("KEEP", height=5)
    doc.modelspace().add_text("DROP", height=5, dxfattribs={"layer": "SECRET", "insert": (0, 20)})
    doc.saveas(tmp_path / "h.dxf")
    p = parse_dxf(str(tmp_path / "h.dxf"))
    text = text_of(plot_pdf(p, "A4", True, None, TITLE, {"SECRET"})[0])
    assert "KEEP" in text and "DROP" not in text


def test_sheet_as_laid_out(tmp_path):
    p = parse_dxf(str(write_all(tmp_path)["sheet"]))
    pdf, s = plot_sheet(p, "Layout1", "브래킷", set())
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    assert s == 1 and (
        round(float(page.mediabox.width) / PT),
        round(float(page.mediabox.height) / PT),
    ) == (420, 297)
    assert "TITLE: BRACKET" in page.extract_text()  # the sheet's own title block, no added frame
    assert "도면번호" not in page.extract_text()
    ops = page.get_contents().get_data().decode()
    # model circle (50, 50) R20 through the 2x viewport centred at (150, 150) on a sheet whose
    # limits start at -10 (the margin): its rightmost point lands 200 mm, 160 mm into the page
    assert f"{200 * PT:.4f} {160 * PT:.4f}" in ops
    with pytest.raises(DxfError) as e:
        plot_sheet(p, "Nope", "", set())
    assert e.value.code == "PLOT_SHEET_NOT_FOUND"


def test_api_pdf(client, world, tmp_path):
    path = write_all(tmp_path)["annotations"]
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("a.dxf", path.read_bytes())},
        headers=world.h["designer"],
    )
    rid = r.json()["data"]["revision_id"]
    r = client.post(
        f"/api/revisions/{rid}/pdf", json={"paper": "A2", "scale": 1}, headers=world.h["viewer"]
    )
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["x-plot-scale"] == "1:1" and r.content.startswith(b"%PDF")
    bad = client.post(f"/api/revisions/{rid}/pdf", json={"paper": "B4"}, headers=world.h["viewer"])
    assert bad.status_code == 422
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("s.dxf", write_all(tmp_path)["sheet"].read_bytes())},
        headers=world.h["designer"],
    )
    rid = r.json()["data"]["revision_id"]
    r = client.post(
        f"/api/revisions/{rid}/pdf", json={"sheet": "Layout1"}, headers=world.h["viewer"]
    )
    assert r.status_code == 200 and r.headers["x-plot-scale"] == "1:1"
    r = client.post(f"/api/revisions/{rid}/pdf", json={"sheet": "Nope"}, headers=world.h["viewer"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "PLOT_SHEET_NOT_FOUND"
