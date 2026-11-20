"""CAD Drawing Templates (KS B ISO 5457 / ISO 128) & Domain-Specific Template Samples.

Includes:
  - Standard layers (color, linetype, lineweight), KS dimension/text styles, mm units & limits
  - Drawing border, 4-side center marks, KS Title Block (title, doc_no, scale, 3rd-angle symbol,
    designer/approver, revision history)
  - 5 built-in templates:
      1. BLANK_KS        — Empty KS standard layers & limits (default, backward compatible)
      2. MECHANICAL_KS   — Mechanical/Manufacturing (3rd-angle, BOM table, roughness/GD&T blocks, notes)
      3. ARCHITECTURAL_KS — Architectural Plan (North arrow, section mark, door/window blocks, finish schedule)
      4. INTERIOR_RENO   — Interior Renovation (finish/lighting legend, furniture/downlight blocks)
      5. MEP_FACILITY    — Electrical/Plumbing/HVAC MEP (equipment legend, panel/outlet/FCU/valve blocks)
"""

from __future__ import annotations

import io
from typing import Any, Literal

import ezdxf
from ezdxf import units

TemplateCode = Literal[
    "BLANK_KS",
    "COMPANY_INTERNAL",
    "MECHANICAL_KS",
    "ARCHITECTURAL_KS",
    "INTERIOR_RENO",
    "MEP_FACILITY",
]

SHEETS: dict[str, tuple[int, int]] = {
    "A4": (297, 210),
    "A3": (420, 297),
    "A2": (594, 420),
    "A1": (841, 594),
    "A0": (1189, 841),
}

# name, ACI colour, linetype, lineweight (1/100 mm), description
LAYERS: list[tuple[str, int, str, int, str]] = [
    ("OUTLINE", 7, "Continuous", 50, "외형선 (굵은 실선)"),
    ("HIDDEN", 2, "DASHED", 25, "숨은선 (가는 파선)"),
    ("CENTER", 1, "CENTER", 25, "중심선 (가는 1점 쇄선)"),
    ("PHANTOM", 6, "PHANTOM", 25, "가상선 (가는 2점 쇄선)"),
    ("DIM", 3, "Continuous", 25, "치수선·지시선"),
    ("TEXT", 7, "Continuous", 25, "문자·주서"),
    ("HATCH", 8, "Continuous", 18, "해칭"),
]

_EXTRA_DOMAIN_LAYERS: dict[str, list[tuple[str, int, str, int, str]]] = {
    "COMMON_FRAME": [
        ("BORDER", 7, "Continuous", 50, "도면 윤곽선 · 중심 마크"),
        ("TITLEBLOCK", 7, "Continuous", 35, "표제란 · 부품란 · 일람표"),
        ("SYMBOLS", 4, "Continuous", 25, "표준 기호 · 블록"),
    ],
    "COMPANY_INTERNAL": [
        ("MEMO", 1, "DASHED", 25, "사내 검토 메모 · 수정 지시 (비공식)"),
        ("QUOTE_REF", 5, "Continuous", 25, "견적 · 발주 참조 치수/구간"),
    ],
    "MECHANICAL_KS": [
        ("BOM", 3, "Continuous", 25, "부품란 (Parts List)"),
    ],
    "ARCHITECTURAL_KS": [
        ("A-WALL", 7, "Continuous", 50, "건축 벽체"),
        ("A-DOOR", 3, "Continuous", 25, "창호 · 문"),
        ("A-WIND", 4, "Continuous", 25, "창호 · 창"),
        ("A-GRID", 1, "CENTER", 18, "건축 중심축 (Grid)"),
        ("A-FURN", 8, "Continuous", 18, "가구 · 집기"),
    ],
    "INTERIOR_RENO": [
        ("I-DEMO", 1, "DASHED", 25, "철거 대상선"),
        ("I-WALL", 7, "Continuous", 50, "신설 가벽 · 목공"),
        ("I-FURN", 3, "Continuous", 25, "가구 · 맞춤집기"),
        ("I-LIGHT", 2, "Continuous", 25, "조명 · 천장도"),
        ("I-FINISH", 6, "Continuous", 18, "마감재 표기"),
    ],
    "MEP_FACILITY": [
        ("E-POWER", 1, "Continuous", 35, "전기 동력 · 분전반"),
        ("E-LIGHT", 2, "Continuous", 25, "전기 조명 · 전열"),
        ("P-WATER", 4, "Continuous", 35, "급수 · 급탕 배관"),
        ("P-DRAIN", 3, "DASHED", 35, "오배수 배관"),
        ("M-HVAC", 6, "Continuous", 35, "공조 · 냉난방기 (HVAC)"),
    ],
}

