"""Track 8 P6-6: 표준품셈 PDF reader v2 — tables under numbered sections (`9-1-3 모르타르 타설`).

The real 품셈 tables are not ruled between their rows (only the box and the header lines), so cells
cannot be read; words are placed by their x / y instead:
- columns come from the x edges of the table's header cells (구분 | 규격 | 단위 | 수량 … | 시공량),
- a data row is a word in the 단위 column; its name is the 구분 words on the same line, its numbers
  are the words in the quantity columns on that line,
- 수량 columns with sub-headings (인력마감 / 기계마감) and 시공량 columns (일당 표: 하루 인원 and the
  시공량 of that day) become one draft item each, groups of rows (시공높이 3.6m이하 …) too,
- the unit line `(100㎡당)` / `(일당)` and the book's page number (the footer) come from the page,
- the 비고 / [주] text under the table is kept as 원문 비고 and "인력품의 N%" notes become a
  비율 투입량 (P6-1).
Everything is a draft for a person to check against the original page; what could not be read goes
to the check list."""

import re
from decimal import Decimal
from typing import Any

from core.quote_engine.costbook_extract import (
    KIND_KO,
    MAX_QTY,
    NOTE,
    UNITS,
    _Draft,
    _name,
    _num,
    _unit,
    heading_unit,
)

SECTION = re.compile(r"^(\d{1,2}-\d{1,2}-\d{1,2})\s+(.+?)\s*(?:\(\s*['’‘].*)?$")
FOOTER = re.compile(r"^(?:\d{3}\s+\S+부문|제\s*\d+장\s+.+?\s+\d{3})$")
PART = {"건축": "A", "공통": "C", "토목": "T", "기계설비": "M", "유지관리": "U"}
NOTE_PCT = re.compile(
    r"(공구손료[^.①-⑩]{0,40}?)(?:의\s*)?기계경비[는은]\s*인력품의\s*(\d+(?:\.\d+)?)\s*%"
)
SKIP_HEAD = ("기계명", "재료명")  # equipment composition tables carry no quantities
MAX_REMARK = 1900


def _collapse(s: str) -> str:
    """'시 공 높 이 3 . 6 m 이 하' -> '시공높이 3.6m이하' (typeset letter spacing; real words stay)."""
    out: list[str] = []
    run: list[str] = []
    for t in s.split():
        if len(t) == 1:
            run.append(t)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(t)
    if run:
        out.append("".join(run))
    return re.sub(r"([가-힣])(\d)", r"\1 \2", " ".join(out))


HEADING2 = re.compile(
    r"^\d{1,2}-\d{1,2}\s+\S"
)  # "9-2 콘크리트면 마무리": a part heading, not a note


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _edges(cells: list[tuple[float, float, float, float]]) -> list[float]:
    xs = sorted({round(c[0]) for c in cells} | {round(c[2]) for c in cells})
    out: list[float] = []
    for x in xs:
        if not out or x - out[-1] > 3:
            out.append(float(x))
    return out


def _band(x: float, edges: list[float]) -> int | None:
    for i in range(len(edges) - 1):
        if edges[i] - 1 <= x < edges[i + 1] + 1:
            return i
    return None


def _join(words: list[dict[str, Any]]) -> str:
    return " ".join(w["text"] for w in sorted(words, key=lambda w: (round(w["top"] / 4), w["x0"])))


