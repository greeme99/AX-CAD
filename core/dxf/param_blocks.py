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

from core.dxf.interior_blocks import INTERIOR_BUILTIN
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
    # --- 기계 (Mechanical) ---
    {
        "name": "HOLE_PLATE",
        "label": "구멍 판 (모서리 4구멍)",
        "category": "기계",
        "base_point": "좌측 하단 모서리 (0,0)",
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
        "category": "기계",
        "base_point": "좌측 호 중심 (0,0)",
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
        "category": "기계",
        "base_point": "플랜지 중심 (0,0)",
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
        "category": "기계",
        "base_point": "하부 플랜지 중심 (0,0)",
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
        "name": "HEX_BOLT",
        "label": "육각 볼트 (KS B 1002)",
        "category": "기계",
        "base_point": "볼트 머리 좌면 중심 (0,0)",
        "params": [_p("D", "호칭 지름 M", 10, 3, 64), _p("L", "축 길이", 35, 6, 500)],
        "states": ["측면", "평면"],
        "entities": [
            {
                "type": "RECT",
                "x": "-D*0.7",
                "y": "-D*0.85",
                "w": "D*0.7",
                "h": "D*1.7",
                "when": ["측면"],
            },
            {"type": "RECT", "x": "0", "y": "-D/2", "w": "L", "h": "D", "when": ["측면"]},
            {"type": "LINE", "x1": "-D*0.7", "y1": "0", "x2": "L+2", "y2": "0", "when": ["측면"]},
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "D*0.85", "when": ["평면"]},
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "D/2", "when": ["평면"]},
        ],
    },
    {
        "name": "HEX_NUT",
        "label": "육각 너트 (KS B 1012)",
        "category": "기계",
        "base_point": "너트 중심 (0,0)",
        "params": [_p("D", "호칭 지름 M", 10, 3, 64)],
        "states": ["평면", "측면"],
        "entities": [
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "D*0.85", "when": ["평면"]},
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "D/2", "when": ["평면"]},
            {
                "type": "RECT",
                "x": "-D*0.4",
                "y": "-D*0.85",
                "w": "D*0.8",
                "h": "D*1.7",
                "when": ["측면"],
            },
            {
                "type": "LINE",
                "x1": "-D*0.4",
                "y1": "-D/2",
                "x2": "D*0.4",
                "y2": "-D/2",
                "when": ["측면"],
            },
            {
                "type": "LINE",
                "x1": "-D*0.4",
                "y1": "D/2",
                "x2": "D*0.4",
                "y2": "D/2",
                "when": ["측면"],
            },
        ],
    },
    {
        "name": "WASHER",
        "label": "평와셔 (KS B 1326)",
        "category": "기계",
        "base_point": "와셔 중심 (0,0)",
        "params": [_p("ID", "안 지름", 10.5, 3, 100), _p("OD", "바깥 지름", 21, 6, 200)],
        "entities": [
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "OD/2"},
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "ID/2"},
        ],
    },
    {
        "name": "BEARING",
        "label": "볼 베어링 단면",
        "category": "기계",
        "base_point": "축 중심 (0,0)",
        "params": [
            _p("ID", "내경", 20, 5, 300),
            _p("OD", "외경", 47, 15, 600),
            _p("B", "폭", 14, 4, 150),
        ],
        "entities": [
            {"type": "RECT", "x": "-B/2", "y": "ID/2", "w": "B", "h": "(OD-ID)/2"},
            {"type": "RECT", "x": "-B/2", "y": "-OD/2", "w": "B", "h": "(OD-ID)/2"},
            {"type": "LINE", "x1": "-B/2", "y1": "ID/2", "x2": "B/2", "y2": "OD/2"},
            {"type": "LINE", "x1": "-B/2", "y1": "OD/2", "x2": "B/2", "y2": "ID/2"},
            {"type": "LINE", "x1": "-B/2", "y1": "-ID/2", "x2": "B/2", "y2": "-OD/2"},
            {"type": "LINE", "x1": "-B/2", "y1": "-OD/2", "x2": "B/2", "y2": "-ID/2"},
        ],
    },
    {
        "name": "SURFACE_ROUGHNESS",
        "label": "표면거칠기 기호 (Ra)",
        "category": "기계",
        "base_point": "접촉 꼭짓점 (0,0)",
        "params": [_p("S", "기호 크기", 5, 2, 30), _p("RA", "Ra 값", 3.2, 0.1, 50)],
        "entities": [
            {"type": "LINE", "x1": "0", "y1": "0", "x2": "-S*0.6", "y2": "S"},
            {"type": "LINE", "x1": "0", "y1": "0", "x2": "S*1.1", "y2": "S*1.9"},
            {"type": "LINE", "x1": "-S*0.6", "y1": "S", "x2": "S*0.6", "y2": "S"},
            {"type": "TEXT", "x": "-S*0.4", "y": "S*1.25", "h": "S*0.45", "text": "Ra{RA}"},
        ],
    },
    # --- 건축 (Architectural) ---
    {
        "name": "DOOR",
        "label": "문 (여닫이)",
        "category": "건축",
        "base_point": "경첩 위치 (0,0)",
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
        "name": "SLIDING_DOOR",
        "label": "문 (미닫이 / 슬라이딩)",
        "category": "건축",
        "base_point": "개구부 좌측 끝 (0,0)",
        "params": [_p("W", "개구부 폭", 1800, 800, 6000), _p("T", "벽 두께", 150, 60, 400)],
        "entities": [
            {"type": "RECT", "x": "0", "y": "0", "w": "W", "h": "T"},
            {"type": "RECT", "x": "0", "y": "T*0.25", "w": "W*0.52", "h": "T*0.25"},
            {"type": "RECT", "x": "W*0.48", "y": "T*0.5", "w": "W*0.52", "h": "T*0.25"},
        ],
    },
    {
        "name": "WINDOW",
        "label": "창 (벽 개구부)",
        "category": "건축",
        "base_point": "좌측 벽체 중심선 (0,0)",
        "params": [_p("W", "창 폭", 1200, 300, 5000), _p("T", "벽 두께", 200, 50, 600)],
        "entities": [
            {"type": "RECT", "x": "0", "y": "-T/2", "w": "W", "h": "T"},
            {"type": "LINE", "x1": "0", "y1": "0", "x2": "W", "y2": "0"},
            {"type": "TEXT", "x": "W/2", "y": "T/2+50", "h": "100", "text": "W{W}"},
        ],
    },
    {
        "name": "TOILET",
        "label": "양변기 (위생기구 평면)",
        "category": "건축",
        "base_point": "벽면 중심 (0,0)",
        "params": [_p("W", "폭", 380, 300, 550), _p("D", "깊이", 680, 500, 850)],
        "entities": [
            {"type": "RECT", "x": "-W/2", "y": "0", "w": "W", "h": "D*0.28"},
            {"type": "CIRCLE", "cx": "0", "cy": "D*0.64", "r": "W*0.46"},
            {"type": "LINE", "x1": "-W*0.46", "y1": "D*0.28", "x2": "-W*0.46", "y2": "D*0.64"},
            {"type": "LINE", "x1": "W*0.46", "y1": "D*0.28", "x2": "W*0.46", "y2": "D*0.64"},
        ],
    },
    {
        "name": "BASIN",
        "label": "세면대 (위생기구 평면)",
        "category": "건축",
        "base_point": "벽면 중심 (0,0)",
        "params": [_p("W", "폭", 550, 350, 1200), _p("D", "깊이", 450, 300, 700)],
        "entities": [
            {"type": "RECT", "x": "-W/2", "y": "0", "w": "W", "h": "D"},
            {"type": "CIRCLE", "cx": "0", "cy": "D*0.55", "r": "min(W,D)*0.32"},
            {"type": "CIRCLE", "cx": "0", "cy": "D*0.18", "r": "20"},
        ],
    },
    {
        "name": "NORTH_ARROW",
        "label": "방위표 (North Arrow)",
        "category": "건축",
        "base_point": "중심 (0,0)",
        "params": [_p("R", "반지름", 150, 10, 2000)],
        "entities": [
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "R"},
            {"type": "LINE", "x1": "0", "y1": "R", "x2": "-R*0.45", "y2": "-R*0.65"},
            {"type": "LINE", "x1": "0", "y1": "R", "x2": "R*0.45", "y2": "-R*0.65"},
            {"type": "LINE", "x1": "-R*0.45", "y1": "-R*0.65", "x2": "0", "y2": "-R*0.25"},
            {"type": "LINE", "x1": "R*0.45", "y1": "-R*0.65", "x2": "0", "y2": "-R*0.25"},
            {"type": "TEXT", "x": "-R*0.2", "y": "R*1.15", "h": "R*0.5", "text": "N"},
        ],
    },
    # --- 배관 · 공조 (Plumbing & HVAC) ---
    {
        "name": "GATE_VALVE",
        "label": "게이트 밸브 (배관 심볼)",
        "category": "배관·공조",
        "base_point": "좌측 관 중심 끝단 (0,0)",
        "params": [_p("L", "길이", 200, 20, 2000), _p("W", "폭", 120, 10, 1200)],
        "entities": [
            {"type": "LINE", "x1": "0", "y1": "-W/2", "x2": "0", "y2": "W/2"},
            {"type": "LINE", "x1": "L", "y1": "-W/2", "x2": "L", "y2": "W/2"},
            {"type": "LINE", "x1": "0", "y1": "-W/2", "x2": "L", "y2": "W/2"},
            {"type": "LINE", "x1": "0", "y1": "W/2", "x2": "L", "y2": "-W/2"},
            {"type": "LINE", "x1": "L/2", "y1": "0", "x2": "L/2", "y2": "W*0.85"},
            {"type": "LINE", "x1": "L*0.25", "y1": "W*0.85", "x2": "L*0.75", "y2": "W*0.85"},
        ],
    },
    {
        "name": "CHECK_VALVE",
        "label": "체크 밸브 (역지변 심볼)",
        "category": "배관·공조",
        "base_point": "좌측 관 중심 끝단 (0,0)",
        "params": [_p("L", "길이", 200, 20, 2000), _p("W", "폭", 120, 10, 1200)],
        "entities": [
            {"type": "LINE", "x1": "0", "y1": "-W/2", "x2": "0", "y2": "W/2"},
            {"type": "LINE", "x1": "L", "y1": "-W/2", "x2": "L", "y2": "W/2"},
            {"type": "LINE", "x1": "0", "y1": "W/2", "x2": "L", "y2": "-W/2"},
            {"type": "CIRCLE", "cx": "L", "cy": "-W/2", "r": "W*0.12"},
        ],
    },
    {
        "name": "PIPE_ELBOW_90",
        "label": "90° 배관 엘보 피팅",
        "category": "배관·공조",
        "base_point": "곡률 중심 (0,0)",
        "params": [_p("R", "곡률 반지름", 150, 20, 2000), _p("D", "관 외경", 60, 10, 1000)],
        "entities": [
            {"type": "ARC", "cx": "0", "cy": "0", "r": "R+D/2", "a0": "0", "a1": "90"},
            {"type": "ARC", "cx": "0", "cy": "0", "r": "max(1,R-D/2)", "a0": "0", "a1": "90"},
            {"type": "LINE", "x1": "max(1,R-D/2)", "y1": "0", "x2": "R+D/2", "y2": "0"},
            {"type": "LINE", "x1": "0", "y1": "max(1,R-D/2)", "x2": "0", "y2": "R+D/2"},
        ],
    },
    {
        "name": "PIPE_TEE",
        "label": "배관 티(Tee) 피팅",
        "category": "배관·공조",
        "base_point": "배관 중심 교차점 (0,0)",
        "params": [
            _p("L", "주관 길이", 240, 30, 2000),
            _p("H", "분기 길이", 120, 15, 1000),
            _p("D", "관 외경", 60, 10, 800),
        ],
        "entities": [
            {"type": "LINE", "x1": "-L/2", "y1": "-D/2", "x2": "L/2", "y2": "-D/2"},
            {"type": "LINE", "x1": "-L/2", "y1": "D/2", "x2": "-D/2", "y2": "D/2"},
            {"type": "LINE", "x1": "D/2", "y1": "D/2", "x2": "L/2", "y2": "D/2"},
            {"type": "LINE", "x1": "-D/2", "y1": "D/2", "x2": "-D/2", "y2": "H"},
            {"type": "LINE", "x1": "D/2", "y1": "D/2", "x2": "D/2", "y2": "H"},
            {"type": "LINE", "x1": "-D/2", "y1": "H", "x2": "D/2", "y2": "H"},
        ],
    },
    {
        "name": "HVAC_FCU_4WAY",
        "label": "천장형 4-Way 냉난방기 (FCU)",
        "category": "배관·공조",
        "base_point": "실내기 중심 (0,0)",
        "params": [_p("W", "판넬 크기", 950, 400, 1800)],
        "entities": [
            {"type": "RECT", "x": "-W/2", "y": "-W/2", "w": "W", "h": "W"},
            {"type": "RECT", "x": "-W*0.32", "y": "-W*0.32", "w": "W*0.64", "h": "W*0.64"},
            {"type": "LINE", "x1": "-W/2", "y1": "-W/2", "x2": "W/2", "y2": "W/2"},
            {"type": "LINE", "x1": "-W/2", "y1": "W/2", "x2": "W/2", "y2": "-W/2"},
            {"type": "TEXT", "x": "-W*0.22", "y": "-W*0.08", "h": "W*0.14", "text": "FCU"},
        ],
    },
    # --- 전기 (Electrical) ---
    {
        "name": "PANEL_BOARD",
        "label": "전기 분전반 (MCCB/ELB)",
        "category": "전기",
        "base_point": "좌측 하단 (0,0)",
        "params": [_p("W", "폭", 600, 200, 2400), _p("D", "깊이", 200, 80, 800)],
        "entities": [
            {"type": "RECT", "x": "0", "y": "0", "w": "W", "h": "D"},
            {"type": "LINE", "x1": "0", "y1": "0", "x2": "W", "y2": "D"},
            {"type": "TEXT", "x": "W*0.1", "y": "D+40", "h": "100", "text": "LP-1"},
        ],
    },
    {
        "name": "OUTLET_2P",
        "label": "2구 접지 콘센트",
        "category": "전기",
        "base_point": "중심 (0,0)",
        "params": [_p("R", "기호 반지름", 80, 10, 500)],
        "entities": [
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "R"},
            {"type": "LINE", "x1": "-R*0.35", "y1": "-R*0.7", "x2": "-R*0.35", "y2": "R*0.7"},
            {"type": "LINE", "x1": "R*0.35", "y1": "-R*0.7", "x2": "R*0.35", "y2": "R*0.7"},
            {"type": "LINE", "x1": "0", "y1": "R", "x2": "0", "y2": "R*1.6"},
        ],
    },
    {
        "name": "SWITCH_1G",
        "label": "조명 스위치 (1로)",
        "category": "전기",
        "base_point": "중심 (0,0)",
        "params": [_p("R", "기호 반지름", 70, 10, 500)],
        "entities": [
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "R*0.6"},
            {"type": "LINE", "x1": "0", "y1": "0", "x2": "R*1.6", "y2": "R*1.6"},
            {"type": "LINE", "x1": "R*1.6", "y1": "R*1.6", "x2": "R*2.2", "y2": "R*1.2"},
        ],
    },
    {
        "name": "LED_PANEL",
        "label": "LED 조명 (평판 / 매입등)",
        "category": "전기",
        "base_point": "조명 중심 (0,0)",
        "params": [_p("W", "가로", 1200, 100, 2400), _p("H", "세로", 300, 100, 1200)],
        "states": ["평판등", "매입등"],
        "entities": [
            {"type": "RECT", "x": "-W/2", "y": "-H/2", "w": "W", "h": "H", "when": ["평판등"]},
            {
                "type": "LINE",
                "x1": "-W/2",
                "y1": "-H/2",
                "x2": "W/2",
                "y2": "H/2",
                "when": ["평판등"],
            },
            {"type": "CIRCLE", "cx": "0", "cy": "0", "r": "min(W,H)/2", "when": ["매입등"]},
            {
                "type": "LINE",
                "x1": "-min(W,H)*0.35",
                "y1": "-min(W,H)*0.35",
                "x2": "min(W,H)*0.35",
                "y2": "min(W,H)*0.35",
                "when": ["매입등"],
            },
            {
                "type": "LINE",
                "x1": "-min(W,H)*0.35",
                "y1": "min(W,H)*0.35",
                "x2": "min(W,H)*0.35",
                "y2": "-min(W,H)*0.35",
                "when": ["매입등"],
            },
        ],
    },
]

BUILTIN.extend(INTERIOR_BUILTIN)


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
    cat = t.get("category", "기계")
    if not isinstance(cat, str) or len(cat) > 30:
        cat = "기계"
    bp = t.get("base_point", "기준점 (0,0)")
    if not isinstance(bp, str) or len(bp) > 60:
        bp = "기준점 (0,0)"
    return {
        "name": t["name"],
        "label": label,
        "category": cat,
        "base_point": bp,
        "layer_policy": "0 / ByLayer",
        "params": params,
        "states": states,
        "entities": ents,
    }


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