TEMPLATE_CATALOG: list[dict[str, Any]] = [
    {
        "code": "BLANK_KS",
        "name": "표준 빈 도면 (KS 기본)",
        "category": "공통",
        "default_sheet": "A3",
        "default_scale": "1:1",
        "plot_style": "monochrome.ctb",
        "description": "KS B ISO 128 표준 레이어 7종, mm 단위계, 치수·문자 기본 스타일만 포함된 빈 도면",
        "includes": ["KS 표준 레이어 7종", "mm 단위계", "KS 치수/문자 스타일"],
    },
    {
        "code": "COMPANY_INTERNAL",
        "name": "회사 업무용 (비공식) 표준 템플릿 (2D·3D 공통)",
        "category": "사내 업무용(비공식)",
        "default_sheet": "A3",
        "default_scale": "1:1",
        "plot_style": "monochrome.ctb",
        "description": "신규 2D·3D 도면용 사내 비공식 실무 양식 — 간이 표제란, 검토 메모(MEMO)·견적참조(QUOTE_REF) 레이어, 간이 자재/공정 메모표, 비공식 검토 스탬프 포함",
        "includes": [
            "사내 비공식 간이 표제란",
            "검토 메모(MEMO) · 견적참조 레이어",
            "간이 자재·공정 메모표",
            "사내 검토용(비공식) 스탬프 블록",
        ],
    },
    {
        "code": "MECHANICAL_KS",
        "name": "기계 · 제조 표준 템플릿 (KS 제3각법)",
        "category": "기계·제조",
        "default_sheet": "A3",
        "default_scale": "1:1",
        "plot_style": "monochrome.ctb",
        "description": "윤곽선·중심마크·제3각법 표제란·부품란(BOM)·표면거칠기/기하공차 블록·일반공차 주서 포함",
        "includes": [
            "윤곽선 & 중심 마크",
            "제3각법 표제란 + Revision",
            "부품란(BOM) 표",
            "표면거칠기(Ra)·기하공차 블록",
            "KS 일반공차 주서",
        ],
    },
    {
        "code": "ARCHITECTURAL_KS",
        "name": "건축 평면도 표준 템플릿",
        "category": "건축",
        "default_sheet": "A1",
        "default_scale": "1:100",
        "plot_style": "architectural_gray.ctb",
        "description": "건축 레이어(벽체·창호·그리드), 윤곽선·표제란, 마감재 일람표, 방위표·단면기호·문/창 블록 포함",
        "includes": [
            "건축 레이어(A-WALL/DOOR/WIND/GRID)",
            "방위표(North) · 단면기호 블록",
            "문(900)·창(1800) 블록",
            "마감재 일람표",
        ],
    },
    {
        "code": "INTERIOR_RENO",
        "name": "인테리어 리노베이션 템플릿",
        "category": "인테리어",
        "default_sheet": "A3",
        "default_scale": "1:50",
        "plot_style": "interior_color.ctb",
        "description": "철거선(I-DEMO)·신설벽체·조명·가구 레이어, 표제란, 마감재/조명 범례, 가구·다운라이트 블록 포함",
        "includes": [
            "철거/신설/조명/가구 레이어",
            "마감재 & 조명 일람표",
            "책상·소파·LED 다운라이트 블록",
        ],
    },
    {
        "code": "MEP_FACILITY",
        "name": "전기 · 배관 · 공조(HVAC) 설비 템플릿",
        "category": "설비(MEP)",
        "default_sheet": "A3",
        "default_scale": "1:100",
        "plot_style": "mep_color.ctb",
        "description": "전기(동력/조명)·배관(급수/배수)·공조(HVAC) 레이어, 설비 범례표, 분전반·콘센트·냉난방기·밸브 블록 포함",
        "includes": [
            "전기·배관·공조 전용 레이어",
            "설비 범례표 (Legend)",
            "분전반·콘센트·FCU·밸브 블록",
        ],
    },
]

