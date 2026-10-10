"""FN-21 quote document: Korean amount words, PDF text (Korean font embedded), XLSX cells."""

import hashlib
import io
from decimal import Decimal as D

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from core.quote_engine.report import FONT_DIR, korean_amount, render_pdf, render_xlsx


def model(**over):
    m = {
        "quote_no": "Q-20261010-000003",
        "issued": "2026-10-10",
        "valid_until": "2026-11-09",
        "official": True,
        "basis": True,
        "master_version_id": 2,
        "source": "2D 도면 D1 Rev 3",
        "manual_count": 1,
        "supplier": {
            "company": "(주)샘플정밀",
            "business_no": "000-00-00000",
            "ceo": "홍길동",
            "address": "경기도 안산시 단원구 샘플로 123",
            "phone": "031-000-0000",
            "email": "sales@example.com",
        },
        "customer": {"name": "한국기계(주)", "project": "브래킷 견적"},
        "item": {
            "name": "BRACKET <b>A</b>",
            "spec": "P-100 / SS400 t3.2",
            "qty": D(4),
            "unit": "EA",
            "unit_price": D("14302.50"),
            "supply": D(57210),
            "vat": D(5720),
        },
        "terms": {"delivery": "발주 후 14일", "payment": "납품 후 익월 말 현금", "note": "샘플"},
        "totals": {
            "material_cost": D(9000),
            "labor_cost": D(19182),
            "overhead_cost": D(20884),
            "outsource_cost": D(0),
            "manufacturing_cost": D(49066),
            "admin_cost": D(2944),
            "total_cost": D(52010),
            "profit": D(5201),
            "supply_amount": D(57210),
            "vat_amount": D(5720),
            "total_amount": D(62930),
        },
        "lines": [
            {
                "line_no": 1,
                "cost_category": "MATERIAL",
                "item_name": "SS400 소재",
                "qty": D("2.059037"),
                "unit": "kg",
                "unit_price": D("1200.50"),
                "amount": D("9000.00"),
                "manual": True,
                "override_reason": '=HYPERLINK("http://x")',
                "basis": "volume_mm3 × density / 1e6 × (1 + scrap_rate) × qty",
            }
        ],
    }
    return m | over


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "영"),
        (10, "일십"),
        (10000, "일만"),
        (62930, "육만이천구백삼십"),
        (100_000_000, "일억"),
        (1_234_005_678, "일십이억삼천사백만오천육백칠십팔"),
    ],
)
def test_korean_amount(n, words):
    assert korean_amount(n) == words


def test_pdf_text_has_korean_totals_and_no_watermark_when_official():
    pdf = render_pdf(model())
    text = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages)
    assert "견" in text and "(주)샘플정밀" in text and "한국기계(주)" in text
    assert "일금 육만이천구백삼십원정" in text and "62,930" in text and "57,210" in text
    assert "14,302.50" in text  # 57,210 / 4: the fraction is shown, not rounded away
    assert "BRACKET <b>A</b>" in text  # user text is escaped, not markup
    assert "산출근거" in text and "DRAFT" not in text
    assert b"/FontFile2" in pdf  # TrueType font embedded (D7)
    assert render_pdf(model()) == pdf  # deterministic: registry hash can be re-checked


def test_pdf_draft_is_watermarked():
    text = "".join(
        pg.extract_text()
        for pg in PdfReader(io.BytesIO(render_pdf(model(official=False, basis=False)))).pages
    )
    assert "초안(DRAFT)" in text and "산출근거" not in text


def test_xlsx_cells_match_and_text_stays_text():
    wb = load_workbook(io.BytesIO(render_xlsx(model())))
    ws = wb["견적서"]
    cells = {c.value: c for row in ws.iter_rows() for c in row if c.value is not None}
    item_row = next(r for r in ws.iter_rows() if r[0].value == 1)
    assert [c.value for c in item_row[3:]] == [4, "EA", 14302.5, 57210, 5720]
    assert "일금 육만이천구백삼십원정" in cells
    assert [r for r in wb["원가내역"].iter_rows(values_only=True)][-1] == ("합계", 62930)
    assert wb["산출근거"]["A1"].value == "내부용·대외비"
    reason = wb["산출근거"]["I3"]
    assert reason.value.startswith("=HYPERLINK") and reason.data_type == "s"
    assert reason.quotePrefix  # stays text even after the cell is edited
    assert render_xlsx(model(official=False))[:2] == b"PK"
    draft = load_workbook(io.BytesIO(render_xlsx(model(official=False, basis=False))))
    assert "DRAFT" in draft["견적서"]["A1"].value and draft.sheetnames == ["견적서"]


def test_control_characters_do_not_break_rendering():
    m = model(customer={"name": "한국\x01기계\x1f(주)", "project": "건\x00명"})
    ws = load_workbook(io.BytesIO(render_xlsx(m)))["견적서"]
    assert any(r[1] == "한국기계(주) 귀하" for r in ws.iter_rows(values_only=True))
    assert "한국기계(주)" in "".join(
        pg.extract_text() for pg in PdfReader(io.BytesIO(render_pdf(m))).pages
    )


def test_fonts_match_manifest():
    manifest = (FONT_DIR / "README.md").read_text(encoding="utf-8")
    for f in ("NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf"):
        assert hashlib.sha256((FONT_DIR / f).read_bytes()).hexdigest() in manifest, f