def parse_table(page: Any, table: Any) -> dict[str, Any] | None:
    """-> {unit, daily_unit, variants:[{label, daily, rows:[(name, unit, qty)]}], last_y} or None."""
    edges = _edges(table.cells) if table.cells else []
    if len(edges) < 4:
        return None
    x0, top, x1, bottom = table.bbox
    words = page.crop((x0 - 1, top - 1, x1 + 1, bottom + 1)).extract_words(
        x_tolerance=2, y_tolerance=2
    )
    for w in words:
        w["band"] = _band((w["x0"] + w["x1"]) / 2, edges)
    nb = len(edges) - 1
    by_band: list[list[dict[str, Any]]] = [[w for w in words if w["band"] == b] for b in range(nb)]
    # the 단위 column: the band whose first-line words, squeezed, read 단위
    first_top = min(w["top"] for w in words)
    first_line = [w for w in words if w["top"] <= first_top + 16]
    unit_band = next(
        (
            b
            for b in range(nb)
            if _squash("".join(w["text"] for w in first_line if w["band"] == b)) == "단위"
        ),
        None,
    )
    if unit_band is None:
        return None
    if any(h in _squash(" ".join(w["text"] for w in words[:40])) for h in SKIP_HEAD):
        return None
    head_bottom = max(w["bottom"] for w in first_line if w["band"] == unit_band)
    rows_y = sorted(w["top"] for w in by_band[unit_band] if w["top"] > head_bottom + 2)
    if not rows_y:
        return None
    data_top = rows_y[0] - 3
    header = [w for w in words if w["bottom"] <= data_top + 2]
    htext = {b: _squash("".join(w["text"] for w in header if w["band"] == b)) for b in range(nb)}
    spec_band = next((b for b in range(unit_band) if "규격" in htext[b]), None)
    name_bands = [b for b in range(unit_band) if b != spec_band]
    daily_start = next((b for b in range(unit_band + 1, nb) if "시공량" in htext[b]), None)
    q_bands = [b for b in range(unit_band + 1, nb) if daily_start is None or b < daily_start]
    d_bands = [] if daily_start is None else list(range(daily_start, nb))
    daily_unit = None
    if daily_start is not None:
        m = re.search(r"시공량\(([^)]+)\)", htext[daily_start])
        daily_unit = _unit(m[1]) if m else None

    def label(b: int, skip: tuple[str, ...]) -> str:
        ws = [w for w in header if w["band"] == b and w["text"] not in skip]
        return _join(ws).strip()

    q_label = {b: _collapse(label(b, ("수", "량", "수량"))) for b in q_bands}
    d_label = {b: _collapse(re.sub(r"시공량\([^)]*\)", "", label(b, ())).strip()) for b in d_bands}
    # row groups: where the table's cells start a new block (시공높이 3.6m이하 / 초과 …)
    cuts = sorted({round(c[1]) for c in table.cells if c[1] > data_top + 2})
    group_band = name_bands[0] if len(name_bands) >= 2 else None
    name_band = name_bands[-1] if name_bands else None
    if name_band is None:
        return None

    def group_of(y: float) -> int:
        return 0 if group_band is None else sum(1 for c in cuts if c <= y + 1)

    rows = []
    for w in by_band[unit_band]:
        if w["top"] <= head_bottom + 2:
            continue
        y = w["top"]

        def near(b: int, y: float = y) -> list[dict[str, Any]]:
            return [x for x in by_band[b] if abs(x["top"] - y) <= 7]

        name = _name(" ".join(x["text"] for x in sorted(near(name_band), key=lambda x: x["x0"])))
        if not name:
            continue
        qty = {b: _num(_join(near(b)).replace(" ", "")) for b in q_bands}
        rows.append({"y": y, "name": name, "unit": w["text"], "qty": qty, "group": group_of(y)})
    if not rows:
        return None

    def group_label(g: int) -> str:
        if group_band is None:
            return ""
        ys = [r["y"] for r in rows if r["group"] == g]
        lo, hi = min(ys) - 8, max(ys) + 8
        ws = [w for w in by_band[group_band] if lo <= w["top"] <= hi and w["top"] > data_top]
        return _collapse(_join(ws)).strip()

    def daily(g: int, b: int) -> Decimal | None:
        ys = [r["y"] for r in rows if r["group"] == g]
        lo, hi = min(ys) - 12, max(ys) + 12
        ws = [w for w in by_band[b] if lo <= w["top"] <= hi and w["top"] > data_top]
        return _num(_join(ws).replace(" ", ""))

    variants = []
    for g in sorted({r["group"] for r in rows}):
        gl = group_label(g)
        for qb in q_bands if len(q_bands) > 1 else q_bands[:1]:
            for db in d_bands or [-1]:
                parts = [
                    gl,
                    q_label[qb] if len(q_bands) > 1 else "",
                    d_label[db] if db >= 0 and len(d_bands) > 1 else "",
                ]
                got = [
                    (r["name"], r["unit"], r["qty"][qb])
                    for r in rows
                    if r["group"] == g and r["qty"].get(qb) is not None
                ]
                if got:
                    variants.append(
                        {
                            "label": " / ".join(p for p in parts if p),
                            "daily": daily(g, db) if db >= 0 else None,
                            "rows": got,
                        }
                    )
    return {
        "daily_unit": daily_unit,
        "variants": variants,
        "last_y": max(r["y"] for r in rows),
        "bottom": bottom,
    }


