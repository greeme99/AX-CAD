"""Track 9 Q4: parametric blocks (plan.md §15.5) — AutoCAD dynamic blocks, the simple way.

A template is JSON: parameters (name, default, range), optional visibility states, and entities
whose coordinates are formulas over the parameters (the safe evaluator of the quote rules, no
eval). Placing one makes an ordinary block named PB-<template>-<hash of the values>, so any CAD
reads it; the template and values go into the BLOCK's description (a standard DXF field), so the
editor can change the values later and place the regenerated block instead.

Entity kinds (coordinates in block units, mm, base point 0,0):
  LINE {x1 y1 x2 y2} · RECT {x y w h} · CIRCLE {cx cy r} · ARC {cx cy r a0 a1 (deg)} ·
  TEXT {x y h text}  — text may show values: "W={W}".
`when: [state...]` shows an entity only in those states. `repeat: {count, polar: {cx cy r
start}}` places count copies around a circle; the copy's centre is (px, py) in its formulas.
"""

import hashlib
import json
import math
import re
from decimal import Decimal
from typing import Any

from core.quote_engine.formula import FormulaError, evaluate

MAX_TEMPLATES = 100
MAX_PARAMS = 20
MAX_ENTITIES = 500
MAX_REPEAT = 360
MAX_STATES = 10
MAX_TEXT = 100
MAX_DESCRIPTION = 4000
MAX_GEOMS = 2000  # entities one block may expand to (repeats included)
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
PARAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
FIELDS = {
    "LINE": ("x1", "y1", "x2", "y2"),
    "RECT": ("x", "y", "w", "h"),
    "CIRCLE": ("cx", "cy", "r"),
    "ARC": ("cx", "cy", "r", "a0", "a1"),
    "TEXT": ("x", "y", "h"),
}
RESERVED = {"px", "py", "i", "min", "max", "ceil", "floor"}


class ParamBlockError(ValueError):
    pass


def _p(name: str, label: str, default: float, lo: float, hi: float) -> dict[str, Any]:
    return {"name": name, "label": label, "default": default, "min": lo, "max": hi}


