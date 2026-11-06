"""Track 8 P6-6: 자원단가 sources -> resource price rows for a master data draft.

Three inputs, each read in an isolated worker (the files come from outside):
- 노임 PDF: 대한건설협회 「건설업 임금실태 조사 보고서(시중노임단가)」, the 개별 직종 노임 단가 table
  (`1002 보 통 인 부 172,698 …`): code `L-<직종번호>`, 원/인 (1일 8시간), the newest 공표일 column.
- 자재 엑셀: 조달청 시설공통자재 가격정보 (`NO 자재구분 … 가격 관할지역`): code `M-<물품식별번호>`.
- 직접 목록 CSV: 코드, 구분, 이름, 단위, 단가, 기준일, 출처 (a few prices from elsewhere, e.g. a 물가동향 report).
Every row keeps its source and date; nothing is guessed (a 직종 not surveyed this time is skipped and listed)."""

import csv
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from core.quote_engine.costbook_extract import _name

RESOURCE_TYPES = {"노무": "LABOR", "재료": "MATERIAL", "장비": "EQUIPMENT"}
LABOR_ROW = re.compile(r"^(\*{0,2})(\d{4})\s+(.+?)\s+((?:[\d,]+|-)(?:\s+(?:[\d,]+|-)){0,3})$")
LABOR_DATE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")
MAX_ROWS = 20_000
MAX_SKIPPED = 200


class PriceImportError(ValueError):
    """The file is not what it was said to be (shown to the user as is)."""


def _money(v: Any) -> str | None:
    s = str(v).replace(",", "").strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return None if d < 0 or not d.is_finite() else format(d.normalize(), "f")


def parse_labor_pdf(path: str) -> dict[str, Any]:
    """-> {rows, skipped}. Only the first (newest) column of the 개별직종 노임단가 table is used."""
    import pdfplumber

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    day: str | None = None
    in_table = False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").split("\n"):
                line = line.strip()
                if "개별직종" in line.replace(" ", "") and "·" not in line:  # not the 목차 line
                    in_table, day = True, None
                if not in_table:
                    continue
                if (m := LABOR_DATE.fullmatch(line.split(" ")[0])) and len(line.split()) >= 2:
                    day = date(int(m[1]), int(m[2]), int(m[3])).isoformat()  # the newest 공표일
                    continue
                m = LABOR_ROW.match(line)
                if not m or day is None:
                    continue
                mark, no, name, cols = m[1], m[2], re.sub(r"\s+", " ", m[3]).strip(), m[4].split()
                name = _name(name)
                price = _money(cols[0])
                if price is None:  # "-": not surveyed on that date
                    skipped.append(f"{no} {name}: {day} 조사 안 됨")
                    continue
                rows.append(
                    {
                        "resource_code": f"L-{no}",
                        "resource_type": "LABOR",
                        "resource_name": name,
                        "unit": "인",
                        "unit_price": price,
                        "price_date": day,
                        "source": "대한건설협회 시중노임단가 "
                        + day.replace("-", ".")
                        + (" (조사 현장 5개 미만)" if mark == "*" else ""),
                    }
                )
    if not rows:
        raise PriceImportError("노임 표(개별 직종 노임 단가)를 찾지 못했습니다")
    return {"rows": rows, "skipped": skipped}


def _get(r: tuple[Any, ...], idx: dict[str, int], key: str) -> str:
    i = idx.get(key)
    return str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ""


def parse_materials_xlsx(path: str) -> dict[str, Any]:
    """조달청 시설공통자재 가격정보 -> rows (가격은 부가세 별도)."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    head: list[str] | None = None
    idx: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for r in ws.iter_rows(values_only=True):
        if head is None:
            if r and r[0] == "NO":
                head = [str(h or "").strip() for h in r]
                idx = {h: i for i, h in enumerate(head)}
                need = ("물품식별번호", "품명", "규격", "단위", "가격", "게시일자")
                if any(k not in idx for k in need):
                    raise PriceImportError("시설공통자재 가격정보 양식이 아닙니다(열 이름 불일치)")
            continue
        if not r or r[0] is None:
            continue
        code, name, spec = _get(r, idx, "물품식별번호"), _get(r, idx, "품명"), _get(r, idx, "규격")
        price = _money(_get(r, idx, "가격"))
        day = _get(r, idx, "게시일자")[:10]
        if not (code and re.fullmatch(r"[A-Za-z0-9._\-]{1,36}", code)) or price is None:
            skipped.append(f"{code or r[0]}: 코드 또는 가격이 올바르지 않음")
            continue
        label = spec if spec.startswith(name) else f"{name} {spec}".strip()
        unit = _get(r, idx, "단위")[:20] or "-"
        src = "조달청 시설공통자재 가격정보 " + day[:7]
        extra = ", ".join(
            x for k in ("자재구분", "인도조건", "부가세여부") if (x := _get(r, idx, k))
        )
        rows.append(
            {
                "resource_code": f"M-{code}",
                "resource_type": "MATERIAL",
                "resource_name": label[:100],
                "unit": unit,
                "unit_price": price,
                "price_date": day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None,
                "source": f"{src} ({extra})"[:100],
            }
        )
        if len(rows) > MAX_ROWS:
            raise PriceImportError(f"행이 {MAX_ROWS}개를 넘습니다")
    if not rows:
        raise PriceImportError("자재 가격 행을 찾지 못했습니다")
    return {"rows": rows, "skipped": skipped}


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise PriceImportError("CSV 문자 인코딩을 읽을 수 없습니다(UTF-8 또는 CP949)")


def parse_manual_csv(path: str) -> dict[str, Any]:
    """코드, 구분(노무/재료/장비), 이름, 단위, 단가, 기준일, 출처 -> rows."""
    with open(path, "rb") as f:
        text = _decode(f.read(2 * 1024**2))
    rd = csv.reader(text.splitlines())
    head = next(rd, None)
    want = ["코드", "구분", "이름", "단위", "단가", "기준일", "출처"]
    if not head or [h.strip() for h in head[:7]] != want:
        raise PriceImportError(f"CSV 첫 줄이 {', '.join(want)} 이어야 합니다")
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for n, r in enumerate(rd, 2):
        if not any(c.strip() for c in r):
            continue
        c = [x.strip() for x in (r + [""] * 7)[:7]]
        kind, price = RESOURCE_TYPES.get(c[1]), _money(c[4])
        if not (re.fullmatch(r"[A-Za-z0-9._\-]{1,40}", c[0]) and kind and c[2] and c[3] and price):
            skipped.append(f"{n}행: 코드·구분(노무/재료/장비)·이름·단위·단가를 확인하세요")
            continue
        if c[5] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", c[5]):
            skipped.append(f"{n}행: 기준일은 YYYY-MM-DD 형식입니다")
            continue
        rows.append(
            {
                "resource_code": c[0],
                "resource_type": kind,
                "resource_name": c[2][:100],
                "unit": c[3][:20],
                "unit_price": price,
                "price_date": c[5] or None,
                "source": c[6][:100] or None,
            }
        )
    if not rows:
        raise PriceImportError("가져올 행이 없습니다")
    return {"rows": rows, "skipped": skipped}


PARSERS = {"labor": parse_labor_pdf, "materials": parse_materials_xlsx, "manual": parse_manual_csv}


def import_job(kind: str, path: str) -> str:
    """Worker entry (run_isolated): JSON text {rows, skipped} or {error}."""
    try:
        out = PARSERS[kind](path)
        out["skipped"] = out["skipped"][:MAX_SKIPPED]
        return json.dumps(out, ensure_ascii=False)
    except PriceImportError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