CONFIG_KEY = "AXCAD_DRAWING"  # $CUSTOMPROPERTY holding the settings of a drawing made from 3D (P3)
LTSCALE = 2.5


def list_template_samples() -> list[dict[str, Any]]:
    return TEMPLATE_CATALOG


def _add_rect(msp: Any, x0: float, y0: float, x1: float, y1: float, layer: str) -> None:
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer}
    )


def _add_table(
    msp: Any,
    x0: float,
    y_top: float,
    col_widths: list[float],
    rows: list[list[str]],
    row_h: float,
    layer: str,
    text_h: float = 2.6,
) -> None:
    total_w = sum(col_widths)
    for r_idx, row in enumerate(rows):
        yt = y_top - r_idx * row_h
        yb = yt - row_h
        _add_rect(msp, x0, yb, x0 + total_w, yt, layer)
        cx = x0
        for c_idx, cell in enumerate(row):
            if c_idx > 0:
                msp.add_line((cx, yt), (cx, yb), dxfattribs={"layer": layer})
            if cell:
                msp.add_text(
                    cell,
                    height=text_h,
                    dxfattribs={"layer": layer, "insert": (cx + 1.8, yb + (row_h - text_h) * 0.5)},
                )
            cx += col_widths[c_idx]


def _draw_border_and_center_marks(
    msp: Any, w: float, h: float
) -> tuple[float, float, float, float]:
    """Draw KS B ISO 5457 outer sheet frame, inner border (left 20mm binding, others 10mm), and 4 center marks."""
    # Outer sheet boundary
    _add_rect(msp, 0, 0, w, h, "BORDER")
    bx0, by0, bx1, by1 = 20.0, 10.0, w - 10.0, h - 10.0
    _add_rect(msp, bx0, by0, bx1, by1, "BORDER")

    # 4-side center marks extending 5mm inward from border
    mx, my = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
    msp.add_line((mx, by1 + 4.0), (mx, by1 - 5.0), dxfattribs={"layer": "BORDER"})
    msp.add_line((mx, by0 - 4.0), (mx, by0 + 5.0), dxfattribs={"layer": "BORDER"})
    msp.add_line((bx0 - 4.0, my), (bx0 + 5.0, my), dxfattribs={"layer": "BORDER"})
    msp.add_line((bx1 + 4.0, my), (bx1 - 5.0, my), dxfattribs={"layer": "BORDER"})
    return bx0, by0, bx1, by1


def _draw_title_block(
    msp: Any,
    bx1: float,
    by0: float,
    *,
    title: str,
    doc_no: str,
    scale_str: str,
    projection: str,
    material: str,
    plot_style: str,
) -> tuple[float, float]:
    """Draw standard KS Title Block at bottom-right of border. Returns (tb_x0, tb_y_top)."""
    col_w = [26.0, 44.0, 26.0, 44.0]  # total 140 mm
    row_h = 7.0
    rows = [
        ["도면명", title, "도면번호", doc_no],
        ["척도 (SCALE)", scale_str, "투상법", projection],
        ["재질 / 분야", material, "출력스타일", plot_style],
        ["작성", "설계자", "검토 / 승인", "팀장 / 부장"],
        ["REV", "00 (초도배포)", "작성일", "YYYY-MM-DD"],
    ]
    tb_w = sum(col_w)
    tb_h = row_h * len(rows)
    tb_x0 = bx1 - tb_w
    tb_y_top = by0 + tb_h
    _add_table(msp, tb_x0, tb_y_top, col_w, rows, row_h, "TITLEBLOCK", text_h=2.5)
    return tb_x0, tb_y_top