BUILTIN: list[dict[str, Any]] = [
    {
        "name": "HOLE_PLATE",
        "label": "구멍 판 (모서리 4구멍)",
        "params": [
            _p("W", "가로", 100, 10, 5000),
            _p("H", "세로", 60, 10, 5000),
            _p("D", "구멍 지름", 9, 1, 100),
            _p("E", "구멍 중심 여백", 10, 2, 500),
        ],
        "entities": [
            {"type": "RECT", "x": "0", "y": "0", "w": "W", "h": "H"},
            {"type": "CIRCLE", "cx": "E", "cy": "E", "r": "D/2"},
            {"type": "CIRCLE", "cx": "W-E", "cy": "E", "r": "D/2"},
            {"type": "CIRCLE", "cx": "W-E", "cy": "H-E", "r": "D/2"},
            {"type": "CIRCLE", "cx": "E", "cy": "H-E", "r": "D/2"},
        ],
    },
    {
        "name": "SLOT",
        "label": "장공 (슬롯)",
        "params": [_p("L", "중심 간 길이", 30, 1, 2000), _p("W", "폭", 10, 1, 500)],
        "entities": [
            {"type": "LINE", "x1": "0", "y1": "-W/2", "x2": "L", "y2": "-W/2"},
            {"type": "LINE", "x1": "L", "y1": "W/2", "x2": "0", "y2": "W/2"},
            {"type": "ARC", "cx": "L", "cy": "0", "r": "W/2", "a0": "-90", "a1": "90"},
            {"type": "ARC", "cx": "0", "cy": "0", "r": "W/2", "a0": "90", "a1": "270"},
        ],
    },
    {
        "name": "BOLT_CIRCLE",
        "label": "플랜지 볼트원",
        "params": [
            _p("OD", "바깥 지름", 150, 10, 5000),
            _p("PCD", "볼트원 지름", 110, 5, 5000),
            _p("D", "볼트 구멍 지름", 14, 1, 200),
            _p("N", "볼트 수", 4, 2, 64),
            _p("ID", "안 지름", 50, 0, 5000),
        ],
        "states": ["관통", "막힘"],
        "entities": [
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "OD/2"},
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "ID/2", "when": ["관통"]},
            {
                "type": "CIRCLE",
                "cx": "px",
                "cy": "py",
                "r": "D/2",
                "repeat": {
                    "count": "N",
                    "polar": {"cx": "0", "cy": "0", "r": "PCD/2", "start": "0"},
                },
            },
        ],
    },
    {
        "name": "H_BEAM",
        "label": "H형강 단면",
        "params": [
            _p("H", "높이", 200, 50, 1000),
            _p("B", "플랜지 폭", 100, 30, 500),
            _p("TW", "웨브 두께", 5.5, 2, 50),
            _p("TF", "플랜지 두께", 8, 2, 60),
        ],
        "entities": [
            {"type": "RECT", "x": "-B/2", "y": "0", "w": "B", "h": "TF"},
            {"type": "RECT", "x": "-B/2", "y": "H-TF", "w": "B", "h": "TF"},
            {"type": "RECT", "x": "-TW/2", "y": "TF", "w": "TW", "h": "H-2*TF"},
        ],
    },
    {
        "name": "DOOR",
        "label": "문 (여닫이)",
        "params": [_p("W", "문 폭", 900, 500, 3000), _p("T", "문짝 두께", 40, 20, 100)],
        "states": ["왼쪽", "오른쪽"],
        "entities": [
            {"type": "RECT", "x": "0", "y": "0", "w": "T", "h": "W", "when": ["왼쪽"]},
            {
                "type": "ARC",
                "cx": "0",
                "cy": "0",
                "r": "W",
                "a0": "0",
                "a1": "90",
                "when": ["왼쪽"],
            },
            {"type": "RECT", "x": "W-T", "y": "0", "w": "T", "h": "W", "when": ["오른쪽"]},
            {
                "type": "ARC",
                "cx": "W",
                "cy": "0",
                "r": "W",
                "a0": "90",
                "a1": "180",
                "when": ["오른쪽"],
            },
            {"type": "LINE", "x1": "0", "y1": "0", "x2": "W", "y2": "0"},
        ],
    },
    {
        "name": "WINDOW",
        "label": "창 (벽 개구부)",
        "params": [_p("W", "창 폭", 1200, 300, 5000), _p("T", "벽 두께", 200, 50, 600)],
        "entities": [
            {"type": "RECT", "x": "0", "y": "0", "w": "W", "h": "T"},
            {"type": "LINE", "x1": "0", "y1": "T/2", "x2": "W", "y2": "T/2"},
            {"type": "TEXT", "x": "W/2", "y": "T+50", "h": "100", "text": "W{W}"},
        ],
    },
]


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(t: dict[str, Any]) -> dict[str, Any]:
    """A template as stored: shape, names, ranges, formulas (parsed, names known)."""
    if not isinstance(t, dict) or not NAME_RE.fullmatch(str(t.get("name", ""))):
        raise ParamBlockError("템플릿 이름은 영문 대문자·숫자·_ 32자 이내입니다")
    label = t.get("label", t["name"])
    if not isinstance(label, str) or not 0 < len(label) <= 100:
        raise ParamBlockError("템플릿 표시 이름은 100자 이내입니다")
    params = t.get("params")
    if not isinstance(params, list) or not 0 < len(params) <= MAX_PARAMS:
        raise ParamBlockError(f"매개변수는 1~{MAX_PARAMS}개입니다")
    names = set()
    for p in params:
        if (
            not isinstance(p, dict)
            or not PARAM_RE.fullmatch(str(p.get("name", "")))
            or p["name"] in RESERVED
        ):
            raise ParamBlockError("매개변수 이름은 영문으로 시작하는 16자 이내입니다")
        if (
            not all(_num(p.get(k)) for k in ("default", "min", "max"))
            or not p["min"] <= p["default"] <= p["max"]
        ):
            raise ParamBlockError(f"{p['name']}: 기본값은 최솟값과 최댓값 사이 숫자여야 합니다")
        if not isinstance(p.get("label", p["name"]), str) or len(p.get("label", "")) > 50:
            raise ParamBlockError(f"{p['name']}: 표시 이름은 50자 이내입니다")
        names.add(p["name"])
    if len(names) != len(params):
        raise ParamBlockError("매개변수 이름이 겹칩니다")
    states = t.get("states", [])
    if (
        not isinstance(states, list)
        or len(states) > MAX_STATES
        or not all(isinstance(s, str) and 0 < len(s) <= 20 for s in states)
    ):
        raise ParamBlockError(f"가시성 상태는 {MAX_STATES}개, 20자 이내입니다")
    ents = t.get("entities")
    if not isinstance(ents, list) or not 0 < len(ents) <= MAX_ENTITIES:
        raise ParamBlockError(f"엔티티는 1~{MAX_ENTITIES}개입니다")
    for e in ents:
        kind = e.get("type") if isinstance(e, dict) else None
        if kind not in FIELDS:
            raise ParamBlockError(f"엔티티 종류는 {', '.join(FIELDS)} 중 하나입니다")
        extra = {"px", "py"} if "repeat" in e else set()
        for k in FIELDS[kind]:
            _formula(e.get(k), names | extra)
        if kind == "TEXT" and (not isinstance(e.get("text"), str) or len(e["text"]) > MAX_TEXT):
            raise ParamBlockError(f"문자는 {MAX_TEXT}자 이내입니다")
        when = e.get("when", [])
        if not isinstance(when, list) or any(w not in states for w in when):
            raise ParamBlockError("when에는 템플릿의 상태 이름만 씁니다")
        if "repeat" in e:
            rep = e["repeat"]
            pol = rep.get("polar") if isinstance(rep, dict) else None
            if not isinstance(pol, dict):
                raise ParamBlockError("repeat는 {count, polar: {cx, cy, r, start}} 형식입니다")
            for f in (
                rep.get("count"),
                pol.get("cx"),
                pol.get("cy"),
                pol.get("r"),
                pol.get("start"),
            ):
                _formula(f, names)
    return {"name": t["name"], "label": label, "params": params, "states": states, "entities": ents}


