"""Korean sentences -> AX-CAD commands, by rules (no LLM). The result is only a proposal: the panel
shows it and the user runs it.

- 2D drawing: the command prompt inputs the 2D editor takes (`REC` -> `0,0` -> `@100,50`), so a
  proposal runs through the same tool reducer as typing and can be undone like it.
- 3D model: a feature to add (primitives, standard parts) or a dialog to open with values filled
  in (holes still need a body and positions picked on screen).

Patterns are anchored on keywords and numbers only; every regex is linear (no nested
quantifiers) and input is capped, so a long or hostile sentence cannot stall the server."""

import re
from typing import Any

MAX_TEXT = 300
MAX_MM = 100_000.0
N = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
PAIR = re.compile(rf"\(?\s*({N})\s*,\s*({N})\s*\)?")
DIMS3 = re.compile(rf"({N})\s*[x×*]\s*({N})\s*[x×*]\s*({N})")
DIMS2 = re.compile(rf"({N})\s*[x×*]\s*({N})")
METRIC = re.compile(r"(?<![A-Za-z])M\s*(\d{1,2})(?!\d)", re.IGNORECASE)
COUNT = re.compile(r"(\d{1,4})\s*(?:개|ea|EA|곳|군데)")
KOR_SIDES = {"삼": 3, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10}


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern + rf"\s*(?:[:=은는이가]\s*)?({N})", text)
    return float(m.group(1)) if m else None


def _fmt(v: float) -> str:
    return f"{v:g}"


def _pairs(text: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in PAIR.findall(text)]


def _ok(*vals: float | None) -> bool:
    return all(v is not None and 0 < v <= MAX_MM for v in vals)


def _radius(text: str) -> float | None:
    r = _num(r"(?:반지름|반경|\bR)", text)
    if r is not None:
        return r
    d = _num(r"(?:(?<!반)지름|직경|Ø|φ|\bD)", text)
    return d / 2 if d is not None else None


def _cad2d(text: str) -> dict[str, Any] | None:
    pts = _pairs(text)
    if any(abs(v) > MAX_MM for xy in pts for v in xy):
        return {"error": "좌표는 ±100 m(100000 mm) 이내로 알려 주세요"}
    at = pts[0] if pts else (0.0, 0.0)
    origin = f"{_fmt(at[0])},{_fmt(at[1])}"
    sizes = PAIR.sub(" ", text)  # sizes are read with the coordinates taken out
    if re.search(r"정사각형", text):
        m = re.search(rf"({N})", sizes)
        w = float(m.group(1)) if m else None
        if _ok(w):
            return _rect(origin, w, w)  # type: ignore[arg-type]
        return {"error": "정사각형 한 변 길이를 '정사각형 50'처럼 알려 주세요"}
    text = sizes if pts else text
    if re.search(r"(?:직사각형|사각형|네모|\bRECT\b)", text, re.IGNORECASE):
        m = DIMS2.search(text)
        w = float(m.group(1)) if m else _num(r"(?:가로|폭|너비)", text)
        h = float(m.group(2)) if m else _num(r"(?:세로|높이)", text)
        if _ok(w, h):
            return _rect(origin, w, h)  # type: ignore[arg-type]
        return {"error": "사각형 크기를 '100x50' 또는 '가로 100 세로 50'처럼 알려 주세요"}
    sides = re.search(r"(\d{1,2}|[삼오육칠팔구십])\s*각형", text)
    if sides or re.search(r"다각형", text):
        n = (int(s) if s.isdigit() else KOR_SIDES[s]) if sides and (s := sides.group(1)) else 6
        r = _radius(text) or _num(r"(?:크기)", text)
        if not 3 <= n <= 64:
            return {"error": "변의 수는 3~64입니다"}
        if _ok(r):
            return {
                "kind": "cad2d",
                "inputs": ["POL", str(n), origin, f"@{_fmt(r)},0"],  # type: ignore[arg-type]
                "summary": f"{n}각형 (중심 {origin}, 외접원 반지름 {_fmt(r)})",  # type: ignore[arg-type]
            }
        return {"error": "다각형 크기를 '반지름 30'처럼 알려 주세요"}
    if re.search(
        r"(?<![가-힣])원(?!통|형|뿔|점|가|래|본|하|인|래)|\bCIRCLE\b", text, re.IGNORECASE
    ):
        r = _radius(text)
        if _ok(r):
            return {
                "kind": "cad2d",
                "inputs": ["C", origin, _fmt(r)],  # type: ignore[arg-type]
                "summary": f"원 (중심 {origin}, 반지름 {_fmt(r)})",  # type: ignore[arg-type]
            }
        return {"error": "원 크기를 '반지름 20' 또는 '지름 40'처럼 알려 주세요"}
    if re.search(r"(?:직선|선분|(?<![가-힣])선(?!택|반)|\bLINE\b)", text, re.IGNORECASE):
        if len(pts) >= 2:
            ins = ["L", *(f"{_fmt(x)},{_fmt(y)}" for x, y in pts[:50]), ""]
            return {
                "kind": "cad2d",
                "inputs": ins,
                "summary": f"선 {len(ins) - 3}개 ({' → '.join(ins[1:-1])})",
            }
        length = _num(r"(?:길이)", text)
        if _ok(length):
            ang = _num(r"(?:각도)", text) or 0.0
            if abs(ang) > 360:
                return {"error": "각도는 -360~360도로 알려 주세요"}
            step = f"@{_fmt(length)}<{_fmt(ang)}"  # type: ignore[arg-type]
            return {
                "kind": "cad2d",
                "inputs": ["L", origin, step, ""],
                "summary": f"선 (시작 {origin}, 길이 {_fmt(length)}, 각도 {_fmt(ang)}°)",  # type: ignore[arg-type]
            }
        return {"error": "선은 '(0,0)에서 (100,0)까지 선' 또는 '길이 100 선'처럼 알려 주세요"}
    m = re.search(r"(?:문자|글자|텍스트)\s*['\"“‘](.{1,100}?)['\"”’]", text)
    if m:
        h = _num(r"(?:높이|크기)", text)
        return {
            "kind": "cad2d",
            "inputs": ["T", origin, _fmt(h) if _ok(h) else "", m.group(1)],  # type: ignore[arg-type]
            "summary": f"문자 '{m.group(1)}' (위치 {origin})",
        }
    return None