def _add_company_internal_frame(
    doc: Any,
    msp: Any,
    bx0: float,
    by0: float,
    bx1: float,
    by1: float,
    tb_x0: float,
    tb_y_top: float,
) -> None:
    """Internal unofficial working template: review stamp, quick material/process memo table, and watermark banner."""
    # Unofficial internal banner at top-right of border
    msp.add_text(
        "[INTERNAL / 사내 업무용 (비공식) — 외부 공식 배포 전 정식 표제란 전환 필요]",
        height=3.0,
        dxfattribs={"layer": "MEMO", "insert": (bx0 + 6.0, by1 - 6.5)},
    )
    # Reusable review stamp block
    blk_stamp = doc.blocks.new("SYM_INTERNAL_STAMP")
    _add_rect(blk_stamp, 0, 0, 46, 14, "MEMO")
    blk_stamp.add_line((0, 7), (46, 7), dxfattribs={"layer": "MEMO"})
    blk_stamp.add_text(
        "사내 검토용 (비공식)", height=2.6, dxfattribs={"layer": "MEMO", "insert": (4.0, 9.0)}
    )
    blk_stamp.add_text(
        "담당:       / 일자:", height=2.2, dxfattribs={"layer": "MEMO", "insert": (3.0, 2.2)}
    )
    msp.add_blockref("SYM_INTERNAL_STAMP", (bx1 - 52.0, by1 - 20.0), dxfattribs={"layer": "MEMO"})

    # Quick internal material / process / quote memo table above Title Block
    memo_cols = [28.0, 38.0, 24.0, 50.0]  # 140 mm
    memo_rows = [
        ["항목 / 부품", "재질 · 규격", "수량", "가공 · 시공 · 견적 메모"],
        ["본체 (2D/3D)", "SS400 / AL6061", "1", "레이저/절곡 또는 3D 실측 검토"],
        ["부자재 / 설비", "표준 규격품", "1식", "단가 확인 후 정식 견적 반영"],
        ["검토 사항", "-", "-", "현장 실측 치수 확인 필요"],
    ]
    _add_table(
        msp,
        tb_x0,
        tb_y_top + 6.5 * len(memo_rows),
        memo_cols,
        memo_rows,
        6.5,
        "QUOTE_REF",
        text_h=2.4,
    )

    # Bottom-left quick working notes
    notes = [
        "사내 업무용 체크리스트 (비공식):",
        "1. 단위: mm (1:1 기준 작도, 출력 시 축척 조정).",
        "2. 수정·검토 메모는 MEMO 레이어(적색 파선)에 기록.",
        "3. 3D Model 전환 시 닫힌 외곽선은 OUTLINE 레이어에 작성.",
    ]
    for i, line in enumerate(notes):
        msp.add_text(
            line,
            height=2.5,
            dxfattribs={"layer": "TEXT", "insert": (bx0 + 6.0, by0 + 23.0 - i * 5.0)},
        )


