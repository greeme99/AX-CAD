"""Track 8 P6-6: 시간당 기계경비 from the 건설기계 경비산출표 (PDF) + fuel prices + 운전사 노임.

표준품셈 8-1·8-4: 기계경비 = 기계손료 + 운전경비,
  운전경비 = 주연료비 + 잡재료비 + 운전 노무비
  주연료비 = Σ 주연료(ℓ/hr) × 연료 단가         (연료 표시가 없으면 경유, 휘발유·중유는 칸에 적힘)
  잡재료비 = 주연료비 × 잡재료(주연료의 %)
  운전 노무비 = 조종원(인/일) × 운전사 노임(원/일) ÷ 1일 8시간
The table has the 손료 and the quantities but no fuel prices (they move), so the prices come from the
person. 운전사 구분 (건설기계 / 화물차 / 일반기계운전사) follows the 8-1 list by the machine's name;
the rule is a keyword match and is written into each row's source so it can be checked.
A row is skipped, never guessed, when a price it needs is missing (중유, 전력, 조수·조장 노임)."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

D = Decimal
ROW = re.compile(
    r"(\d{4}-\d{4})\s+(\S.*?)\s+([\d,]+)\s+([\d,]+)\s+(.+?)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$"
)
CODE = re.compile(r"\d{4}-\d{4}")
OPERATOR = {  # 8-1 5. 운전사의 구분 (키워드로 기종 이름에 대응; 확인 필요)
    "화물차운전사": ("살수차", "트랙터", "제설차", "노면청소차", "트럭탑재", "화물트럭"),
    "일반기계운전사": (
        "양수기", "윈치", "벨트컨베이어", "발전기", "래머", "콤팩", "콤펙", "콘크리트파쇄", "그라우트",
    ),
}  # fmt: skip
WAGE_CODE = {"건설기계운전사": "L-1048", "화물차운전사": "L-1049", "일반기계운전사": "L-1050"}
MAX_ROWS = 2000


class MachineCostError(ValueError):
    """The file is not a 경비산출표 (shown to the user as is)."""


def _num(s: str) -> Decimal | None:
    try:
        return D(s.replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def operator_kind(name: str, spec: str) -> str:
    for kind, words in OPERATOR.items():
        if any(w in name for w in words):
            return kind
    if "덤프트럭" in name:  # 12ton 이상은 건설기계운전사, 미만은 화물차운전사
        m = re.match(r"([\d.]+)", spec)
        if m and (t := _num(m[1])) is not None and t < 12:
            return "화물차운전사"
    return "건설기계운전사"


def _fuel(text: str, last: str) -> tuple[list[tuple[str, Decimal]], str, str | None]:
    """'19.5' / '휘발유0.7' / '중유487.2' / '〃 614.7' / '73.7+휘발유54.5' -> ([(fuel, ℓ/hr)], fuel kept for 〃, problem)."""
    t = text.strip()
    if t in ("-", ""):
        return [], last, None
    if "㎾" in t:
        return [], last, "전력(㎾) 기계: 전력 단가 필요"
    out: list[tuple[str, Decimal]] = []
    for part in t.split("+"):
        m = re.fullmatch(r"(휘발유|중유|〃)?\s*([\d.]+)", part.strip())
        if not m or (q := _num(m[2])) is None:
            return [], last, f"연료칸을 읽지 못함('{text}')"
        kind = {"휘발유": "휘발유", "중유": "중유", "〃": last}.get(m[1] or "", "경유")
        out.append((kind, q))
        last = kind
    return out, last, None


def parse_machine_pdf(path: str) -> dict[str, Any]:
    import pdfplumber

    rows: list[dict[str, Any]] = []
    skipped = 0
    names: dict[str, list[str]] = {}
    last_fuel = "경유"
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").split("\n"):
                hit = CODE.search(line)
                if not hit:
                    continue
                group = hit[0][:4]
                pre = re.sub(r"^\d{4}\s*", "", line[: hit.start()]).strip()
                if pre:
                    names.setdefault(group, []).append(pre)
                r = ROW.search(line)
                if not r or "별" in line:
                    skipped += 1
                    continue
                fuels, last_fuel, problem = _fuel(r[5], last_fuel)
                tail = [r[6], r[7], r[8], r[9]]  # 잡재료%, 조종원, 조수, 조장
                rows.append(
                    {
                        "code": r[1],
                        "group": group,
                        "spec": r[2].strip(),
                        "price_k": _num(r[3]),
                        "wear": _num(r[4]),
                        "fuels": fuels,
                        "misc": _num(tail[0]) or D(0),
                        "operator": _num(tail[1]) or D(0),
                        "assistant": _num(tail[2]) or D(0),
                        "foreman": _num(tail[3]) or D(0),
                        "problem": problem,
                    }
                )
                if len(rows) > MAX_ROWS:
                    raise MachineCostError(f"기계가 {MAX_ROWS}행을 넘습니다")
    if not rows:
        raise MachineCostError("경비산출표의 기계 행을 찾지 못했습니다")
    for row in rows:
        row["name"] = " ".join(names.get(row["group"], [])) or row["group"]
    return {"rows": rows, "skipped_lines": skipped}


def compute(
    rows: list[dict[str, Any]],
    fuel_price: dict[str, Decimal],
    wage: dict[str, Decimal],
    price_date: str,
    hours: Decimal | None = None,
) -> dict[str, Any]:
    """-> {rows: price rows (EQUIPMENT, 원/시간), skipped: [reason]}."""
    hours = hours or D(8)
    out: list[dict[str, Any]] = []
    skipped: list[str] = []
    for r in rows:
        who = f"{r['code']} {r['name']} {r['spec']}".strip()
        if r["problem"]:
            skipped.append(f"{who}: {r['problem']}")
            continue
        if r["wear"] is None:
            skipped.append(f"{who}: 시간당 손료를 읽지 못함")
            continue
        if r["assistant"] or r["foreman"]:
            skipped.append(f"{who}: 조수·조장 노임 기준이 없어 제외")
            continue
        missing = [k for k, _ in r["fuels"] if k not in fuel_price]
        if missing:
            skipped.append(f"{who}: {missing[0]} 단가 없음")
            continue
        kind = operator_kind(r["name"], r["spec"])
        if r["operator"] and kind not in wage:
            skipped.append(f"{who}: {kind} 노임 없음")
            continue
        fuel = sum((q * fuel_price[k] for k, q in r["fuels"]), D(0))
        misc = fuel * r["misc"] / 100
        labor = r["operator"] * wage[kind] / hours if r["operator"] else D(0)
        total = (r["wear"] + fuel + misc + labor).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        bits = [f"손료{r['wear']:,.0f}"]
        if fuel:
            bits.append(f"연료{fuel:,.0f}+잡재{misc:,.0f}")
        if labor:
            bits.append(f"{kind[:4]} {labor:,.0f}")
        out.append(
            {
                "resource_code": f"E-{r['code']}",
                "resource_type": "EQUIPMENT",
                "resource_name": f"{r['name']} {r['spec']}"[:100],
                "unit": "시간",
                "unit_price": str(total),
                "price_date": price_date,
                "source": ("경비산출표 2026: " + " ".join(bits))[:100],
            }
        )
    return {"rows": out, "skipped": skipped}


def rows_to_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decimals as text, so the isolated worker can hand the rows back as JSON."""
    out: list[dict[str, Any]] = []
    for r in rows:
        d = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()}
        d["fuels"] = [[k, str(q)] for k, q in r["fuels"]]
        out.append(d)
    return out


def rows_from_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    num = ("price_k", "wear", "misc", "operator", "assistant", "foreman")
    out = []
    for r in rows:
        d = dict(r)
        for k in num:
            d[k] = None if r.get(k) is None else D(str(r[k]))
        d["fuels"] = [(k, D(str(q))) for k, q in r["fuels"]]
        out.append(d)
    return out
