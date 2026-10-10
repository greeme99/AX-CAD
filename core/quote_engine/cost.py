"""FN-17 Korean standard cost quote (pure: metrics + master-data bundle in, lines + totals out).

  직접재료비 = 정미중량(kg) × (1 + 스크랩률) × 수량 × 원/kg
  직접노무비 = Σ 표준공수(h) × 임률
  제조간접비 = Σ 기계시간(h) × 기계경비   (overhead_basis MACHINE_HOUR)
             | 직접노무비 × 제조간접비율  (overhead_basis LABOR_RATIO)
  외주가공비 = 노무/기계 단가가 없는 공정: 공식 결과(원/개) × 수량
  순제조원가 = 재료비 + 노무비 + 간접비 + 외주비
  일반관리비 = 순제조원가 × 일반관리비율 → 총원가
  이윤      = 총원가 × 이윤율 → 공급가액 → VAT → 합계

Every line carries traces: source (drawing handles / 3D features) -> rule -> price -> formula + inputs.
All money is Decimal; nothing passes through float."""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from core.quote_engine.formula import FormulaError, evaluate

ROUNDING = {"FLOOR": ROUND_FLOOR, "HALF_UP": ROUND_HALF_UP, "CEILING": ROUND_CEILING}
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1e13")  # per line and per total: numeric(18,2) holds < 1e16 with headroom
MAX_QTY = Decimal("1e11")  # numeric(18,6)
QTY_STEP = Decimal("0.000001")
MAX_TOTAL = Decimal("1e15")  # header columns are numeric(18,2)
MAX_SOURCES = 500  # handles listed per trace (the count is always complete)
# which drawing items explain a metric (FN-18 highlight)
ROLES = {
    "cutting_length_mm": ("CUT", "HOLE"),
    "hole_count": ("HOLE", "PUNCH"),
    "punch_hole_count": ("PUNCH",),
    "bend_count": ("BEND",),
    "bend_length_mm": ("BEND",),
    "net_area_mm2": ("CUT", "HOLE", "PUNCH"),
}
# group -> (metrics that need pricing, metrics a rule may use to price them); else RULE_NOT_MAPPED.
# Laser-cut holes are already in cutting_length_mm, so only punched holes need their own rule.
GROUPS = {
    "절단": (("cutting_length_mm",), ("cutting_length_mm",)),
    "펀칭 구멍": (("punch_hole_count",), ("hole_count", "punch_hole_count")),
    "절곡": (("bend_count", "bend_length_mm"), ("bend_count", "bend_length_mm")),
    "표면처리": (("surface_area_mm2",), ("surface_area_mm2",)),
}
D = Decimal


def _d(v: Any) -> Decimal | None:
    return None if v is None else D(str(v))


def round_amount(v: Decimal, rule: str, unit: int) -> Decimal:
    # settle division residue first: 4 min / 60 * 4 * 30000 must stay 8000, not 8000.000...01
    # (which CEILING would turn into 8010)
    v = v.quantize(D("0.0001"), rounding=ROUND_HALF_UP)
    u = D(unit)
    return ((v / u).quantize(D(1), rounding=ROUNDING[rule]) * u).quantize(CENT)


