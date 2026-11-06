"""Track 8 P6-2: 표준품셈 PDF -> G1 draft. Synthetic ruled tables (not the real 품셈's numbers)."""

import hashlib
from decimal import Decimal as D

import pytest
from openpyxl import load_workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from core.quote_engine.costbook_extract import (
    ExtractError,
    draft_from_pdf,
    heading_unit,
    parse_row,
)
from core.quote_engine.g1 import parse_g1

FONT = "HYSMyeongJo-Medium"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONT", (0, 0), (-1, -1), FONT)])


def make_pdf(path, pages):
    """pages: [(title, rows)] -> one ruled table per page under its title."""
    style = ParagraphStyle("t", fontName=FONT, fontSize=12)
    story = []
    for i, (title, rows) in enumerate(pages):
        if i:
            story.append(PageBreak())
        story += [Paragraph(title, style), Table(rows, style=GRID)]
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


PLASTER = [
    ["구분", "단위", "수량"],
    ["미장공", "인", "0.06"],
    ["보통인부", "인", "0.02"],
    ["시멘트", "kg", "8.5"],
    ["잡재료비", "%", "3"],
]


def test_parse_row_and_heading():
    assert parse_row(["미장공", "인", "0.06"]) == {
        "name": "미장공", "unit": "인", "qty": D("0.06"),
        "qtys": [D("0.06")], "kind": "LABOR", "percent": False,
    }  # fmt: skip
    assert parse_row(["굴착기", "", "시간", "1,234.5"])["kind"] == "EQUIPMENT"
    assert parse_row(["구분", "단위", "수량"]) is None and parse_row(["메모만"]) is None
    assert parse_row(["잡재료비", "%", "3"])["percent"] is True
    assert heading_unit("벽 미장 (m2당)") == ("m2", D(1))
    assert heading_unit("벽 미장 (100㎡당)") == ("m2", D(100))
    assert heading_unit("제목만") == (None, D(1))
    assert heading_unit("장비조립･해체 (회당)") == ("회", D(1))  # a unit the tool does not know
    # letters spaced out by the typesetting ("특 별 인 부") are joined; real words keep their space
    assert parse_row(["특 별 인 부", "인", "1", "1", "1"])["name"] == "특별인부"
    assert parse_row(["벽 미장", "m2", "0.5"])["name"] == "벽 미장"
    assert parse_row(["보통인부", "인", "1", "2", "2"])["qtys"] == [
        D(1),
        D(2),
        D(2),
    ]  # several columns


def test_draft_workbook_rows_page_sha_and_checks(tmp_path):
    pdf, out = tmp_path / "p.pdf", tmp_path / "draft.xlsx"
    make_pdf(pdf, [("벽 미장 (100m2당)", PLASTER), ("제목에 단위 없음", PLASTER[:3])])
    s = draft_from_pdf(
        str(pdf), str(out), notice="국토교통부 공고 제2025-1574호", effective="2026-01-01"
    )
    assert s["items"] == 2 and s["resources"] == 6 and s["issues"] >= 3
    wb = load_workbook(out)
    item = [c.value for c in wb["12_품셈항목"][5]]
    assert item[0] == "P0001-1" and item[3] == "m2" and item[7] == 1
    assert item[8] == hashlib.sha256(pdf.read_bytes()).hexdigest() and item[9] == "PDF"
    assert item[10] is None  # no 검수자: a person fills it in after checking the page
    rows = [[c.value for c in r] for r in wb["13_품셈투입량"].iter_rows(min_row=5)]
    rows = [r for r in rows if r[0]]
    assert [r[1:4] for r in rows[:3]] == [
        ["노무", "L-0001", pytest.approx(0.0006)],  # 0.06 per 100 m2 -> per 1 m2
        ["노무", "L-0002", pytest.approx(0.0002)],
        ["재료", "M-0001", pytest.approx(0.085)],
    ]
    assert rows[3][2].startswith("PCT-") and rows[3][5] is None  # percent row: base to be filled in
    assert "점검" in rows[3][4]
    prices = [[c.value for c in r] for r in wb["14_자원단가"].iter_rows(min_row=5) if r[0].value]
    assert {p[2] for p in prices} == {"미장공", "보통인부", "시멘트"} and prices[0][4] is None
    issues = [[c.value for c in r] for r in wb["추출점검"].iter_rows(min_row=2)]
    assert any("단위를 찾지 못함" in i[3] or "'~당' 단위" in i[3] for i in issues)
    assert any("환산" in i[3] for i in issues)
    # the draft is a G1 workbook: the importer reads it (prices are blank, so it can't activate)
    r = parse_g1(str(out))
    assert r["errors"] == [], r["errors"]
    assert len(r["bundle"]["costbook_items"]) == 2


def test_page_range_and_errors(tmp_path):
    pdf = tmp_path / "p.pdf"
    make_pdf(pdf, [("벽 미장 (m2당)", PLASTER)] * 3)
    s = draft_from_pdf(str(pdf), str(tmp_path / "a.xlsx"), page_from=2, page_to=2)
    assert s["items"] == 1
    with pytest.raises(ExtractError):
        draft_from_pdf(str(pdf), str(tmp_path / "b.xlsx"), page_from=9, page_to=9)


