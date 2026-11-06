"""Track 8 P6-2: 표준품셈 PDF -> draft of the G1 sheets 12·13·14.

Reads the ruled tables of a page range with pdfplumber and writes a G1 workbook whose 품셈 sheets
hold *draft* rows: item code `P<page>-<n>`, name and unit from the heading above the table
("(m2당)"), one 13 row per table row that has a name and a number, resources collected in 14 with
blank prices. Nothing here is a finished number: every row carries the original page and the PDF's
SHA-256, the 검수자 column stays empty, and what looks wrong (no unit, quantity out of range,
a percent row, a resource twice, a scanned page) goes to the sheet `추출점검` and the row's 비고.
A person checks each page against the original before importing (the engine warns on items without
a 검수자). Scanned pages are listed, not read (no OCR).

The tables of the real 품셈 vary (merged cells, notes, several units in one table); this is a
starting point to cut typing, tuned on synthetic tables, not a parser to trust."""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from core.quote_engine.g1 import (
    FIRST_ROW,
    S_CB,
    S_CB_RES,
    S_RES_PRICE,
    build_template,
)

MAX_PAGES = 150  # one run (the whole 건축 chapter is done in several runs)
MAX_QTY = Decimal(10_000)
NOTE = "점검: "
ISSUES_SHEET = "추출점검"
UNITS = {
    "인": "인", "시간": "시간", "hr": "시간", "h": "시간", "kg": "kg", "ton": "ton", "t": "ton",
    "m": "m", "m2": "m2", "m3": "m3", "㎡": "m2", "㎥": "m3", "m²": "m2", "m³": "m3",
    "개": "개", "본": "본", "매": "매", "장": "장", "식": "식", "대": "대", "l": "L", "ℓ": "L",
    "㎏": "kg", "㎜": "mm", "mm": "mm", "cm": "cm", "%": "%",
}  # fmt: skip
HEADER_WORDS = {"구분", "명칭", "품명", "규격", "단위", "수량", "비고", "공종", "종별", "직종"}
KIND_KO = {"LABOR": "노무", "MATERIAL": "재료", "EQUIPMENT": "장비"}
KIND_CODE = {"LABOR": "L", "MATERIAL": "M", "EQUIPMENT": "E"}
PER = re.compile(r"[(\[（]\s*([^()\[\]（）]{1,16}?)\s*당\s*[)\]）]")
SCALED = re.compile(r"(\d+(?:\.\d+)?)\s*(.+)")


class ExtractError(ValueError):
    """The request cannot be run (bad page range, not a PDF)."""