def _page_no(lines: list[dict[str, Any]]) -> int | None:
    for ln in reversed(lines):
        t = ln["text"].strip()
        if FOOTER.match(t):
            m = re.search(r"(\d{3})(?!\d)", t)
            if m:
                return int(m[1])
    return None


def extract_sections(pdf: Any, page_from: int, page_to: int, ctx: dict[str, Any], d: _Draft) -> int:
    """Read the pages into `d`; returns how many items were made (0: not a sectioned 품셈 PDF)."""
    last = min(page_to, len(pdf.pages))
    part = ctx.get("part")
    if part is None:  # the 부문 is printed in the footer of every other page
        for no in range(page_from, last + 1):
            for ln in pdf.pages[no - 1].extract_text_lines():
                if m := re.fullmatch(
                    r"\d{3}\s+(건축|공통|토목|기계설비|유지관리)부문", ln["text"].strip()
                ):
                    part = PART[m[1]]
                    break
            if part:
                break
    section: dict[str, Any] | None = None  # the last section header seen (carries over pages)
    made = 0
    seen: dict[str, int] = {}
    made_by: dict[str, list[int]] = {}  # section code -> indexes of its items in d.items
    for no in range(page_from, last + 1):
        page = pdf.pages[no - 1]
        lines = page.extract_text_lines()
        book_page = _page_no(lines)
        heads = []
        for ln in lines:
            if m := SECTION.match(ln["text"].strip()):
                heads.append(
                    {"code": m[1], "title": m[2].strip(" ·"), "top": ln["top"], "page": no}
                )
        tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
        foot = [ln["top"] for ln in lines if FOOTER.match(ln["text"].strip())]
        spans = [(t.bbox[1] - 2, t.bbox[3] + 2) for t in tables]
        if section is not None:  # notes at the top of the page still belong to the section before
            stop = min(
                [h["top"] for h in heads] + [t.bbox[1] for t in tables] + foot + [page.height]
            )
            carry = " ".join(
                ln["text"].strip()
                for ln in lines
                if ln["top"] < stop
                and not any(lo <= ln["top"] <= hi for lo, hi in spans)
                and not FOOTER.match(ln["text"].strip())
            )
            if carry.strip():
                _append_note(d, made_by.get(section["code"], []), carry)
        prev_bottom = 0.0
        for t in tables:
            above = [h for h in heads if h["top"] < t.bbox[1]]
            cur = above[-1] if above else section
            floor = max(prev_bottom, above[-1]["top"] if above else 0.0)
            prev_bottom = t.bbox[3]
            if cur is None:
                continue
            res = parse_table(page, t)
            if res is None or not res["variants"]:
                continue
            nxt = [h["top"] for h in heads if h["top"] > t.bbox[1]]
            end = min(nxt + foot + [page.height])
            note = " ".join(
                ln["text"].strip()
                for ln in lines
                if res["last_y"] + 6 < ln["top"] < end
                and not FOOTER.match(ln["text"].strip())
                and not HEADING2.match(ln["text"].strip())
            )
            unit_line = " ".join(ln["text"] for ln in lines if floor < ln["top"] < t.bbox[1])
            idx = _emit(d, ctx, part or "X", cur, res, unit_line, note, book_page or no, no, seen)
            made_by.setdefault(cur["code"], []).extend(idx)
            made += len(idx)
        if heads:
            section = heads[-1]
    return made