def _add_mechanical_symbols_and_bom(
    doc: Any,
    msp: Any,
    bx0: float,
    by0: float,
    bx1: float,
    by1: float,
    tb_x0: float,
    tb_y_top: float,
) -> None:
    # 1. Reusable blocks: Surface Roughness (Ra 3.2), GD&T Frame, 3rd-Angle Projection
    blk_r = doc.blocks.new("SYM_ROUGHNESS")
    blk_r.add_lwpolyline(
        [(0, 0), (-3, 5), (0, 0), (5.5, 9.5), (-3, 5)], dxfattribs={"layer": "SYMBOLS"}
    )
    blk_r.add_text("Ra 3.2", height=2.2, dxfattribs={"layer": "SYMBOLS", "insert": (-1.5, 6.2)})

    blk_gdt = doc.blocks.new("SYM_GDT_PERP")
    _add_rect(blk_gdt, 0, 0, 26, 6, "SYMBOLS")
    blk_gdt.add_line((6, 0), (6, 6), dxfattribs={"layer": "SYMBOLS"})
    blk_gdt.add_line((19, 0), (19, 6), dxfattribs={"layer": "SYMBOLS"})
    blk_gdt.add_line((1.5, 1.2), (4.5, 1.2), dxfattribs={"layer": "SYMBOLS"})
    blk_gdt.add_line((3.0, 1.2), (3.0, 4.8), dxfattribs={"layer": "SYMBOLS"})
    blk_gdt.add_text("0.05", height=2.5, dxfattribs={"layer": "SYMBOLS", "insert": (7.5, 1.8)})
    blk_gdt.add_text("A", height=2.5, dxfattribs={"layer": "SYMBOLS", "insert": (21.0, 1.8)})

    blk_proj = doc.blocks.new("SYM_THIRD_ANGLE")
    # Frustum (left) + concentric circles (right)
    blk_proj.add_lwpolyline(
        [(0, 1.5), (8, 3.0), (8, -3.0), (0, -1.5)], close=True, dxfattribs={"layer": "SYMBOLS"}
    )
    blk_proj.add_circle((14, 0), 3.0, dxfattribs={"layer": "SYMBOLS"})
    blk_proj.add_circle((14, 0), 1.5, dxfattribs={"layer": "SYMBOLS"})
    blk_proj.add_line((-2, 0), (19, 0), dxfattribs={"layer": "CENTER"})
    blk_proj.add_line((14, -4.5), (14, 4.5), dxfattribs={"layer": "CENTER"})

    # Place sample symbol blocks at top-left reference area
    msp.add_blockref("SYM_THIRD_ANGLE", (tb_x0 - 24.0, by0 + 14.0), dxfattribs={"layer": "SYMBOLS"})
    msp.add_blockref("SYM_ROUGHNESS", (bx0 + 12.0, by1 - 22.0), dxfattribs={"layer": "SYMBOLS"})
    msp.add_blockref("SYM_GDT_PERP", (bx0 + 28.0, by1 - 20.0), dxfattribs={"layer": "SYMBOLS"})

    # 2. Parts List (BOM) directly above Title Block
    bom_cols = [14.0, 46.0, 30.0, 32.0, 18.0]  # 140 mm
    bom_rows = [
        ["품번", "품명 (PART NAME)", "재질", "규격", "수량"],
        ["1", "BASE PLATE", "SS400", "120x80x12T", "1"],
        ["2", "SUPPORT BRACKET", "SUS304", "60x40x6T", "2"],
        ["3", "HEX BOLT M8x25", "SCM435", "KS B 1002", "4"],
    ]
    _add_table(
        msp, tb_x0, tb_y_top + 6.5 * len(bom_rows), bom_cols, bom_rows, 6.5, "BOM", text_h=2.4
    )

    # 3. Standard KS Mechanical Notes (bottom-left)
    notes = [
        "NOTE (주서):",
        "1. 지시 없는 일반공차는 KS B ISO 2768-mK 에 따른다.",
        "2. 지시 없는 모따기 C0.5, 필렛 R1.0, 날카로운 모서리 버 제거.",
        "3. 표면거칠기 기본값 Ra 3.2 (별도 표기 제외).",
    ]
    for i, line in enumerate(notes):
        msp.add_text(
            line,
            height=2.6,
            dxfattribs={"layer": "TEXT", "insert": (bx0 + 6.0, by0 + 24.0 - i * 5.2)},
        )