def _formula(f: Any, names: set[str]) -> None:
    if not isinstance(f, str):
        raise ParamBlockError('좌표·크기는 문자열 수식으로 씁니다 (예: "W/2")')
    try:
        from core.quote_engine.formula import names as used

        unknown = used(f) - names
    except FormulaError as e:
        raise ParamBlockError(f"수식 오류: {f} ({e})") from None
    if unknown:
        raise ParamBlockError(f"수식 {f}에 모르는 이름: {', '.join(sorted(unknown))}")


def values_of(
    t: dict[str, Any], given: dict[str, Any], state: str | None
) -> tuple[dict[str, Decimal], str | None]:
    vals: dict[str, Decimal] = {}
    for p in t["params"]:
        v = given.get(p["name"], p["default"])
        if not _num(v) or not p["min"] <= v <= p["max"]:
            raise ParamBlockError(
                f"{p.get('label', p['name'])}은(는) {p['min']:g}~{p['max']:g}입니다"
            )
        vals[p["name"]] = Decimal(str(v))
    if set(given) - set(vals):
        raise ParamBlockError(f"모르는 매개변수: {', '.join(sorted(set(given) - set(vals)))}")
    states = t.get("states") or []
    if states:
        state = state if state is not None else states[0]
        if state not in states:
            raise ParamBlockError(f"상태는 {', '.join(states)} 중 하나입니다")
    else:
        state = None
    return vals, state


def _ev(f: str, env: dict[str, Decimal]) -> float:
    try:
        v = float(evaluate(f, env))
    except (FormulaError, ArithmeticError) as e:
        raise ParamBlockError(f"수식 {f}을(를) 계산할 수 없습니다 ({e})") from None
    if not math.isfinite(v) or abs(v) > 1e7:
        raise ParamBlockError(f"수식 {f}의 값이 범위를 벗어납니다")
    return v


