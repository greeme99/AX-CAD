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
All money is Decimal; nothing passes through float.

Track 6 (both quote kinds): every master unit price is multiplied by the chosen region and
escalation factors and the scenario's factor for its cost category, every quantity by the
scenario's qty factor (ratio lines excepted: they follow their base). A price row past its
valid_until warns QUOTE_PRICE_EXPIRED."""

from datetime import date
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
Groups = dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
# Laser-cut holes are already in cutting_length_mm, so only punched holes need their own rule.
GROUPS: Groups = {
    "절단": (("cutting_length_mm",), ("cutting_length_mm",)),
    "펀칭 구멍": (("punch_hole_count",), ("hole_count", "punch_hole_count")),
    "절곡": (("bend_count", "bend_length_mm"), ("bend_count", "bend_length_mm")),
    "표면처리": (("surface_area_mm2",), ("surface_area_mm2",)),
}
# Track 8 P4-1: machining of 3D bodies (holes from the hole features, stock - part)
GROUPS_3D: Groups = {
    "드릴": (("hole_count",), ("hole_count", "drill_length_mm")),
    "탭": (("tap_count",), ("tap_count", "tap_length_mm")),
    "카운터보어": (("counterbore_count",), ("counterbore_count",)),
    "접시": (("countersink_count",), ("countersink_count",)),
    "밀링": (("mill_removed_volume_mm3",), ("mill_removed_volume_mm3", "removed_volume_mm3")),
    "선반": (("turn_removed_volume_mm3",), ("turn_removed_volume_mm3", "removed_volume_mm3")),
}
# which hole features explain a 3D hole metric (all of them, or only those of one kind)
HOLE_METRICS = {
    "hole_count": lambda h: True,
    "drill_length_mm": lambda h: True,
    "tap_count": lambda h: h["threaded"],
    "tap_length_mm": lambda h: h["threaded"],
    "counterbore_count": lambda h: h["head"] == "COUNTERBORE",
    "countersink_count": lambda h: h["head"] == "COUNTERSINK",
}
PI = Decimal("3.14159265358979323846")
ALLOWANCE = Decimal(2)  # stock allowance per side when the master data has none (mm)
D = Decimal


def _d(v: Any) -> Decimal | None:
    return None if v is None else D(str(v))


def round_amount(v: Decimal, rule: str, unit: int) -> Decimal:
    # settle division residue first: 4 min / 60 * 4 * 30000 must stay 8000, not 8000.000...01
    # (which CEILING would turn into 8010)
    v = v.quantize(D("0.0001"), rounding=ROUND_HALF_UP)
    u = D(unit)
    return ((v / u).quantize(D(1), rounding=ROUNDING[rule]) * u).quantize(CENT)


CATEGORIES = ("MATERIAL", "LABOR", "OVERHEAD", "OUTSOURCE")


class Quote:
    def __init__(
        self,
        ratios: dict[str, Any],
        bundle: dict[str, Any] | None = None,
        adjust: dict[str, Any] | None = None,
        on: date | None = None,
    ):
        self.rule, self.unit = ratios["rounding_rule"], int(ratios["rounding_unit"])
        self.per_line = ratios["rounding_scope"] == "LINE"
        self.lines: list[dict[str, Any]] = []
        self.logs: list[dict[str, str]] = []
        self.on = on
        self.factors, self.qty_factor, self.base_factor = self._factors(bundle or {}, adjust or {})

    def _factors(
        self, bundle: dict[str, Any], adjust: dict[str, Any]
    ) -> tuple[dict[str, Decimal], Decimal, dict[str, str]]:
        """Price factor per cost category (region × escalation × scenario), the qty factor, and
        what went into them (for the trace)."""
        base, used = D(1), {}
        rows = {(f["factor_type"], f["code"]): f for f in bundle.get("adjust_factors", [])}
        for kind, key in (("REGION", "region"), ("ESCALATION", "escalation")):
            if code := adjust.get(key):
                row = rows.get((kind, code))
                if row is None:
                    self.log(
                        "ERROR", "QUOTE_FACTOR_UNKNOWN", f"기준정보에 보정계수 {code}가 없습니다"
                    )
                    continue
                base *= D(str(row["factor"]))
                used[f"{key}_factor"] = str(row["factor"])
        sc = adjust.get("scenario") or {}
        factors = {c: base * D(str(sc.get(c.lower(), 1))) for c in CATEGORIES}
        return factors, D(str(sc.get("qty", 1))), used

    def expired(self, row: dict[str, Any], label: str) -> None:
        until = row.get("valid_until")
        if self.on and until and date.fromisoformat(str(until)) < self.on:
            self.log("WARN", "QUOTE_PRICE_EXPIRED", f"{label}: 단가 유효기한({until})이 지났습니다")

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
        adjust: bool = True,
    ) -> None:
        """adjust=False for ratio lines (간접비·일반관리비 basis): their base is already adjusted."""
        if adjust:
            f = self.factors[category]
            if f != 1 or self.qty_factor != 1 or self.base_factor:
                # the master price stays in the trace's unit_price; what multiplied it is here
                extra = {
                    **self.base_factor,
                    "price_factor": str(f),
                    "qty_factor": str(self.qty_factor),
                }
                traces = [{**t, "inputs": {**t["inputs"], **extra}} for t in traces]
            qty, price = qty * self.qty_factor, None if price is None else price * f
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
        bodies = metrics.get("bodies", [])
        pick = HOLE_METRICS.get(metric or "")
        if pick:  # a hole process: the hole features behind it
            refs = [str(h["feature_id"]) for b in bodies for h in b.get("holes", []) if pick(h)]
        else:
            refs = [str(b["feature_id"]) for b in bodies]
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


def stock(body: dict[str, Any], allowance: Decimal) -> dict[str, Decimal]:
    """Raw stock of a 3D body (P4-1): the bounding box grown by the allowance on every side, or for
    a turned body (full revolution / cylinder on a world axis) a round bar: diameter = the larger
    cross size + 2 allowance, length = the axial size + 2 allowance."""
    size = [D(str(v)) for v in body["bbox"]["size"]]
    axis = body.get("turn_axis")
    if axis in ("X", "Y", "Z"):
        i = "XYZ".index(axis)
        dia = max(v for j, v in enumerate(size) if j != i) + 2 * allowance
        length = size[i] + 2 * allowance
        return {
            "stock_volume_mm3": PI * dia * dia / 4 * length,
            "stock_diameter_mm": dia,
            "stock_length_mm": length,
        }
    vol = D(1)
    for v in size:
        vol *= v + 2 * allowance
    return {"stock_volume_mm3": vol}


def _machining_env(bodies: list[dict[str, Any]], allowance: Decimal) -> dict[str, Decimal]:
    """Stock, removed volume and hole totals of 3D bodies; a metric that does not apply (no taps,
    a milled part's turning volume) is left out, so its rule is skipped, not warned about."""
    env: dict[str, Decimal] = {}

    def add(k: str, v: Decimal) -> None:
        if v > 0:
            env[k] = env.get(k, D(0)) + v

    for b in bodies:
        st = stock(b, allowance)
        for k, v in st.items():
            add(k, v)
        removed = max(st["stock_volume_mm3"] - D(str(b["volume_mm3"])), D(0))
        add("removed_volume_mm3", removed)
        turned = "stock_diameter_mm" in st
        add("turn_removed_volume_mm3" if turned else "mill_removed_volume_mm3", removed)
        for h in b.get("holes", []):
            n = D(h["count"])
            # drilled length as measured by the kernel; older features: count x depth
            drilled = h.get("drill_length")
            length = D(str(drilled)) if drilled is not None else n * D(str(h.get("depth") or 0))
            add("hole_count", n)
            add("drill_length_mm", length)
            if h["threaded"]:
                add("tap_count", n)
                thread = h.get("thread_length")
                add("tap_length_mm", n * D(str(thread)) if thread is not None else length)
            if h["head"] == "COUNTERBORE":
                add("counterbore_count", n)
            elif h["head"] == "COUNTERSINK":
                add("countersink_count", n)
    if "hole_count" in env:  # formulas of the hole rules read both totals
        env.setdefault("drill_length_mm", D(0))
    if "tap_count" in env:
        env.setdefault("tap_length_mm", D(0))
    return env


def _env(
    metrics: dict[str, Any],
    source: dict[str, Any],
    qty: int,
    thickness: Decimal | None,
    allowance: Decimal = ALLOWANCE,
) -> dict[str, Decimal]:
    """Numeric inputs a formula may read."""
    if source["kind"] == "DOCUMENT_3D":
        bodies = metrics.get("bodies", [])
        env = {
            "volume_mm3": sum((D(str(b["volume_mm3"])) for b in bodies), D(0)),
            "surface_area_mm2": sum((D(str(b["surface_area_mm2"])) for b in bodies), D(0)),
            **_machining_env(bodies, allowance),
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
    allowance: Decimal = ALLOWANCE,
    label: str = "",
) -> None:
    if not code:
        return q.log(
            "ERROR", "QUOTE_MATERIAL_REQUIRED", f"{label}재질을 지정하세요 (표제란에서 읽지 못함)"
        )
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
    q.expired(m, f"재질 {code}")
    extra: dict[str, str] = {}
    if source["kind"] == "DOCUMENT_3D":
        bodies = metrics.get("bodies", [])
        if basis == "BBOX":  # the raw stock: box or round bar, with the allowance (P4-1)
            stocks = [stock(b, allowance) for b in bodies]
            vol = sum((s["stock_volume_mm3"] for s in stocks), D(0))
            extra = {"stock_allowance_mm": str(allowance)}
            if len(stocks) == 1 and "stock_diameter_mm" in stocks[0]:
                extra |= {
                    "stock_diameter_mm": str(stocks[0]["stock_diameter_mm"]),
                    "stock_length_mm": str(stocks[0]["stock_length_mm"]),
                }
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
        **extra,
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
        f"{label}{code} 소재",
        "kg",
        kg,
        _d(m["unit_price_per_kg"]),
        [{**_sources(metrics, metric, source), **trace}],
    )