def _add_architectural_symbols_and_schedule(
    doc: Any,
    msp: Any,
    bx0: float,
    by0: float,
    bx1: float,
    by1: float,
    tb_x0: float,
    tb_y_top: float,
) -> None:
    # North Arrow block
    blk_n = doc.blocks.new("SYM_NORTH")
    blk_n.add_circle((0, 0), 8.0, dxfattribs={"layer": "SYMBOLS"})
    blk_n.add_lwpolyline(
        [(0, 8), (-4, -5), (0, -2), (4, -5)], close=True, dxfattribs={"layer": "SYMBOLS"}
    )
    blk_n.add_text("N", height=4.0, dxfattribs={"layer": "SYMBOLS", "insert": (-1.5, 9.5)})

    # Section mark block
    blk_s = doc.blocks.new("SYM_SECTION")
    blk_s.add_circle((0, 0), 5.0, dxfattribs={"layer": "SYMBOLS"})
    blk_s.add_line((-7, 0), (7, 0), dxfattribs={"layer": "SYMBOLS"})
    blk_s.add_text("A", height=3.0, dxfattribs={"layer": "SYMBOLS", "insert": (-1.2, 0.8)})

    # Door (900mm scaled 1:100 -> 9mm) & Window (1800mm -> 18mm)
    blk_door = doc.blocks.new("BLK_DOOR_900")
    blk_door.add_line((0, 0), (0, 9), dxfattribs={"layer": "A-DOOR"})
    blk_door.add_arc((0, 0), 9.0, 0, 90, dxfattribs={"layer": "A-DOOR"})

    blk_win = doc.blocks.new("BLK_WINDOW_1800")
    _add_rect(blk_win, 0, 0, 18, 2.0, "A-WIND")
    blk_win.add_line((0, 1.0), (18, 1.0), dxfattribs={"layer": "A-WIND"})

    msp.add_blockref("SYM_NORTH", (bx1 - 24.0, by1 - 24.0), dxfattribs={"layer": "SYMBOLS"})
    msp.add_blockref("SYM_SECTION", (bx0 + 18.0, by1 - 18.0), dxfattribs={"layer": "SYMBOLS"})
    msp.add_blockref("BLK_DOOR_900", (bx0 + 36.0, by1 - 22.0), dxfattribs={"layer": "A-DOOR"})
    msp.add_blockref("BLK_WINDOW_1800", (bx0 + 54.0, by1 - 19.0), dxfattribs={"layer": "A-WIND"})

    # Finish Schedule above Title Block
    fin_cols = [26.0, 38.0, 38.0, 38.0]  # 140 mm
    fin_rows = [
        ["실명", "바닥 마감", "벽체 마감", "천장 마감"],
        ["사무실", "OA 플로어+타일카펫", "석고2P 수성도장", "텍스 천장재"],
        ["복도/홀", "포세린 타일 600각", "인테리어 필름", "석고보드 도장"],
        ["화장실", "논슬립 타일 300각", "자기질 타일", "SMC 천장재"],
    ]
    _add_table(
        msp,
        tb_x0,
        tb_y_top + 6.5 * len(fin_rows),
        fin_cols,
        fin_rows,
        6.5,
        "TITLEBLOCK",
        text_h=2.4,
    )