class Quote:
    def __init__(self, ratios: dict[str, Any]):
        self.rule, self.unit = ratios["rounding_rule"], int(ratios["rounding_unit"])
        self.per_line = ratios["rounding_scope"] == "LINE"
        self.lines: list[dict[str, Any]] = []
        self.logs: list[dict[str, str]] = []

    def log(self, severity: str, code: str, message: str) -> None:
        self.logs.append({"severity": severity, "code": code, "message": message})

    def money(self, v: Decimal) -> Decimal:
        """Line-level rounding when the scope is LINE, else exact (kept to the cent for storage)."""
        return round_amount(v, self.rule, self.unit) if self.per_line else v

    def line(
        self,
        category: str,
        code: str,
        name: str,
        unit: str,
        qty: Decimal,
        price: Decimal | None,
        traces: list[dict[str, Any]],
    ) -> None:
        if not (abs(qty) < MAX_QTY and (price is None or abs(price) < MAX_AMOUNT)):
            # out of numeric(18,6)/(18,2) range: a data error, never a 500 (security review M1)
            return self.log(
                "ERROR", "QUOTE_AMOUNT_OVERFLOW", f"{name}: 수량 또는 단가가 비정상적으로 큽니다"
            )
        stored = qty.quantize(QTY_STEP, rounding=ROUND_HALF_UP)  # display/storage only
        if stored <= 0:
            return self.log(
                "WARN", "QUOTE_ZERO_QTY", f"{name}: 수량이 0에 가까워 라인을 만들지 않았습니다"
            )
        # amount from the exact quantity: 0.266667 h x 30000 is 8000.01, which CEILING makes 8010
        amount = None if price is None else self.money(qty * price)
        qty = stored
        excluded = price is None
        if price is None:
            self.log(
                "ERROR", "QUOTE_PRICE_MISSING", f"{name}: 단가({code})가 없어 합계에서 제외했습니다"
            )
        elif amount is not None and abs(amount) >= MAX_AMOUNT:
            self.log("ERROR", "QUOTE_AMOUNT_OVERFLOW", f"{name}: 금액이 비정상적으로 큽니다")
            amount, excluded = None, True
        self.lines.append(
            {
                "line_no": len(self.lines) + 1,
                "cost_category": category,
                "item_code": code,
                "item_name": name,
                "unit": unit,
                "calculated_qty": qty,
                "calculated_unit_price": price,
                "calculated_amount": amount,
                "excluded": excluded,
                "traces": traces,
            }
        )

    def total(self, category: str) -> Decimal:
        return sum(
            (
                ln["calculated_amount"]
                for ln in self.lines
                if ln["cost_category"] == category and not ln["excluded"]
            ),
            D(0),
        )


def _sources(metrics: dict[str, Any], metric: str | None, source: dict[str, Any]) -> dict[str, Any]:
    """Trace source block for a metric: the drawing handles behind it, or the 3D bodies.
    The list is capped, source_count is always the full number."""
    if source["kind"] == "DOCUMENT_3D":
        refs = [str(b["feature_id"]) for b in metrics.get("bodies", [])]
        kind, rev = "FEATURE", None
    else:
        roles = ROLES.get(metric or "", ())
        refs = sorted({i["handle"] for i in metrics.get("items", []) if i["role"] in roles})
        kind, rev = "ENTITY", source["revision_id"]
        if not refs:  # part_qty, thickness...: the revision as a whole
            kind, refs = "REVISION", [source["revision_id"]]
    return {
        "source_kind": kind,
        "sources": refs[:MAX_SOURCES],
        "source_count": len(refs),
        "revision_id": rev,
    }


def _env(
    metrics: dict[str, Any], source: dict[str, Any], qty: int, thickness: Decimal | None
) -> dict[str, Decimal]:
    """Numeric inputs a formula may read."""
    if source["kind"] == "DOCUMENT_3D":
        bodies = metrics.get("bodies", [])
        env = {
            "volume_mm3": sum((D(str(b["volume_mm3"])) for b in bodies), D(0)),
            "surface_area_mm2": sum((D(str(b["surface_area_mm2"])) for b in bodies), D(0)),
        }
    else:
        keys = (
            "cutting_length_mm",
            "hole_count",
            "punch_hole_count",
            "bend_count",
            "bend_length_mm",
            "net_area_mm2",
        )
        env = {k: D(str(metrics[k])) for k in keys if metrics.get(k) is not None}
    env["part_qty"] = D(qty)
    if thickness is not None:
        env["thickness_mm"] = thickness
    return env


