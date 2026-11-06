"""Track 8 P6-6: 자원단가 from 노임 PDF, 조달청 자재 엑셀 and a CSV list (synthetic files in the real layouts)."""

import pytest
from openpyxl import Workbook
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from core.quote_engine import price_import as pi
from tests.test_api_master import CONSTRUCTION, FULL
from tests.test_costbook import master

pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))


def labor_pdf(path, rows=None):
    c = canvas.Canvas(str(path))
    c.setFont("HYSMyeongJo-Medium", 10)
    lines = [
        "Ⅲ. 개 별 직 종 노 임 단 가",
        "공 표 일",
        "2026.9.1 2026.1.1 2025.9.1 2025.1.1",
        "1002 보 통 인 부 172,698 172,068 171,037 169,804",
        "*1049 화 물 차 운 전 사 240,042 238,936 240,685 237,500",
        "**1050 일 반 기 계 운 전 사 - 173,995 172,387 170,920",
        "1027 미 장 공 279,102 277,276 278,998 272,354",
    ]
    for n, line in enumerate(rows or lines):
        c.drawString(50, 780 - n * 16, line)
    c.save()


def materials_xlsx(path):
    wb = Workbook()
    ws = wb.active
    ws.append(["시설공통자재 가격정보"])
    ws.append(
        [
            "NO",
            "자재구분",
            "물품분류번호",
            "물품식별번호",
            "품명",
            "규격",
            "인도조건",
            "부가세여부",
            "게시일자",
            "단위",
            "가격",
            "관할지역",
        ]
    )
    ws.append(
        [
            1,
            "시설자재(건축분야) ",
            "30161509",
            "20153201",
            "석고보드",
            "석고보드, 평보드, 15.0×900×18",
            "현장도착도",
            "부가가치세별도",
            "2026-10-01 00:00:00.0",
            "매",
            "8304",
            "전지역(제주제외)",
        ]
    )
    ws.append(
        [
            2,
            "시설자재(건축분야) ",
            "30161509",
            "20153202",
            "석고보드",
            "석고보드, 평보드, 15.0×900×18",
            "현장도착도",
            "부가가치세별도",
            "2026-10-01 00:00:00.0",
            "㎡",
            "5125",
            "전지역(제주제외)",
        ]
    )
    ws.append(
        [
            3,
            "시설자재(건축분야) ",
            "30161509",
            "BAD CODE!",
            "나쁜 코드",
            "x",
            "x",
            "x",
            "2026-10-01",
            "개",
            "100",
            "x",
        ]
    )
    wb.save(path)


MANUAL = '코드,구분,이름,단위,단가,기준일,출처\nM-KPI-RMC,재료,레미콘 25-120-21,㎥,"100,910",2026-10-01,KPI물가동향 보고서 2026.10.21\n,재료,코드 없음,개,1,,\n'


def test_labor_pdf_reads_the_newest_column(tmp_path):
    p = tmp_path / "l.pdf"
    labor_pdf(p)
    r = pi.parse_labor_pdf(str(p))
    by = {x["resource_code"]: x for x in r["rows"]}
    assert by["L-1002"]["resource_name"] == "보통인부" and by["L-1002"]["unit_price"] == "172698"
    assert by["L-1002"]["price_date"] == "2026-09-01" and by["L-1002"]["unit"] == "인"
    assert "5개 미만" in by["L-1049"]["source"]  # a "*" 직종
    assert "L-1050" not in by and any("1050" in s and "조사 안 됨" in s for s in r["skipped"])
    with pytest.raises(pi.PriceImportError):
        labor_pdf(tmp_path / "x.pdf", ["아무 표도 없음"])
        pi.parse_labor_pdf(str(tmp_path / "x.pdf"))


def test_materials_and_manual_lists(tmp_path):
    x = tmp_path / "m.xlsx"
    materials_xlsx(x)
    r = pi.parse_materials_xlsx(str(x))
    assert [m["resource_code"] for m in r["rows"]] == ["M-20153201", "M-20153202"]
    a = r["rows"][0]
    assert a["unit_price"] == "8304" and a["price_date"] == "2026-10-01" and a["unit"] == "매"
    assert (
        a["resource_name"] == "석고보드, 평보드, 15.0×900×18"
    )  # the spec already starts with the name
    assert "부가가치세별도" in a["source"] and len(r["skipped"]) == 1
    c = tmp_path / "k.csv"
    c.write_bytes(MANUAL.encode("cp949"))  # a Korean Excel CSV
    m = pi.parse_manual_csv(str(c))
    assert m["rows"][0]["unit_price"] == "100910" and m["rows"][0]["resource_type"] == "MATERIAL"
    assert len(m["skipped"]) == 1
    c.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(pi.PriceImportError):
        pi.parse_manual_csv(str(c))


def test_api_import_prices_merges_into_a_draft(client, world, tmp_path):
    admin = world.h["admin"]
    vid, r = master(client, admin, FULL | CONSTRUCTION | {"work_items": []})
    assert r.status_code == 200
    labor_pdf(tmp_path / "l.pdf")
    materials_xlsx(tmp_path / "m.xlsx")
    (tmp_path / "k.csv").write_text(MANUAL, encoding="utf-8")

    def post(files, who="admin", version=vid):
        return client.post(
            f"/api/master-versions/{version}/import-prices", files=files, headers=world.h[who]
        )

    def files():
        return [
            ("labor", ("노임.pdf", (tmp_path / "l.pdf").read_bytes(), "application/pdf")),
            (
                "materials",
                ("자재.xlsx", (tmp_path / "m.xlsx").read_bytes(), "application/octet-stream"),
            ),
            ("manual", ("직접.csv", (tmp_path / "k.csv").read_bytes(), "text/csv")),
        ]

    ok = post(files())
    assert ok.status_code == 200, ok.text
    d = ok.json()["data"]
    assert d["added"] == 3 + 2 + 1 and d["updated"] == 0 and d["total"] == 6
    assert (
        d["skipped_total"] == 3
    )  # 일반기계운전사 not surveyed, a bad code, a CSV row without a code
    again = post(files()).json()["data"]
    assert (
        again["added"] == 0 and again["updated"] == 6 and again["total"] == 6
    )  # same code + date: updated
    prices = client.get(f"/api/master-versions/{vid}", headers=admin).json()["data"][
        "resource_prices"
    ]
    assert {p["resource_code"] for p in prices} >= {"L-1002", "M-20153201", "M-KPI-RMC"}
    assert post([("labor", ("x.txt", b"%PDF-", "x"))]).status_code == 422  # wrong extension
    assert post([("labor", ("x.pdf", b"not a pdf", "x"))]).status_code == 422  # wrong magic bytes
    assert post([]).status_code == 422
    assert post(files(), who="viewer").status_code == 403
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code in (
        200,
        422,
    )