def _add_interior_symbols_and_schedule(
    doc: Any,
    msp: Any,
    bx0: float,
    by0: float,
    bx1: float,
    by1: float,
    tb_x0: float,
    tb_y_top: float,
) -> None:
    blk_desk = doc.blocks.new("BLK_DESK_1600")
    _add_rect(blk_desk, 0, 0, 32, 16, "I-FURN")
    blk_desk.add_text("DESK 1600", height=2.2, dxfattribs={"layer": "I-FURN", "insert": (3, 6.5)})

    blk_sofa = doc.blocks.new("BLK_SOFA_2100")
    _add_rect(blk_sofa, 0, 0, 42, 18, "I-FURN")
    _add_rect(blk_sofa, 2, 13, 40, 17, "I-FURN")

    blk_led = doc.blocks.new("SYM_LED_DOWNLIGHT")
    blk_led.add_circle((0, 0), 3.0, dxfattribs={"layer": "I-LIGHT"})
    blk_led.add_line((-2.1, -2.1), (2.1, 2.1), dxfattribs={"layer": "I-LIGHT"})
    blk_led.add_line((-2.1, 2.1), (2.1, -2.1), dxfattribs={"layer": "I-LIGHT"})

    msp.add_blockref("BLK_DESK_1600", (bx0 + 10.0, by1 - 28.0), dxfattribs={"layer": "I-FURN"})
    msp.add_blockref("BLK_SOFA_2100", (bx0 + 50.0, by1 - 28.0), dxfattribs={"layer": "I-FURN"})
    msp.add_blockref(
        "SYM_LED_DOWNLIGHT", (bx0 + 102.0, by1 - 18.0), dxfattribs={"layer": "I-LIGHT"}
    )

    cols = [30.0, 55.0, 55.0]  # 140 mm
    rows = [
        ["구분", "자재 / 규격", "비고 (시공 기준)"],
        ["바닥", "강마루 / 포세린 타일", "수평몰탈 시공 후 마감"],
        ["벽체", "실크도배 / 인테리어 필름", "퍼티 2회 바탕 정리"],
        ["조명", "LED 매입등 15W (3000K)", "회로별 디밍 스위치 분리"],
    ]
    _add_table(msp, tb_x0, tb_y_top + 6.5 * len(rows), cols, rows, 6.5, "TITLEBLOCK", text_h=2.4)


def _add_mep_symbols_and_legend(
    doc: Any,
    msp: Any,
    bx0: float,
    by0: float,
    bx1: float,
    by1: float,
    tb_x0: float,
    tb_y_top: float,
) -> None:
    blk_pb = doc.blocks.new("SYM_PANEL_BOARD")
    _add_rect(blk_pb, 0, 0, 12, 6, "E-POWER")
    blk_pb.add_line((0, 0), (12, 6), dxfattribs={"layer": "E-POWER"})

    blk_out = doc.blocks.new("SYM_OUTLET")
    blk_out.add_circle((0, 0), 3.0, dxfattribs={"layer": "E-LIGHT"})
    blk_out.add_line((-1.2, -2.0), (-1.2, 2.0), dxfattribs={"layer": "E-LIGHT"})
    blk_out.add_line((1.2, -2.0), (1.2, 2.0), dxfattribs={"layer": "E-LIGHT"})

    blk_fcu = doc.blocks.new("SYM_HVAC_FCU")
    _add_rect(blk_fcu, 0, 0, 16, 16, "M-HVAC")
    blk_fcu.add_line((0, 0), (16, 16), dxfattribs={"layer": "M-HVAC"})
    blk_fcu.add_line((0, 16), (16, 0), dxfattribs={"layer": "M-HVAC"})

    blk_vlv = doc.blocks.new("SYM_VALVE")
    blk_vlv.add_lwpolyline(
        [(0, 0), (0, 6), (8, 0), (8, 6)], close=True, dxfattribs={"layer": "P-WATER"}
    )

    msp.add_blockref("SYM_PANEL_BOARD", (bx0 + 10.0, by1 - 20.0), dxfattribs={"layer": "E-POWER"})
    msp.add_blockref("SYM_OUTLET", (bx0 + 32.0, by1 - 17.0), dxfattribs={"layer": "E-LIGHT"})
    msp.add_blockref("SYM_HVAC_FCU", (bx0 + 44.0, by1 - 26.0), dxfattribs={"layer": "M-HVAC"})
    msp.add_blockref("SYM_VALVE", (bx0 + 70.0, by1 - 20.0), dxfattribs={"layer": "P-WATER"})

    cols = [30.0, 50.0, 60.0]  # 140 mm
    rows = [
        ["기호 / 레이어", "설비 항목", "규격 / 시공 기준"],
        ["E-POWER", "분전반 (MCCB/ELB)", "3상 4선 380/220V 접지본딩"],
        ["P-WATER", "급수 배관 / 밸브", "STS304 25A, 수압 10kgf/cm²"],
        ["M-HVAC", "시스템 냉난방기 (4Way)", "동배관 보온 19T, 질소기밀"],
    ]
    _add_table(msp, tb_x0, tb_y_top + 6.5 * len(rows), cols, rows, 6.5, "TITLEBLOCK", text_h=2.4)