def _rules(
    q: Quote,
    bundle: dict[str, Any],
    metrics: dict[str, Any],
    source: dict[str, Any],
    qty: int,
    thickness_mm: Decimal | None,
    allowance: Decimal,
    machine_hour: bool,
    label: str,
) -> dict[str, Decimal]:
    """Process rule lines of one part (the whole drawing, or one 3D body); returns its inputs."""
    prices = {p["item_code"]: p for p in bundle["price_items"]}
    env = _env(metrics, source, qty, thickness_mm, allowance)
    for rule in bundle["process_rules"]:
        metric = rule["input_metric"]
        name = f"{label}{rule['process_name'] or rule['process_code']}"
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
            if item:
                q.expired(item, f"{label}({code})")
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

    return env


def compute_quote(
    metrics: dict[str, Any],
    bundle: dict[str, Any],
    source: dict[str, Any],
    *,
    qty: int,
    material_code: str | None,
    thickness_mm: Decimal | None,
    adjust: dict[str, Any] | None = None,
    on: date | None = None,
) -> dict[str, Any]:
    """source: {"kind": "REVISION", "revision_id"} (2D metrics) or {"kind": "DOCUMENT_3D", "document_id"}.
    adjust: {"region", "escalation", "scenario": {material, labor, overhead, outsource, qty}}.
    A 3D quote prices each body on its own (P4-1/P4-3): its material, its stock and machining, its
    quantity; a body may carry its own "material_code" and "qty", else the quote's apply."""
    ratios = bundle["cost_ratios"]
    q = Quote(ratios, bundle, adjust, on)
    basis = ratios.get("material_basis", "NET")
    allowance = _d(ratios.get("stock_allowance_mm"))
    allowance = ALLOWANCE if allowance is None else allowance
    machine_hour = ratios["overhead_basis"] == "MACHINE_HOUR"
    if source["kind"] == "DOCUMENT_3D":
        bodies = metrics.get("bodies", [])
        envs = []
        for b in bodies:
            part = {**metrics, "bodies": [b]}
            label = f"{b['name']} · " if len(bodies) > 1 else ""
            mat, n = b.get("material_code") or material_code, int(b.get("qty") or qty)
            _material(q, bundle, part, source, mat, None, n, basis, allowance, label)
            envs.append(_rules(q, bundle, part, source, n, None, allowance, machine_hour, label))
        groups = GROUPS_3D
    else:
        _material(q, bundle, metrics, source, material_code, thickness_mm, qty, basis)
        envs = [_rules(q, bundle, metrics, source, qty, thickness_mm, allowance, machine_hour, "")]
        groups = GROUPS
    env = {k: max(e.get(k, D(0)) for e in envs) for e in envs for k in e}

    mapped = {r["input_metric"] for r in bundle["process_rules"]}
    for group, (need, by) in groups.items():
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
        q.line("OVERHEAD", "OVERHEAD", "제조간접비(노무비 비율)", "식", labor, rate, traces, False)
    return finish(q, ratios)