def _rect(origin: str, w: float, h: float) -> dict[str, Any]:
    return {
        "kind": "cad2d",
        "inputs": ["REC", origin, f"@{_fmt(w)},{_fmt(h)}"],
        "summary": f"사각형 {_fmt(w)}×{_fmt(h)} (구석점 {origin})",
    }


def _model3d(text: str) -> dict[str, Any] | None:
    count = COUNT.search(text)
    n = int(count.group(1)) if count else 1
    metric = METRIC.search(text)
    if metric and re.search(r"(?:볼트|너트|와셔)", text):
        size = f"M{int(metric.group(1))}"
        kind = (
            "HEX_NUT"
            if "너트" in text
            else "SPRING_WASHER"
            if "스프링" in text
            else "WASHER"
            if "와셔" in text
            else "SOCKET_BOLT"
            if re.search(r"(?:렌치|소켓|육각\s*구멍)", text)
            else "HEX_BOLT"
        )
        params: dict[str, Any] = {"kind": kind, "size": size, "qty": n}
        if kind.endswith("BOLT"):
            length = _num(r"(?:길이|L)", text) or _num(rf"M\s*{metric.group(1)}\s*[x×*]", text)
            if not _ok(length):
                return {"error": "볼트 길이를 'M8x20' 또는 'M8 길이 20'처럼 알려 주세요"}
            params["length"] = length
        return {
            "kind": "feature",
            "feature_type": "STANDARD",
            "params": params,
            "summary": f"규격품 {size} {kind} {n}개",
        }
    if re.search(r"(?:판금|절곡판|시트\s*메탈|sheet)", text, re.IGNORECASE):
        t = _num(r"(?:두께|\bT|t)", text)
        return {
            "kind": "dialog",
            "dialog": "SHEET",
            "values": {"thickness": t} if t is not None and 0 < t <= 50 else {},
            "summary": "판금 창을 엽니다. 스케치를 고르고 두께·굽힘 반경을 확인하세요",
        }
    if re.search(r"(?:구멍|홀|탭|드릴)", text):
        values: dict[str, Any] = {"count": n}
        if metric:
            values["size"] = f"M{int(metric.group(1))}"
            values["kind"] = "TAPPED" if re.search(r"(?:탭|나사)", text) else "CLEARANCE"
        else:
            d = _radius(text)
            if _ok(d):
                values["diameter"] = 2 * d  # type: ignore[operator]
        depth = _num(r"(?:깊이)", text)
        if _ok(depth):
            values["depth"] = depth
        return {
            "kind": "dialog",
            "dialog": "HOLE",
            "values": values,
            "summary": "구멍 마법사를 값을 채워 엽니다. 몸체와 위치를 화면에서 지정하세요",
        }
    if re.search(r"(?:상자|박스|직육면체|블록|BOX)", text, re.IGNORECASE):
        m = DIMS3.search(text)
        if not m:
            return {"error": "상자 크기를 '100x50x20'(가로x세로x높이)처럼 알려 주세요"}
        l, w, h = (float(g) for g in m.groups())
        if not _ok(l, w, h):
            return {"error": "크기는 0보다 크고 100 m 이하여야 합니다"}
        return _prim(
            {"kind": "BOX", "length": l, "width": w, "height": h},
            f"상자 {_fmt(l)}×{_fmt(w)}×{_fmt(h)}",
        )
    if re.search(r"(?:원통|실린더|원기둥|CYLINDER)", text, re.IGNORECASE):
        cr, ch = _radius(text), _num(r"(?:높이|길이)", text)
        if cr is None or ch is None or not _ok(cr, ch):
            return {"error": "원통 크기를 '지름 30 높이 50'처럼 알려 주세요"}
        return _prim(
            {"kind": "CYLINDER", "radius": cr, "height": ch}, f"원통 R{_fmt(cr)} 높이 {_fmt(ch)}"
        )
    if re.search(r"(?:구(?!멍)|공|SPHERE)", text, re.IGNORECASE) and re.search(
        r"(?:반지름|지름|반경|직경)", text
    ):
        r = _radius(text)
        if not _ok(r):
            return {"error": "구 크기를 '반지름 20'처럼 알려 주세요"}
        return _prim({"kind": "SPHERE", "radius": r}, f"구 R{_fmt(r)}")  # type: ignore[arg-type]
    return None


def _prim(params: dict[str, Any], summary: str) -> dict[str, Any]:
    return {
        "kind": "feature",
        "feature_type": "PRIMITIVE",
        "params": params,
        "summary": summary + " (원점)",
    }


def parse(text: str, page: str) -> dict[str, Any] | None:
    """A command proposal for `page` ("cad2d" | "model3d"), an {"error"} hint when the sentence
    is a command with missing values, or None when it is not a command at all."""
    t = " ".join(text.split())[:MAX_TEXT]
    if not t:
        return None
    if page == "cad2d":
        return _cad2d(t)
    if page == "model3d":
        return _model3d(t)
    return None
