"""Design-for-manufacturing checks over what the app already measures: 3D bodies (holes from the
hole features, bounding boxes, kernel warnings) and 2D drawing metrics. Each finding says what is
wrong, how to fix it, and which feature / line it is about, so the panel can jump there."""

from typing import Any

DEEP_HOLE_RATIO = 10.0  # drilled depth / diameter above this needs a gun drill or peck cycles
MIN_HOLE_MM = 1.0  # smaller holes are micro-drilling / EDM
THIN_MM = 0.5  # a body thinner than this in any direction is almost certainly a modelling slip
SHEET_MAX_MM = 6.0  # thinner bodies are treated as sheet (laser / punch); thicker ones are machined

SEVERITIES = ("ERROR", "WARN", "INFO")


def finding(
    severity: str, code: str, message: str, fix: str, target: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "fix": fix, "target": target}


def _feature(fid: int) -> dict[str, Any]:
    return {"kind": "feature", "id": fid}


def check_model(
    features: list[dict[str, Any]], bodies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """features: {feature_id, feature_type, status, suppressed}; bodies: the 3D quote bodies
    (routes_quote._bodies: bbox, holes, warnings)."""
    out: list[dict[str, Any]] = []
    for f in features:
        fid = f["feature_id"]
        if f["status"] != "OK":
            out.append(
                finding(
                    "ERROR",
                    "DFM_FEATURE_FAILED",
                    f"피처 #{fid}({f['feature_type']})가 재생성에 실패했습니다",
                    "피처를 편집해 입력값(스케치·대상 몸체·치수)을 고치거나 억제하세요",
                    _feature(fid),
                )
            )
        elif f.get("suppressed"):
            out.append(
                finding(
                    "INFO",
                    "DFM_FEATURE_SUPPRESSED",
                    f"피처 #{fid}({f['feature_type']})가 억제되어 형상·견적에서 빠졌습니다",
                    "의도한 것이 아니면 억제를 해제하세요",
                    _feature(fid),
                )
            )
    if not bodies and any(f["feature_type"] != "SKETCH" for f in features):
        out.append(
            finding(
                "WARN",
                "DFM_NO_BODY",
                "견적할 수 있는 몸체가 없습니다",
                "돌출·회전 등으로 솔리드를 만들거나 실패한 피처를 고치세요",
            )
        )
    for b in bodies:
        bid, name = b["feature_id"], b.get("name") or f"#{b['feature_id']}"
        size = b.get("bbox", {}).get("size") or []
        thin = min(size) if size else None
        if thin is not None and thin < THIN_MM:
            out.append(
                finding(
                    "WARN",
                    "DFM_THIN_BODY",
                    f"{name}: 가장 얇은 방향 두께가 {thin:.3g} mm입니다",
                    "치수 단위(mm)와 돌출 깊이를 확인하세요",
                    _feature(bid),
                )
            )
        for w in b.get("warnings", []):
            code = str(w).partition(":")[0]
            if code in ("OPEN_SHELLS", "SEWN"):
                out.append(
                    finding(
                        "WARN",
                        "DFM_SHELL_CHECK",
                        f"{name}: 닫히지 않았거나 봉합된 형상이라 부피·중량을 신뢰할 수 없습니다",
                        "원본 STEP을 솔리드로 다시 내보내거나 형상을 다시 만드세요",
                        _feature(bid),
                    )
                )
        for h in b.get("holes", []):
            out += _hole(h, thin)
    return out


def _hole(h: dict[str, Any], thickness: float | None) -> list[dict[str, Any]]:
    out = []
    d, n, fid = float(h["diameter"]), int(h["count"]), h["feature_id"]
    drilled = h.get("drill_length")
    depth = float(drilled) / n if drilled is not None and n else float(h.get("depth") or 0)
    if d < MIN_HOLE_MM:
        out.append(
            finding(
                "WARN",
                "DFM_HOLE_TINY",
                f"구멍 지름 {d:g} mm는 일반 드릴 가공 범위보다 작습니다",
                f"{MIN_HOLE_MM:g} mm 이상으로 키우거나 방전·레이저 가공을 지정하세요",
                _feature(fid),
            )
        )
    elif depth and depth / d > DEEP_HOLE_RATIO:
        out.append(
            finding(
                "WARN",
                "DFM_HOLE_DEEP",
                f"구멍 깊이/지름 비가 {depth / d:.1f}로 깊습니다 (지름 {d:g}, 깊이 {depth:.3g} mm)",
                f"깊이를 지름의 {DEEP_HOLE_RATIO:g}배 이하로 줄이거나 지름을 키우세요(심공 가공비 증가)",
                _feature(fid),
            )
        )
    if thickness is not None and d < thickness <= SHEET_MAX_MM:
        out.append(
            finding(
                "INFO",
                "DFM_HOLE_LT_THICKNESS",
                f"구멍 지름 {d:g} mm가 판 두께 {thickness:g} mm보다 작습니다",
                "판재 펀칭·레이저는 보통 지름 ≥ 두께가 필요합니다. 드릴 가공으로 견적되는지 확인하세요",
                _feature(fid),
            )
        )
    return out


DRAWING_WARN = {
    "METRIC_UNITS_ASSUMED_MM": (
        "도면 단위가 없어 mm로 가정했습니다",
        "DXF 단위($INSUNITS)를 mm로 저장하거나 축척을 확인하세요",
    ),
    "METRIC_OPEN_CONTOUR": (
        "닫히지 않은 윤곽이 {n}개 있습니다",
        "끝점이 떨어진 선을 결합(J)하거나 연장(EX)해 닫으세요",
    ),
    "METRIC_DUPLICATE_GEOMETRY": (
        "겹친 선이 {n}개 있습니다",
        "중복선을 지우세요. 절단 길이는 한 번만 셌습니다",
    ),
    "METRIC_NO_OUTER_CONTOUR": (
        "외곽 윤곽을 찾지 못했습니다",
        (
            "외곽선을 닫힌 폴리라인(LWPOLYLINE, 끝점 간격 0.0001 mm 이하)으로 그리세요. "
            "레이어는 치수·문자·중심선·숨은선·표제란·표 레이어만 아니면 됩니다"
        ),
    ),
    "METRIC_MULTIPLE_PARTS": (
        "분리된 윤곽이 {n}개 있어 가장 큰 것만 부품으로 봤습니다",
        "부품마다 도면을 나누거나 블록으로 묶으세요",
    ),
}


# checks that only matter when the drawing is quoted (a general drawing — layout, assembly,
# cover sheet — has no single outer contour, material or thickness to find)
QUOTE_ONLY = {"METRIC_NO_OUTER_CONTOUR", "METRIC_MULTIPLE_PARTS", "METRIC_OPEN_CONTOUR"}


def check_drawing(m: dict[str, Any], quote: bool = True) -> list[dict[str, Any]]:
    """2D metrics (metrics2d.compute) -> findings: drawing quality, and for a quote drawing the
    outer contour, title block and holes smaller than the sheet thickness."""
    out = []
    for w in m.get("warnings", []):
        code, _, n = str(w).partition(":")
        if code in DRAWING_WARN and (quote or code not in QUOTE_ONLY):
            text, fix = DRAWING_WARN[code]
            if (
                code == "METRIC_NO_OUTER_CONTOUR"
                and m.get("cutting_length_mm") == 0
                and not m.get("items")
            ):
                # a blank drawing (or one with only dimensions/centre lines) is not a broken part
                out.append(
                    finding(
                        "INFO",
                        "DFM_DRAWING_EMPTY",
                        "아직 절단할 선이 없습니다",
                        "외곽선을 그린 뒤 다시 점검하세요. 치수·문자·중심선 레이어는 절단선으로 세지 않습니다",
                    )
                )
                continue
            sev = "ERROR" if code == "METRIC_NO_OUTER_CONTOUR" else "WARN"
            out.append(finding(sev, "DFM_" + code.removeprefix("METRIC_"), text.format(n=n), fix))
    if not quote:
        return out
    title = m.get("title_block") or {}
    missing = [k for k in title.get("missing", []) if k in ("material", "thickness_mm")]
    if missing:
        names = {"material": "재질", "thickness_mm": "두께"}
        out.append(
            finding(
                "INFO",
                "DFM_TITLE_MISSING",
                "표제란에서 " + "·".join(names[k] for k in missing) + "을(를) 찾지 못했습니다",
                "표제란에 재질·두께를 적거나 견적 때 직접 입력하세요",
            )
        )
    t = title.get("thickness_mm")
    try:
        thick = float(t) if t is not None else None
    except (TypeError, ValueError):
        thick = None
    if thick:
        small = [float(d) for d in (m.get("holes_by_dia") or {}) if float(d) < thick]
        if small:
            out.append(
                finding(
                    "WARN",
                    "DFM_HOLE_LT_THICKNESS",
                    f"두께 {thick:g} mm보다 작은 구멍(지름 {', '.join(f'{d:g}' for d in small)} mm)이 있습니다",
                    "펀칭·레이저는 보통 지름 ≥ 두께가 필요합니다. 지름을 키우거나 드릴 공정으로 바꾸세요",
                )
            )
    return out


QUOTE_ADVICE = {
    "QUOTE_PRICE_MISSING": "기준정보 단가표에 해당 품목 단가를 넣거나 라인에서 수동 단가를 입력하세요",
    "QUOTE_QTY_INVALID": "수량을 0보다 크게 고치거나 라인을 제외하세요",
    "QUOTE_MATERIAL_REQUIRED": "견적 생성 시 재질·두께를 지정하고 재산출하세요",
    "QUOTE_OVERRIDE_LARGE": "수동 조정 사유를 확인하세요. 자동값과 차이가 커 감사 대상입니다",
    "QUOTE_DUPLICATE_LINE": "같은 원천에서 나온 중복 라인 하나를 제외하세요",
    "QUOTE_OVERHEAD_STALE": "수동 조정 후 간접비가 다시 계산되지 않았습니다. 재산출하세요",
    "QUOTE_SOURCE_OUTDATED": "도면이 바뀌었습니다. 최신 리비전으로 견적을 다시 만드세요",
    "QUOTE_SOURCE_CHECK": "원본 도면/모델을 열어 Copilot 점검 탭의 지적 사항을 고치세요",
    "QUOTE_MASTER_OUTDATED": "현재 기준정보로 재산출하면 최신 단가가 반영됩니다",
    "QUOTE_PRICE_EXPIRED": "기준정보에서 단가 유효기한을 갱신하세요",
    "QUOTE_TOTAL_MISMATCH": "라인 합계와 헤더 합계가 다릅니다. 재산출하세요",
    "QUOTE_AMOUNT_OVERFLOW": "수량·단가 단위를 확인하세요(금액이 너무 큽니다)",
}


def advise_quote(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Quote validation logs -> findings with a fix; the line number is the jump target."""
    return [
        finding(
            lg["severity"] if lg["severity"] in SEVERITIES else "WARN",
            lg["code"],
            lg["message"],
            QUOTE_ADVICE.get(lg["code"], "견적 화면에서 해당 라인을 확인하세요"),
            {"kind": "line", "id": lg["line_no"]} if lg.get("line_no") else None,
        )
        for lg in logs
    ]


def sort_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda f: SEVERITIES.index(f["severity"]))


def check_assembly(
    components: list[dict[str, Any]], mates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Track 9 Q2: an assembly's components (routes_assembly._comp_out) and mates."""
    out = []
    names = {c["component_id"]: c["name"] for c in components}
    for c in components:
        if c["status"] == "MISSING":
            out.append(
                finding(
                    "ERROR",
                    "ASM_COMPONENT_MISSING",
                    f"{c['name']}: 원본 부품의 형상이 없어졌습니다",
                    "원본 부품 문서를 고치거나 이 부품을 지우고 다시 추가하세요",
                    {"kind": "entity", "id": c["component_id"]},
                )
            )
    for m in mates:
        if m["status"] == "FAILED":
            pair = f"{names.get(m['a']['component_id'], '?')} ↔ {names.get(m['b']['component_id'], '?')}"
            out.append(
                finding(
                    "ERROR",
                    "ASM_MATE_FAILED",
                    f"{m['mate_type']} 메이트를 만족할 수 없습니다 ({pair})",
                    "다른 메이트와 충돌합니다. 값·방향(같은 방향 옵션)을 바꾸거나 메이트 하나를 지우세요",
                    {"kind": "entity", "id": m["b"]["component_id"]},
                )
            )
    if len(components) > 1 and not any(c["fixed"] for c in components):
        out.append(
            finding(
                "WARN",
                "ASM_NOTHING_FIXED",
                "고정된 부품이 없어 메이트를 풀 때 모든 부품이 움직입니다",
                "기준이 되는 부품 하나를 고정하세요",
            )
        )
    if components and not mates and len(components) > 1:
        out.append(
            finding(
                "INFO",
                "ASM_NO_MATES",
                "메이트가 없어 부품 위치가 좌표로만 정해져 있습니다",
                "면을 골라 일치·동심 메이트를 걸면 부품을 바꿔도 위치가 유지됩니다",
            )
        )
    return out


def check_sheet(fid: int, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Track 9 Q3: a sheet metal feature (core/geometry/sheetmetal spec)."""
    import math

    from core.geometry.sheetmetal import edges

    t, r = spec["thickness"], spec["bend_radius"]
    target = _feature(fid)
    out = []
    if r < t:
        out.append(
            finding(
                "INFO",
                "SHEET_RADIUS_SMALL",
                f"굽힘 반경 R{r:g}이 두께 {t:g} mm보다 작습니다",
                "고장력·알루미늄 판은 갈라질 수 있습니다. R ≥ T를 권장합니다",
                target,
            )
        )
    if not spec.get("outline"):  # plan §23 S3: a body turned into a sheet has no outline
        return out
    es = edges(spec["outline"])
    # plan §23 S2: a flange may sit on another flange's side (no outline edge) and bend again
    by_edge = {f["edge"]: f for f in spec["flanges"] if f.get("edge") is not None}
    min_len = 2 * t
    for i, f in enumerate(spec["flanges"]):
        where = f"변 #{f['edge']}" if f.get("edge") is not None else f"플랜지 {i + 1}(옆면)"
        short = [st["length"] for st in [f, *(f.get("steps") or [])] if st["length"] < min_len]
        if short and f.get("style") != "HEM":  # a hem's return leg is short on purpose
            out.append(
                finding(
                    "WARN",
                    "SHEET_FLANGE_SHORT",
                    f"{where} 플랜지 직선부 {min(short):g} mm가 짧습니다 (최소 {min_len:g} mm)",
                    "절곡기 금형에 걸리도록 직선부를 두께의 2배 이상으로 늘리세요",
                    target,
                )
            )
        if f.get("edge") is None:
            continue
        e = es[f["edge"]]
        (ax, ay), (bx, by) = e["start"], e["end"]
        for c in spec["outline"]["circles"]:
            cx, cy = c["center"]
            d = abs((bx - ax) * (ay - cy) - (ax - cx) * (by - ay)) / e["length"] - c["radius"]
            if d < 2 * t + r:
                out.append(
                    finding(
                        "WARN",
                        "SHEET_HOLE_NEAR_BEND",
                        f"구멍(지름 {2 * c['radius']:g})이 변 #{f['edge']} 절곡선에서 {d:.1f} mm로 가깝습니다",
                        f"구멍이 늘어나지 않도록 {2 * t + r:g} mm(2T+R) 이상 떼세요",
                        target,
                    )
                )
    n = len(es)
    for i, f in by_edge.items():
        g = by_edge.get((i + 1) % n)
        if g is None:
            continue
        gap = f["inset_end"] + g["inset_start"]
        if gap < r + t and abs(math.remainder(f["angle"], 360)) > 0:
            out.append(
                finding(
                    "WARN",
                    "SHEET_FLANGE_OVERLAP",
                    f"변 #{i}와 #{(i + 1) % n} 플랜지가 모서리에서 겹칩니다",
                    f"두 플랜지의 들여쓰기 합을 {r + t:g} mm(R+T) 이상으로 해 모서리 릴리프를 두세요",
                    target,
                )
            )
    return out