def finish(q: Quote, ratios: dict[str, Any], construction: bool = False) -> dict[str, Any]:
    for ln in q.lines:  # lines are stored to the cent; totals are always summed from that
        for k in ("calculated_unit_price", "calculated_amount"):
            if ln[k] is not None:
                ln[k] = ln[k].quantize(CENT, rounding=ROUND_HALF_UP)
    totals = summarize(q.lines, ratios, construction=construction)
    if totals is None:
        q.log("ERROR", "QUOTE_AMOUNT_OVERFLOW", "합계가 비정상적으로 커서 금액을 0으로 두었습니다")
        totals = dict.fromkeys(TOTAL_KEYS, D("0.00"))
    return {
        "lines": q.lines,
        "totals": totals,
        "logs": q.logs,
        "has_errors": any(lg["severity"] == "ERROR" for lg in q.logs),
    }


TOTAL_KEYS = (
    "material_cost",
    "labor_cost",
    "overhead_cost",
    "outsource_cost",
    "manufacturing_cost",
    "admin_cost",
    "total_cost",
    "profit",
    "supply_amount",
    "vat_amount",
    "total_amount",
)


def effective_amount(line: dict[str, Any]) -> Decimal | None:
    """FN-19: a manual amount wins; an excluded line without one counts for nothing."""
    if line.get("override_amount") is not None:
        return D(str(line["override_amount"]))
    if line["excluded"] or line["calculated_amount"] is None:
        return None
    return D(str(line["calculated_amount"]))