def test_api_costbook_draft(client, world, tmp_path):
    pdf = tmp_path / "p.pdf"
    make_pdf(pdf, [("벽 미장 (m2당)", PLASTER)] * 2)

    def post(name, data, who="admin", **form):
        return client.post(
            "/api/master-data/costbook-draft",
            files={"file": (name, data, "application/pdf")},
            data=form,
            headers=world.h[who],
        )

    ok = post("품셈.pdf", pdf.read_bytes(), page_from="1", page_to="1", notice="공고")
    assert ok.status_code == 200, ok.text
    assert ok.headers["x-draft-items"] == "1" and ok.headers["x-draft-resources"] == "4"
    assert ok.content.startswith(b"PK")  # an .xlsx
    assert post("x.pdf", b"not a pdf").status_code == 422
    assert post("x.txt", pdf.read_bytes()).status_code == 422
    assert post("품셈.pdf", pdf.read_bytes(), page_from="9", page_to="9").status_code == 422
    assert post("품셈.pdf", pdf.read_bytes(), who="viewer").status_code == 403


# --- v2: numbered sections, not-ruled-between-rows tables, 일당 시공량 --------------------------------
def sectioned_pdf(path):
    """One page laid out like the real 건축부문 page: footer page number, section titles, a unit line,
    tables that are ruled only at the box and the header (no lines between data rows)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont(FONT, 10)
    H = 842

    def text(x, y, s):
        c.drawString(x, H - y, s)

    def box(x0, y0, x1, y1, cols, header_y):
        c.rect(x0, H - y1, x1 - x0, y1 - y0)
        for x in cols:
            c.line(x, H - y1, x, H - y0)
        c.line(x0, H - header_y, x1, H - header_y)

    text(70, 120, "9-1-3 모르타르 타설('14, '15, '19, '22, '25년 보완)")
    text(500, 145, "(일당)")
    box(70, 160, 545, 300, [165, 260, 355, 450], 205)
    text(95, 177, "구 분")
    text(190, 177, "단 위")
    text(285, 177, "수 량")
    text(375, 168, "시공량(㎥)")
    text(360, 195, "모르타르")
    text(455, 195, "경량기포콘크리트")
    for y, name, u, q in [
        (225, "미 장 공", "인", "2"),
        (245, "기 계 설 비 공", "인", "1"),
        (275, "보 통 인 부", "인", "2"),
        (290, "모르타르 타설장비", "대", "1"),
    ]:
        text(80, y, name)
        text(205, y, u)
        text(300, y, q)
    text(395, 252, "50")
    text(490, 252, "65")
    text(70, 320, "[주] ① 본 품은 모르타르 타설장비를 이용한 기준이다.")
    text(80, 336, "② 공구손료 및 경장비(비빔기 등)의 기계경비는 인력품의 2%로 계상한다.")
    text(70, 380, "9-1-4 표면 마무리('14, '15, '19년 보완)")
    text(480, 405, "(100㎡당)")
    box(70, 420, 545, 490, [165, 260, 355, 450], 462)
    text(95, 437, "구 분")
    text(190, 437, "규 격")
    text(285, 437, "단 위")
    text(395, 430, "수 량")
    text(360, 452, "인력마감")
    text(455, 452, "기계마감")
    text(80, 478, "미 장 공")
    text(205, 478, "-")
    text(300, 478, "인")
    text(395, 478, "0.30")
    text(490, 478, "0.22")
    text(70, 800, "624 건축부문")
    c.save()


def test_sectioned_page_gives_variants_daily_output_and_percent(tmp_path):
    pdf, out = tmp_path / "s.pdf", tmp_path / "s.xlsx"
    sectioned_pdf(pdf)
    s = draft_from_pdf(str(pdf), str(out), notice="공고", effective="2026-01-01")
    assert s["items"] == 4
    wb = load_workbook(out)
    items = {r[0]: r for r in wb["12_품셈항목"].iter_rows(min_row=5, values_only=True) if r[0]}
    a, b = items["A-9-1-3"], items["A-9-1-3.1"]
    assert (a[1], a[2], a[3], a[7], a[13]) == ("모르타르 타설", "모르타르", "m3", 624, 50)
    assert (b[2], b[13]) == ("경량기포콘크리트", 65)  # one item per 시공량 column
    assert a[8] == hashlib.sha256(pdf.read_bytes()).hexdigest() and a[14].startswith("[주]")
    rows = [r for r in wb["13_품셈투입량"].iter_rows(min_row=5, values_only=True) if r[0]]
    mine = [(r[1], r[3]) for r in rows if r[0] == "A-9-1-3"]
    assert mine[:4] == [
        ("노무", 2),
        ("노무", 1),
        ("노무", 2),
        ("장비", 1),
    ]  # 인원/일, equipment 1대
    pct = [r for r in rows if r[0] == "A-9-1-3" and r[5]]
    assert pct and pct[0][3] == 2 and pct[0][5] == "노무"  # 인력품의 2% (a 비율 투입량)
    assert items["A-9-1-4"][2] == "인력마감" and items["A-9-1-4.1"][2] == "기계마감"
    per_m2 = {r[0]: r[3] for r in rows if r[0].startswith("A-9-1-4")}
    assert per_m2["A-9-1-4"] == pytest.approx(0.003) and per_m2["A-9-1-4.1"] == pytest.approx(
        0.0022
    )
    r = parse_g1(str(out))  # a G1 workbook the importer reads: 일당 시공량 and 원문 비고 included
    assert r["errors"] == [], r["errors"]
    got = {i["item_code"]: i for i in r["bundle"]["costbook_items"]}
    assert got["A-9-1-3"]["daily_output"] == "50" and got["A-9-1-3"]["remark"].startswith("[주]")