def _material(
    q: Quote,
    bundle: dict[str, Any],
    metrics: dict[str, Any],
    source: dict[str, Any],
    code: str | None,
    thickness: Decimal | None,
    qty: int,
    basis: str,
) -> None:
    if not code:
        return q.log("ERROR", "QUOTE_MATERIAL_REQUIRED", "재질을 지정하세요 (표제란에서 읽지 못함)")
    rows = [m for m in bundle["materials"] if m["material_code"] == code]
    if thickness is not None:
        rows = [
            m
            for m in rows
            if (m["thickness_min_mm"] is None or D(m["thickness_min_mm"]) <= thickness)
            and (m["thickness_max_mm"] is None or thickness <= D(m["thickness_max_mm"]))
        ]
    if not rows:
        return q.log(
            "ERROR",
            "QUOTE_MATERIAL_UNKNOWN",
            f"기준정보에 재질 {code}{'' if thickness is None else f' {thickness}t'}가 없습니다",
        )
    if len(rows) > 1:  # overlapping thickness ranges or a 3D quote over several plate rows
        q.log(
            "WARN",
            "QUOTE_MATERIAL_AMBIGUOUS",
            f"재질 {code} 단가 행이 {len(rows)}개라 첫 행을 썼습니다",
        )
    m = rows[0]
    if source["kind"] == "DOCUMENT_3D":
        bodies = metrics.get("bodies", [])
        if basis == "BBOX":
            vol = sum(
                (
                    D(str(b["bbox"]["size"][0]))
                    * D(str(b["bbox"]["size"][1]))
                    * D(str(b["bbox"]["size"][2]))
                    for b in bodies
                ),
                D(0),
            )
        else:
            vol = sum((D(str(b["volume_mm3"])) for b in bodies), D(0))
        metric = "volume_mm3"
    else:
        if thickness is None:
            return q.log(
                "ERROR", "QUOTE_THICKNESS_REQUIRED", "판재 두께를 지정하세요 (표제란에서 읽지 못함)"
            )
        if basis == "BBOX":
            if not metrics.get("bbox"):
                return q.log(
                    "ERROR",
                    "QUOTE_METRIC_MISSING",
                    "외곽 윤곽이 없어 소재 크기를 계산할 수 없습니다",
                )
            sx, sy = metrics["bbox"]["size"]
            area = D(str(sx)) * D(str(sy))
        else:
            if metrics.get("net_area_mm2") is None:
                return q.log(
                    "ERROR", "QUOTE_METRIC_MISSING", "외곽 윤곽이 없어 순면적을 계산할 수 없습니다"
                )
            area = D(str(metrics["net_area_mm2"]))
        vol, metric = area * thickness, "net_area_mm2"
    density = D(m["density_g_cm3"])
    scrap = _d(m["scrap_rate"]) or D(0)
    kg_each = vol * density / D(1_000_000)  # mm³ × g/cm³ = mm³ × 1e-3 g/mm³ -> kg
    kg = kg_each * (1 + scrap) * qty
    if kg <= 0:
        return q.log("WARN", "QUOTE_ZERO_QTY", "재료 중량이 0입니다")
    inputs = {
        "volume_mm3": str(vol),
        "density_g_cm3": str(density),
        "scrap_rate": str(scrap),
        "qty": qty,
        "basis": basis,
    }
    trace = {
        "rule_code": f"MATERIAL:{basis}",
        "price_item_code": code,
        "unit_price": m["unit_price_per_kg"],
        "inputs": inputs,
        "formula_text": "volume_mm3 × density / 1e6 × (1 + scrap_rate) × qty",
    }
    q.line(
        "MATERIAL",
        code,
        f"{code} 소재",
        "kg",
        kg,
        _d(m["unit_price_per_kg"]),
        [{**_sources(metrics, metric, source), **trace}],
    )