def _geom(e: dict[str, Any], env: dict[str, Decimal]) -> dict[str, Any] | None:
    v = {k: _ev(e[k], env) for k in FIELDS[e["type"]]}
    kind = e["type"]
    if kind == "LINE":
        if math.dist((v["x1"], v["y1"]), (v["x2"], v["y2"])) < 1e-9:
            return None
        return {"type": "LINE", "start": [v["x1"], v["y1"]], "end": [v["x2"], v["y2"]]}
    if kind == "RECT":
        if v["w"] <= 0 or v["h"] <= 0:
            return None
        x, y, w, h = v["x"], v["y"], v["w"], v["h"]
        return {
            "type": "LWPOLYLINE",
            "points": [[x, y, 0], [x + w, y, 0], [x + w, y + h, 0], [x, y + h, 0]],
            "closed": True,
        }
    if kind in ("CIRCLE", "ARC"):
        if v["r"] <= 0:
            return None  # e.g. a bore of diameter 0: no hole
        if kind == "CIRCLE":
            return {"type": "CIRCLE", "center": [v["cx"], v["cy"]], "radius": v["r"]}
        return {
            "type": "ARC",
            "center": [v["cx"], v["cy"]],
            "radius": v["r"],
            "start_angle": v["a0"] % 360,
            "end_angle": v["a1"] % 360,
        }
    text = re.sub(
        r"\{([A-Za-z][A-Za-z0-9_]{0,15})\}",
        lambda m: f"{env[m.group(1)].normalize():f}" if m.group(1) in env else m.group(0),
        e["text"],
    )
    if v["h"] <= 0:
        return None
    return {
        "type": "TEXT",
        "insert": [v["x"], v["y"]],
        "height": v["h"],
        "value": text,
        "rotation": 0,
    }


def instantiate(
    t: dict[str, Any], given: dict[str, Any], state: str | None = None
) -> dict[str, Any]:
    """The block for these values: its name, entities (block coordinates, mm) and description."""
    vals, state = values_of(t, given, state)
    geoms: list[dict[str, Any]] = []
    for e in t["entities"]:
        if e.get("when") and state not in e["when"]:
            continue
        if "repeat" in e:
            pol = e["repeat"]["polar"]
            n = _ev(e["repeat"]["count"], vals)
            if n != int(n) or not 1 <= n <= MAX_REPEAT:
                raise ParamBlockError(f"반복 개수는 1~{MAX_REPEAT} 정수입니다")
            cx, cy, r, a0 = (_ev(pol[k], vals) for k in ("cx", "cy", "r", "start"))
            for k in range(int(n)):
                a = math.radians(a0 + 360 * k / n)
                env = {
                    **vals,
                    "px": Decimal(repr(cx + r * math.cos(a))),
                    "py": Decimal(repr(cy + r * math.sin(a))),
                }
                g = _geom(e, env)
                if g:
                    geoms.append(g)
                if len(geoms) > MAX_GEOMS:
                    raise ParamBlockError(f"블록 엔티티는 {MAX_GEOMS}개까지입니다")
            continue
        g = _geom(e, vals)
        if g:
            geoms.append(g)
        if len(geoms) > MAX_GEOMS:
            raise ParamBlockError(f"블록 엔티티는 {MAX_GEOMS}개까지입니다")
    if not geoms:
        raise ParamBlockError("이 값으로는 그릴 것이 없습니다")
    key = {
        "template": t["name"],
        "values": {k: f"{v.normalize():f}" for k, v in vals.items()},
        "state": state,
    }
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    desc = json.dumps({"axcad_pb": 1, **key}, ensure_ascii=False, sort_keys=True)
    return {
        "name": f"PB-{t['name']}-{digest}",
        "geoms": geoms,
        "description": desc[:MAX_DESCRIPTION],
        **key,
    }


def parse_description(text: str | None) -> dict[str, Any] | None:
    """The template and values of a parametric block's description, or None for any other block."""
    if not text or len(text) > MAX_DESCRIPTION:
        return None
    try:
        d = json.loads(text)
    except (ValueError, RecursionError):  # a deeply nested array in a crafted file
        return None
    if not isinstance(d, dict) or d.get("axcad_pb") != 1 or not isinstance(d.get("values"), dict):
        return None
    if not NAME_RE.fullmatch(str(d.get("template", ""))):
        return None
    vals, state = d["values"], d.get("state")
    if len(vals) > MAX_PARAMS or not all(
        isinstance(k, str)
        and len(k) <= 16
        and isinstance(v, str | int | float)
        and len(str(v)) <= 40
        for k, v in vals.items()
    ):
        return None
    if state is not None and not (isinstance(state, str) and len(state) <= 20):
        return None
    return {"template": d["template"], "values": d["values"], "state": d.get("state")}