def _cell(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


HANGUL = re.compile(r"[가-힣]")


def _name(s: str) -> str:
    """'특 별 인 부' (letters spaced out by the typesetting) -> '특별인부'; '벽 미장' stays."""
    parts = s.split(" ")
    if len(parts) > 1 and all(len(t) == 1 and HANGUL.fullmatch(t) for t in parts):
        return "".join(parts)
    return s


def _num(s: str) -> Decimal | None:
    t = s.replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:  # pragma: no cover - the pattern already rules it out
        return None


def _unit(s: str) -> str | None:
    return UNITS.get(s.replace(" ", "").lower()) or UNITS.get(s.replace(" ", ""))


def heading_unit(text: str) -> tuple[str | None, Decimal]:
    """'미장 (100m2당)' -> ('m2', 100): the unit of the work and how many of it the table is for."""
    m = PER.search(text)
    if not m:
        return None, Decimal(1)
    raw = m[1].strip()
    scale = Decimal(1)
    if (s := SCALED.fullmatch(raw)) and _unit(s[2].strip()):
        scale, raw = Decimal(s[1]), s[2].strip()
    unit = _unit(raw)
    if unit is None and 0 < len(raw) <= 8 and _num(raw) is None:
        unit = raw  # a unit this tool does not know ("회", "개소"): kept as written
    return unit, scale


def parse_row(cells: list[Any]) -> dict[str, Any] | None:
    """One table row -> {name, unit, qty, kind, percent} or None (header, empty, no number)."""
    vals = [_cell(c) for c in cells]
    vals = [v for v in vals if v]
    if len(vals) < 2 or any(v in HEADER_WORDS for v in vals):
        return None
    q = next((i for i in range(len(vals) - 1, -1, -1) if _num(vals[i]) is not None), None)
    if q is None:
        return None
    first = q  # the quantity columns: the run of numbers that ends the row
    while first > 0 and _num(vals[first - 1]) is not None:
        first -= 1
    qtys = [n for v in vals[first : q + 1] if (n := _num(v)) is not None]
    rest = vals[:first] + vals[q + 1 :]
    unit = next((u for v in reversed(rest) if (u := _unit(v))), None)
    name = next((v for v in rest if _num(v) is None and not _unit(v)), None)
    if name is None:
        return None
    name = _name(name)
    percent = unit == "%" or "%" in name
    kind = "LABOR" if unit == "인" else "EQUIPMENT" if unit == "시간" else "MATERIAL"
    return {
        "name": name,
        "unit": unit,
        "qty": qtys[-1],
        "qtys": qtys,
        "kind": kind,
        "percent": percent,
    }


class _Draft:
    def __init__(self) -> None:
        self.items: list[list[Any]] = []
        self.rows: list[list[Any]] = []
        self.prices: dict[tuple[str, str], list[Any]] = {}
        self.issues: list[tuple[Any, ...]] = []
        self.codes: dict[tuple[str, str], str] = {}

    def code(self, kind: str, name: str) -> str:
        key = (kind, name)
        if key not in self.codes:
            n = sum(1 for k, _ in self.codes if k == kind) + 1
            self.codes[key] = f"{KIND_CODE[kind]}-{n:04d}"
        return self.codes[key]


def _heading(page: Any, top: float) -> str:
    """The last text line just above a table (its title and 'per unit')."""
    if top <= 2:
        return ""
    box = page.crop((0, max(0.0, top - 60), page.width, top - 1))
    lines = [_cell(x) for x in (box.extract_text() or "").split("\n")]
    lines = [x for x in lines if x]
    return " ".join(lines[-2:]) if lines else ""


def _add_table(d: _Draft, page_no: int, n: int, rows: list[Any], head: str, ctx: dict[str, Any]):
    parsed = [(i, p) for i, r in enumerate(rows, 1) if (p := parse_row(r))]
    if not parsed:
        return
    code = f"P{page_no:04d}-{n}"
    unit, scale = heading_unit(head)
    title = PER.sub("", head).strip(" -:·") or f"p.{page_no} 표 {n}"
    notes: list[str] = []
    if unit is None:
        notes.append("제목에서 '~당' 단위를 찾지 못함")
    if scale != 1:
        notes.append(f"원문은 {scale.normalize():f}{unit}당 → 1{unit}당으로 환산")
    seen: set[str] = set()
    out: list[list[Any]] = []
    for i, p in parsed:
        qty = p["qty"] / scale
        flags: list[str] = []
        if qty <= 0 or qty > MAX_QTY:
            flags.append("투입량이 0 이하이거나 너무 큼")
        if p["unit"] is None:
            flags.append("단위를 찾지 못함")
        if p["name"] in seen:
            flags.append("같은 자원이 한 표에 두 번")
        seen.add(p["name"])
        percent_of = None
        if p["percent"]:
            flags.append("비율 항목: 비율기준(노무/재료/장비)을 적고 투입량은 % 숫자인지 확인")
            percent_of = ""
        kind = p["kind"]
        rcode = d.code(kind, p["name"]) if not p["percent"] else f"PCT-{len(d.rows) + 1:04d}"
        if not p["percent"]:
            d.prices.setdefault(
                (kind, p["name"]), [rcode, KIND_KO[kind], p["name"], p["unit"] or "", None]
            )
        out.append(
            [code, KIND_KO[kind], rcode, float(qty), NOTE + "; ".join(flags) if flags else None]
            + [percent_of]
        )
        for f in flags:
            d.issues.append((page_no, n, i, f"{p['name']}: {f}"))
    for f in notes:
        d.issues.append((page_no, n, "-", f))
    d.rows += out
    d.items.append(
        [code, title[:80], None, unit or "?", None, ctx["notice"], ctx["effective"], page_no]
        + [ctx["sha"], "PDF", None, None, NOTE + "; ".join(notes) if notes else None]
    )


def extract(pdf_path: str, page_from: int, page_to: int, ctx: dict[str, Any]) -> _Draft:
    import pdfplumber  # imported late: the API process never loads it, only the isolated worker

    d = _Draft()
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        last = min(page_to, total)
        if page_from < 1 or page_from > last:
            raise ExtractError(f"쪽 범위가 올바르지 않습니다 (PDF는 {total}쪽)")
        if last - page_from + 1 > MAX_PAGES:
            raise ExtractError(f"한 번에 {MAX_PAGES}쪽까지 처리합니다")
        for no in range(page_from, last + 1):
            page = pdf.pages[no - 1]
            text = (page.extract_text() or "").strip()
            if len(text) < 10 and page.images:
                d.issues.append(
                    (no, "-", "-", "스캔된 쪽으로 보임: 글자를 읽지 않았습니다(OCR 안 함)")
                )
            for n, t in enumerate(page.find_tables(), 1):
                _add_table(d, no, n, t.extract(), _heading(page, t.bbox[1]), ctx)
            page.flush_cache()
    return d


def write_draft(d: _Draft, out_path: str) -> None:
    """The blank G1 workbook with the draft rows in 12·13·14 and the issue list in 추출점검."""
    from openpyxl import load_workbook

    build_template(out_path)
    wb = load_workbook(out_path)
    for sheet, rows in (
        (S_CB, d.items),
        (S_CB_RES, d.rows),
        (S_RES_PRICE, [[*v, None, None, None, None] for v in d.prices.values()]),
    ):
        ws = wb[sheet]
        ws.delete_rows(FIRST_ROW, ws.max_row)  # the example rows
        for r, row in enumerate(rows, FIRST_ROW):
            for c, v in enumerate(row, 1):
                ws.cell(row=r, column=c, value=v)
    ws = wb.create_sheet(ISSUES_SHEET)
    ws.append(["쪽", "표", "행", "점검 내용"])
    for issue in d.issues:
        ws.append(list(issue))
    wb.save(out_path)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def draft_from_pdf(
    pdf_path: str,
    out_path: str,
    *,
    notice: str | None = None,
    effective: str | None = None,
    page_from: int = 1,
    page_to: int = MAX_PAGES,
) -> dict[str, int]:
    ctx = {"notice": notice, "effective": effective, "sha": sha256(pdf_path)}
    d = extract(pdf_path, page_from, page_to, ctx)
    write_draft(d, out_path)
    return {
        "items": len(d.items),
        "resources": len(d.rows),
        "prices": len(d.prices),
        "issues": len(d.issues),
    }


def draft_job(pdf_path: str, out_path: str, options: str) -> str:
    """Worker entry (run_isolated): the summary as JSON text; ExtractError becomes `error`."""
    try:
        o = json.loads(options)
        return json.dumps(draft_from_pdf(pdf_path, out_path, **o), ensure_ascii=False)
    except ExtractError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


if (
    __name__ == "__main__"
):  # uv run python -m core.quote_engine.costbook_extract in.pdf out.xlsx 1 50
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    a, b = (int(x) for x in (sys.argv[3:5] if len(sys.argv) > 4 else (1, MAX_PAGES)))
    print(draft_from_pdf(src, dst, page_from=a, page_to=b))