def _percent_rows(code: str, note: str, label: str, d: _Draft, n: int) -> list[list[Any]]:
    """ "공구손료 및 경장비의 기계경비는 인력품의 N%" and a 요율 table (인력마감 - / 기계마감 9%)."""
    out: list[list[Any]] = []
    for m in NOTE_PCT.finditer(note):
        rcode = f"PCT-{len(d.rows) + n + len(out) + 1:04d}"
        flag = NOTE + f"주석 '{m[1].strip()} 기계경비는 인력품의 {m[2]}%'에서 읽음: 원문 확인"
        out.append([code, KIND_KO["EQUIPMENT"], rcode, float(m[2]), flag, "노무"])
    rate = re.search(r"구\s*분\s+(\S+)\s+(\S+)\s+요\s*율\s+(\S+)\s+(\S+)", note)
    if rate and label:
        pct = {rate[1]: rate[3], rate[2]: rate[4]}.get(label.split(" / ")[-1], "")
        if re.fullmatch(r"\d+(?:\.\d+)?%", pct):
            rcode = f"PCT-{len(d.rows) + n + len(out) + 1:04d}"
            flag = NOTE + f"요율표 '{label}' {pct}에서 읽음: 원문 확인"
            out.append([code, KIND_KO["EQUIPMENT"], rcode, float(pct[:-1]), flag, "노무"])
    return out


def _append_note(d: _Draft, idxs: list[int], text: str) -> None:
    """Page-top notes that belong to the sections made on the page before."""
    for i in idxs:
        item = d.items[i]
        item[14] = (((item[14] or "") + " " + re.sub(r"\s+", " ", text)).strip())[:MAX_REMARK]
        d.rows += _percent_rows(item[0], text, "", d, 0)


def _emit(
    d: _Draft,
    ctx: dict[str, Any],
    part: str,
    sec: dict[str, Any],
    res: dict[str, Any],
    unit_line: str,
    note: str,
    page_no: int,
    pdf_page: int,
    seen: dict[str, int],
) -> list[int]:
    unit, scale = heading_unit(unit_line)
    base = f"{part}-{sec['code']}"
    remark = re.sub(r"\s+", " ", note).strip()[:MAX_REMARK] or None
    made: list[int] = []
    for v in res["variants"]:
        n = seen.get(base, 0)
        seen[base] = n + 1
        code = base if n == 0 else f"{base}.{n}"
        daily = v["daily"]
        item_unit = res["daily_unit"] if daily is not None else unit
        flags: list[str] = []
        if daily is None and res["daily_unit"]:
            flags.append("시공량을 읽지 못함(일당 표)")
        if item_unit is None:
            flags.append("기준 단위('~당' 또는 시공량 단위)를 찾지 못함")
        if daily is None and scale != 1 and unit:
            flags.append(f"원문은 {scale.normalize():f}{unit}당 → 1{unit}당으로 환산")
        seen_names: set[str] = set()
        out: list[list[Any]] = []
        for name, u, q in v["rows"]:
            qty = q if daily is not None else q / scale
            rflags: list[str] = []
            kind = (
                "LABOR" if u == "인" else "EQUIPMENT" if u in ("대", "시간", "hr") else "MATERIAL"
            )
            if _unit(u) is None and u not in UNITS:
                rflags.append(f"단위 '{u}' 확인")
            if qty <= 0 or qty > MAX_QTY:
                rflags.append("투입량이 0 이하이거나 너무 큼")
            if name in seen_names:
                rflags.append("같은 자원이 한 표에 두 번")
            seen_names.add(name)
            rcode = d.code(kind, name)
            d.prices.setdefault((kind, name), [rcode, KIND_KO[kind], name, _unit(u) or u, None])
            out.append(
                [
                    code,
                    KIND_KO[kind],
                    rcode,
                    float(qty),
                    NOTE + "; ".join(rflags) if rflags else None,
                    None,
                ]
            )
            for f in rflags:
                d.issues.append((pdf_page, code, name, f))
        pct = _percent_rows(code, note, v["label"], d, len(out))
        if "다음 요율" in note and not any("요율표" in (r[4] or "") for r in pct):
            flags.append("주석에 요율표가 있음: 원문 확인")
        for f in flags:
            d.issues.append((pdf_page, code, "-", f))
        d.rows += out + pct
        d.items.append(
            [
                code,
                sec["title"][:80],
                v["label"] or None,
                item_unit or "?",
                None,
                ctx["notice"],
                ctx["effective"],
                page_no,
            ]
            + [ctx["sha"], "PDF", None, None, NOTE + "; ".join(flags) if flags else None]
            + [None if daily is None else float(daily), remark]
        )
        made.append(len(d.items) - 1)
    return made