def blank_dxf(
    sheet: str = "A3",
    portrait: bool = False,
    template: TemplateCode = "BLANK_KS",
    *,
    title: str = "AX-CAD STANDARD DRAWING",
    doc_no: str = "AX-0001",
) -> bytes:
    """Create a standards-compliant DXF/DWT template drawing."""
    w, h = SHEETS[sheet]
    if portrait:
        w, h = h, w
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.MM
    doc.header["$MEASUREMENT"] = 1  # metric
    doc.header["$LTSCALE"] = LTSCALE
    # Standard KS dimension variables
    doc.header["$DIMTXT"] = 2.5
    doc.header["$DIMASZ"] = 2.5
    doc.header["$DIMEXO"] = 1.25
    doc.header["$DIMEXE"] = 2.0
    doc.header["$DIMGAP"] = 1.0

    layout = doc.modelspace().dxf_layout.dxf
    layout.limmin, layout.limmax = (0, 0), (w, h)

    for name, color, linetype, lineweight, description in LAYERS:
        layer = doc.layers.add(name, color=color, linetype=linetype, lineweight=lineweight)
        layer.description = description

    if template != "BLANK_KS":
        for grp in ("COMMON_FRAME", template):
            for name, color, linetype, lineweight, description in _EXTRA_DOMAIN_LAYERS.get(grp, []):
                if name not in doc.layers:
                    layer = doc.layers.add(
                        name, color=color, linetype=linetype, lineweight=lineweight
                    )
                    layer.description = description

        msp = doc.modelspace()
        bx0, by0, bx1, by1 = _draw_border_and_center_marks(msp, float(w), float(h))
        meta = next((m for m in TEMPLATE_CATALOG if m["code"] == template), TEMPLATE_CATALOG[1])
        tb_x0, tb_y_top = _draw_title_block(
            msp,
            bx1,
            by0,
            title=title if title != "AX-CAD STANDARD DRAWING" else str(meta["name"]),
            doc_no=doc_no,
            scale_str=str(meta["default_scale"]),
            projection="제3각법" if template == "MECHANICAL_KS" else "정투상",
            material=str(meta["category"]),
            plot_style=str(meta["plot_style"]),
        )
        if template == "COMPANY_INTERNAL":
            _add_company_internal_frame(doc, msp, bx0, by0, bx1, by1, tb_x0, tb_y_top)
        elif template == "MECHANICAL_KS":
            _add_mechanical_symbols_and_bom(doc, msp, bx0, by0, bx1, by1, tb_x0, tb_y_top)
        elif template == "ARCHITECTURAL_KS":
            _add_architectural_symbols_and_schedule(doc, msp, bx0, by0, bx1, by1, tb_x0, tb_y_top)
        elif template == "INTERIOR_RENO":
            _add_interior_symbols_and_schedule(doc, msp, bx0, by0, bx1, by1, tb_x0, tb_y_top)
        elif template == "MEP_FACILITY":
            _add_mep_symbols_and_legend(doc, msp, bx0, by0, bx1, by1, tb_x0, tb_y_top)

    doc.header["$CLAYER"] = "OUTLINE"
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
