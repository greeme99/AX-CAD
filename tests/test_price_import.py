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


def machines_pdf(path):
    c = canvas.Canvas(str(path))
    c.setFont("HYSMyeongJo-Medium", 9)
    lines = [
        "굴착기 0201-0100 1.00㎥ 142,069 29,621 19.5 22 1 - -",
        "(무한궤도) 0201-0060 0.60㎥ 112,342 23,423 10.2 22 1 - -",
        "래머 1630-0080 80㎏ 1,418 525 휘발유0.7 10 1 - -",
        "발전기 7505-0100 100㎾ 24,124 5,698 17.4 24 1 - -",
        "아스팔트 믹싱플랜트 3108-0040 40ton( 80㎾) 341,024 85,290 중유487.2 - 2 - -",
        "덤프트럭 0602-0045 4.5ton 25,662 7,613 5.0 38 1 - -",
        "덤프트럭 0602-0150 15ton 90,000 20,000 15.9 38 1 - -",
        "콘크리트 부속장치 0610-0150 15ton 1,634 438 - - - - -",
    ]
    for n, line in enumerate(lines):
        c.drawString(40, 800 - n * 16, line)
    c.save()


def test_machine_cost_follows_the_8_4_formula(tmp_path):
    from decimal import Decimal as D

    from core.quote_engine import machine_cost as mc

    p = tmp_path / "m.pdf"
    machines_pdf(p)
    got = mc.parse_machine_pdf(str(p))
    rows = {r["code"]: r for r in got["rows"]}
    assert rows["0201-0100"]["fuels"] == [("경유", D("19.5"))] and rows["0201-0100"]["misc"] == 22
    assert rows["1630-0080"]["fuels"] == [("휘발유", D("0.7"))]
    assert (
        rows["3108-0040"]["fuels"] == [("중유", D("487.2"))] and rows["3108-0040"]["operator"] == 2
    )
    wage = {"건설기계운전사": D(283323), "화물차운전사": D(240042), "일반기계운전사": D(173995)}
    out = mc.compute(
        got["rows"], {"경유": D("1842.01"), "휘발유": D("1855.31")}, wage, "2026-11-04"
    )
    by = {r["resource_code"]: r for r in out["rows"]}
    # 굴착기 1.0㎥: 손료 29,621 + 연료 19.5 x 1,842.01 (35,919) + 잡재 22 % (7,902) + 운전원 283,323 / 8
    fuel = D("19.5") * D("1842.01")
    expected = D(29621) + fuel + fuel * D("0.22") + D(283323) / 8
    assert D(by["E-0201-0100"]["unit_price"]) == expected.quantize(D("0.01"))
    assert (
        round(fuel) == 35919 and round(fuel * D("0.22")) == 7902
    )  # the numbers in the reference note
    assert by["E-1630-0080"]["resource_type"] == "EQUIPMENT"  # 래머: 일반기계운전사 노임
    assert "일반기계" in by["E-1630-0080"]["source"] and "일반기계" in by["E-7505-0100"]["source"]
    assert "화물차" in by["E-0602-0045"]["source"]  # 12ton 미만 덤프트럭
    assert "건설기계" in by["E-0602-0150"]["source"]
    assert D(by["E-0610-0150"]["unit_price"]) == D(438).quantize(D("0.01"))  # an attachment: 손료만
    assert any("중유 단가 없음" in s for s in out["skipped"]) and "E-3108-0040" not in by


def test_api_machines_need_fuel_and_wage_prices(client, world, tmp_path):
    admin = world.h["admin"]
    vid, _ = master(client, admin, FULL | CONSTRUCTION | {"work_items": []})
    machines_pdf(tmp_path / "m.pdf")
    (tmp_path / "w.csv").write_text(
        "코드,구분,이름,단위,단가,기준일,출처\n"
        "L-1048,노무,건설기계운전사,인,283323,2026-09-01,노임\n"
        "L-1049,노무,화물차운전사,인,240042,2026-09-01,노임\n"
        "L-1050,노무,일반기계운전사,인,173995,2026-01-01,노임(2026.9.1 미조사)\n",
        encoding="utf-8",
    )

    def post(files, **form):
        return client.post(
            f"/api/master-versions/{vid}/import-prices", files=files, data=form, headers=admin
        )

    pdf = ("machines", ("경비산출표.pdf", (tmp_path / "m.pdf").read_bytes(), "application/pdf"))
    assert post([pdf]).status_code == 422  # no fuel price
    wage = ("manual", ("w.csv", (tmp_path / "w.csv").read_bytes(), "text/csv"))
    r = post([wage, pdf], diesel_price="1842.01", gasoline_price="1855.31", fuel_date="2026-11-04")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (
        d["added"] == 3 + 7 + 2 and d["skipped_total"] == 1
    )  # wages + 7 machines + 2 fuels, 중유 skipped
    prices = client.get(f"/api/master-versions/{vid}", headers=admin).json()["data"][
        "resource_prices"
    ]
    by = {p["resource_code"]: p for p in prices}
    assert (
        by["M-FUEL-DIESEL"]["unit_price"].startswith("1842.01")
        and by["E-0201-0100"]["unit"] == "시간"
    )
    assert by["E-0201-0100"]["resource_type"] == "EQUIPMENT"


def test_match_prices_by_name_only_when_one_candidate_agrees():
    from core.quote_engine.price_match import match

    def row(code, name, unit="인", price=None, kind="LABOR"):
        return {
            "resource_code": code, "resource_type": kind, "resource_name": name, "unit": unit,
            "unit_price": price, "price_date": "2026-09-01" if price else None, "source": "노임",
        }  # fmt: skip

    unpriced = [
        row("L-0001", "보 통 인 부"),
        row("L-0002", "철근공"),
        row("L-0003", "콘크리트공", unit="시간"),
        row("L-0004", "목수"),
    ]
    priced = [
        row("L-1027", "보통인부", price="150000"),
        row("L-1030", "철근공", price="200000"),
        row("L-1031", "철근 공", price="210000"),
        row("L-1040", "콘크리트공", price="190000"),
    ]
    new, rep = match(unpriced, priced)
    assert [n["resource_code"] for n in new] == ["L-0001"] and new[0]["unit_price"] == "150000"
    assert "L-1027" in new[0]["source"]
    assert len(rep["ambiguous"]) == 1 and "철근공" in rep["ambiguous"][0]  # two candidates
    assert any("콘크리트공" in u for u in rep["unmatched"])  # unit differs
    assert any("목수" in u for u in rep["unmatched"])