def compute_quote(
    metrics: dict[str, Any],
    bundle: dict[str, Any],
    source: dict[str, Any],
    *,
    qty: int,
    material_code: str | None,
    thickness_mm: Decimal | None,
) -> dict[str, Any]:
    """source: {"kind": "REVISION", "revision_id"} (2D metrics) or {"kind": "DOCUMENT_3D", "document_id"}."""
    ratios = bundle["cost_ratios"]
    q = Quote(ratios)
    basis = ratios.get("material_basis", "NET")
    _material(q, bundle, metrics, source, material_code, thickness_mm, qty, basis)

    prices = {p["item_code"]: p for p in bundle["price_items"]}
    env = _env(metrics, source, qty, thickness_mm)
    machine_hour = ratios["overhead_basis"] == "MACHINE_HOUR"
    for rule in bundle["process_rules"]:
        metric, name = rule["input_metric"], rule["process_name"] or rule["process_code"]
        if metric not in env:
            q.log("INFO", "RULE_NOT_APPLICABLE", f"{name}: 이 도면에는 {metric} 값이 없습니다")
            continue
        if env[metric] <= 0:
            q.log("WARN", "QUOTE_ZERO_QTY", f"{name}: {metric} = 0 이라 라인을 만들지 않았습니다")
            continue
        try:
            value = evaluate(rule["formula_text"], {**env, **rule["params"]})
        except FormulaError as e:
            q.log("ERROR", "RULE_FORMULA_ERROR", f"{name}: {e}")
            continue
        if value <= 0:
            q.log("WARN", "QUOTE_ZERO_QTY", f"{name}: 계산 결과가 0 이하입니다")
            continue
        inputs = {k: str(v) for k, v in {**env, **rule["params"]}.items()}
        base = {
            "rule_code": rule["rule_code"],
            "inputs": inputs,
            "formula_text": rule["formula_text"],
        }
        sources = _sources(metrics, metric, source)

        def priced(
            code: str,
            category: str,
            label: str,
            unit: str,
            qty_: Decimal,
            base: dict[str, Any] = base,
            sources: dict[str, Any] = sources,
        ) -> None:
            item = prices.get(code)
            price = _d(item["unit_price"]) if item else None
            trace = {
                **base,
                "price_item_code": code,
                "unit_price": None if price is None else str(price),
            }
            q.line(category, code, label, unit, qty_, price, [{**sources, **trace}])

        hours = value / 60 * qty  # formula: standard minutes per piece
        if rule["labor_item_code"]:
            priced(rule["labor_item_code"], "LABOR", f"{name} 노무", "h", hours)
        if rule["machine_item_code"] and machine_hour:
            priced(rule["machine_item_code"], "OVERHEAD", f"{name} 기계경비", "h", hours)
        if not rule["labor_item_code"] and not rule["machine_item_code"]:
            # no time-based price: the formula yields won per piece (e.g. painting by area)
            trace = {**base, "price_item_code": None, "unit_price": str(value)}
            q.line(
                "OUTSOURCE",
                rule["rule_code"],
                f"{name} 외주",
                "EA",
                D(qty),
                value,
                [{**sources, **trace}],
            )

    mapped = {r["input_metric"] for r in bundle["process_rules"]}
    for group, (need, by) in GROUPS.items():
        if any(env.get(k, 0) > 0 for k in need) and not mapped & set(by):
            q.log(
                "WARN", "RULE_NOT_MAPPED", f"{group} 메트릭이 있지만 이를 쓰는 공정 규칙이 없습니다"
            )

    labor = q.total("LABOR")
    if not machine_hour and labor > 0:
        rate = _d(ratios.get("overhead_rate"))
        labor_lines = [
            ln for ln in q.lines if ln["cost_category"] == "LABOR" and not ln["excluded"]
        ]
        traces = [
            {
                **t,
                "rule_code": "OVERHEAD:LABOR_RATIO",
                "price_item_code": None,
                "unit_price": None if rate is None else str(rate),
                "inputs": {"labor_cost": str(labor)},
                "formula_text": "직접노무비 × 제조간접비율",
            }
            for ln in labor_lines
            for t in ln["traces"][:1]
        ]
        q.line("OVERHEAD", "OVERHEAD", "제조간접비(노무비 비율)", "식", labor, rate, traces)

    material, overhead, outsource = q.total("MATERIAL"), q.total("OVERHEAD"), q.total("OUTSOURCE")
    manufacturing = material + labor + overhead + outsource
    admin_rate, profit_rate, vat_rate = (
        _d(ratios.get(k)) or D(0) for k in ("admin_rate", "profit_rate", "vat_rate")
    )
    r = lambda v: round_amount(v, q.rule, q.unit)
    admin = q.money(manufacturing * admin_rate)
    total_cost = manufacturing + admin
    profit = q.money(total_cost * profit_rate)
    supply = r(
        total_cost + profit
    )  # LINE scope: already whole units; TOTAL scope: rounded once here
    vat = r(supply * vat_rate)
    totals = {
        "material_cost": material,
        "labor_cost": labor,
        "overhead_cost": overhead,
        "outsource_cost": outsource,
        "manufacturing_cost": manufacturing,
        "admin_cost": admin,
        "total_cost": total_cost,
        "profit": profit,
        "supply_amount": supply,
        "vat_amount": vat,
        "total_amount": supply + vat,
    }
    if any(abs(v) >= MAX_TOTAL for v in totals.values()):
        q.log("ERROR", "QUOTE_AMOUNT_OVERFLOW", "합계가 비정상적으로 커서 금액을 0으로 두었습니다")
        totals = dict.fromkeys(totals, D(0))
    totals = {k: v.quantize(CENT, rounding=ROUND_HALF_UP) for k, v in totals.items()}
    for ln in q.lines:
        for k in ("calculated_unit_price", "calculated_amount"):
            if ln[k] is not None:
                ln[k] = ln[k].quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "lines": q.lines,
        "totals": totals,
        "logs": q.logs,
        "has_errors": any(lg["severity"] == "ERROR" for lg in q.logs),
    }