def summarize(
    lines: list[dict[str, Any]], ratios: dict[str, Any], *, construction: bool = False
) -> dict[str, Decimal] | None:
    """Cost summary from line amounts (override ?? calculated). None when out of column range.
    construction: 공사원가계산서 이윤 = (노무비 + 경비 + 일반관리비) × 이윤율 (재료비 제외)."""
    rule, unit = ratios["rounding_rule"], int(ratios["rounding_unit"])
    per_line = ratios["rounding_scope"] == "LINE"
    by: dict[str, Decimal] = {c: D(0) for c in ("MATERIAL", "LABOR", "OVERHEAD", "OUTSOURCE")}
    for ln in lines:
        if (a := effective_amount(ln)) is not None:
            by[ln["cost_category"]] += a

    def money(v: Decimal) -> Decimal:
        return round_amount(v, rule, unit) if per_line else v

    manufacturing = sum(by.values(), D(0))
    admin_rate, profit_rate, vat_rate = (
        _d(ratios.get(k)) or D(0) for k in ("admin_rate", "profit_rate", "vat_rate")
    )
    admin = money(manufacturing * admin_rate)
    total_cost = manufacturing + admin
    profit_base = total_cost - by["MATERIAL"] - by["OUTSOURCE"] if construction else total_cost
    profit = money(profit_base * profit_rate)
    supply = round_amount(total_cost + profit, rule, unit)  # TOTAL scope: the one rounding
    vat = round_amount(supply * vat_rate, rule, unit)
    values = (
        by["MATERIAL"],
        by["LABOR"],
        by["OVERHEAD"],
        by["OUTSOURCE"],
        manufacturing,
        admin,
        total_cost,
        profit,
        supply,
        vat,
        supply + vat,
    )
    if any(abs(v) >= MAX_TOTAL for v in values):
        return None
    return {
        k: v.quantize(CENT, rounding=ROUND_HALF_UP) for k, v in zip(TOTAL_KEYS, values, strict=True)
    }
