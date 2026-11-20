"""Sample architectural & interior floorplan DXF generator with placed standard blocks.

Generates ready-to-open DXF floorplans (Apartment 84m2, Office 150m2) using real
AX-CAD parametric standard blocks (`PB-*`, Layer `0 / ByLayer`, canonical `(0,0)` base point)
placed as DXF `INSERT` block references with preserved `axcad_pb` descriptions.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace

from core.dxf import param_blocks as pb

_BUILTIN_MAP: dict[str, dict[str, Any]] = {t["name"]: pb.validate(t) for t in pb.BUILTIN}


def _setup_layers(doc: Drawing, specs: list[tuple[str, int]]) -> None:
    for name, color in specs:
        if name not in doc.layers:
            doc.layers.add(name, color=color)


def _add_rect(msp: Modelspace, x: float, y: float, w: float, h: float, layer: str) -> None:
    msp.add_lwpolyline(
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
        close=True,
        dxfattribs={"layer": layer},
    )


def _add_wall_line(
    msp: Modelspace, x1: float, y1: float, x2: float, y2: float, layer: str = "A-WALL"
) -> None:
    msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})


def _add_label(
    msp: Modelspace, x: float, y: float, text: str, height: float = 220, layer: str = "A-ANNO-TEXT"
) -> None:
    msp.add_text(text, dxfattribs={"layer": layer, "height": height, "insert": (x, y)})


def _ensure_block(
    doc: Drawing,
    template_name: str,
    values: dict[str, Any] | None = None,
    state: str | None = None,
) -> str:
    tpl = _BUILTIN_MAP[template_name]
    placed = pb.instantiate(tpl, values or {}, state)
    bname: str = placed["name"]
    if bname in doc.blocks:
        return bname

    blk = doc.blocks.new(name=bname, base_point=(0.0, 0.0, 0.0))
    if blk.block is not None:
        blk.block.dxf.description = placed["description"]

    # Standard CAD block policy: Layer 0 + ByLayer color/linetype
    base_attrs = {"layer": "0", "color": 256, "linetype": "BYLAYER"}
    for g in placed["geoms"]:
        gtype = g["type"]
        if gtype == "LINE":
            blk.add_line(tuple(g["start"]), tuple(g["end"]), dxfattribs=base_attrs)
        elif gtype == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in g["points"]]
            blk.add_lwpolyline(pts, close=bool(g.get("closed", False)), dxfattribs=base_attrs)
        elif gtype == "CIRCLE":
            blk.add_circle(tuple(g["center"]), float(g["radius"]), dxfattribs=base_attrs)
        elif gtype == "ARC":
            blk.add_arc(
                tuple(g["center"]),
                float(g["radius"]),
                float(g["start_angle"]),
                float(g["end_angle"]),
                dxfattribs=base_attrs,
            )
        elif gtype == "TEXT":
            blk.add_text(
                str(g["value"]),
                dxfattribs={
                    **base_attrs,
                    "height": float(g["height"]),
                    "insert": tuple(g["insert"]),
                    "rotation": float(g.get("rotation", 0.0)),
                },
            )
    return bname


def _place(
    doc: Drawing,
    msp: Modelspace,
    template_name: str,
    x: float,
    y: float,
    *,
    layer: str,
    rotation: float = 0.0,
    values: dict[str, Any] | None = None,
    state: str | None = None,
) -> str:
    bname = _ensure_block(doc, template_name, values=values, state=state)
    msp.add_blockref(
        bname,
        (x, y),
        dxfattribs={"layer": layer, "rotation": rotation},
    )
    return bname


def _draw_title_frame(
    msp: Modelspace, width: float, height: float, title: str, subtitle: str
) -> None:
    margin = 1200.0
    _add_rect(msp, -margin, -margin, width + 2 * margin, height + 2 * margin, "A-TITLE")
    tb_w, tb_h = 4200.0, 900.0
    tb_x = width + margin - tb_w
    tb_y = -margin
    _add_rect(msp, tb_x, tb_y, tb_w, tb_h, "A-TITLE")
    _add_label(msp, tb_x + 150, tb_y + 520, title, height=240, layer="A-TITLE")
    _add_label(msp, tb_x + 150, tb_y + 180, subtitle, height=160, layer="A-TITLE")


def _to_dxf_bytes(doc: Drawing) -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def build_apartment_84m2_dxf() -> bytes:
    """Build 84m2 3-Bay Korean Apartment Sample Floorplan DXF with standard interior blocks."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()

    _setup_layers(
        doc,
        [
            ("A-WALL", 7),
            ("A-DOOR", 3),
            ("A-WIND", 4),
            ("A-FURN", 2),
            ("A-KITCH", 6),
            ("A-BATH", 5),
            ("A-BALC", 30),
            ("A-ANNO-TEXT", 1),
            ("A-TITLE", 8),
        ],
    )

    # Outer envelope: 11,400 x 8,400 mm (Veranda y=0..1500, Main rooms y=1500..8400)
    w, h = 11400.0, 8400.0
    _add_rect(msp, 0, 0, w, h, "A-WALL")
    _add_wall_line(msp, 0, 1500, w, 1500)  # Veranda partition
    _add_wall_line(msp, 3800, 1500, 3800, 8400)  # Left bay (Master bed + Master bath)
    _add_wall_line(msp, 7800, 1500, 7800, 8400)  # Right bay (Bed 2 + Main bath + Entrance)
    _add_wall_line(msp, 0, 5600, 3800, 5600)  # Master bath divider
    _add_wall_line(msp, 7800, 5600, 11400, 5600)  # Main bath divider
    _add_wall_line(msp, 9600, 5600, 9600, 8400)  # Entrance vestibule divider

    # --- 창호·문 ---
    _place(doc, msp, "DOOR_ENTRANCE", 10000, 8400, layer="A-DOOR", rotation=180, state="좌열림")
    _place(doc, msp, "WINDOW_BALCONY", 4000, 1500, layer="A-WIND", values={"W": 3600, "T": 240})
    _place(doc, msp, "WINDOW_SLIDING", 700, 1500, layer="A-WIND", values={"W": 2400, "T": 220})
    _place(doc, msp, "WINDOW_SLIDING", 8400, 1500, layer="A-WIND", values={"W": 2400, "T": 220})
    _place(doc, msp, "SLIDING_DOOR", 1200, 0, layer="A-DOOR", values={"W": 1800, "T": 150})
    _place(doc, msp, "DOOR", 3800, 4600, layer="A-DOOR", rotation=90, state="왼쪽")
    _place(doc, msp, "DOOR", 7800, 4600, layer="A-DOOR", rotation=90, state="오른쪽")
    _place(doc, msp, "DOOR", 2600, 5600, layer="A-DOOR", values={"W": 750}, state="왼쪽")
    _place(doc, msp, "DOOR", 8200, 5600, layer="A-DOOR", values={"W": 750}, state="오른쪽")

    # --- 현관 ---
    _place(doc, msp, "SHOE_CABINET", 9650, 6200, layer="A-FURN", values={"W": 1600, "D": 380})

    # --- 거실 (x=3800..7800, y=1500..5200) ---
    _place(doc, msp, "SOFA_3SEAT", 5800, 2100, layer="A-FURN", state="카우치형")
    _place(doc, msp, "COFFEE_TABLE", 5800, 3600, layer="A-FURN", state="사각")
    _place(doc, msp, "TV_CONSOLE", 5800, 4900, layer="A-FURN", rotation=180)

    # --- 안방 (x=0..3800, y=1500..5600) ---
    _place(doc, msp, "BED_QUEEN", 1900, 1800, layer="A-FURN", state="협탁포함")
    _place(doc, msp, "WARDROBE", 200, 4800, layer="A-FURN", values={"W": 2400, "D": 650})

    # --- 침실 2 / 서재 (x=7800..11400, y=1500..5600) ---
    _place(doc, msp, "STUDY_DESK", 8200, 1800, layer="A-FURN")
    _place(doc, msp, "BOOKSHELF", 10000, 1800, layer="A-FURN")
    _place(doc, msp, "WARDROBE", 8200, 4800, layer="A-FURN", values={"W": 2000, "D": 600})

    # --- 주방·식당 (x=3800..7800, y=5200..8400) ---
    _place(doc, msp, "KITCHEN_SINK", 4100, 7700, layer="A-KITCH", state="2조")
    _place(doc, msp, "KITCHEN_COOKTOP", 5900, 8000, layer="A-KITCH", state="3구")
    _place(doc, msp, "DISHWASHER", 6650, 7750, layer="A-KITCH")
    _place(doc, msp, "REFRIGERATOR", 7300, 7500, layer="A-KITCH", state="양문형")
    _place(doc, msp, "MICROWAVE_TOWER", 3850, 6800, layer="A-KITCH")
    _place(doc, msp, "KITCHEN_ISLAND", 4600, 6200, layer="A-KITCH")
    _place(doc, msp, "DINING_TABLE", 5800, 5600, layer="A-FURN", state="6인용")

    # --- 안방 욕실 (x=0..3800, y=5600..8400) ---
    _place(doc, msp, "SHOWER_BOOTH", 200, 7300, layer="A-BATH")
    _place(doc, msp, "VANITY_CABINET", 1800, 7850, layer="A-BATH")
    _place(doc, msp, "BIDET_TOILET", 3100, 7650, layer="A-BATH")
    _place(doc, msp, "BATH_DRAIN", 1800, 6600, layer="A-BATH")

    # --- 공용 욕실 (x=7800..9600, y=5600..8400) ---
    _place(doc, msp, "BATHTUB", 7900, 7550, layer="A-BATH")
    _place(doc, msp, "TOILET", 8400, 6100, layer="A-BATH")
    _place(doc, msp, "BASIN", 9100, 6100, layer="A-BATH")
    _place(doc, msp, "BATH_DRAIN", 8700, 6900, layer="A-BATH")

    # --- 남측 베란다 & 다용도실 (x=0..11400, y=0..1500) ---
    _place(doc, msp, "WASHER_DRYER", 1100, 300, layer="A-BALC", state="병렬2대")
    _place(doc, msp, "DRYING_RACK", 5800, 750, layer="A-BALC")
    _place(doc, msp, "PLANTER_BOX", 8200, 200, layer="A-BALC")
    _place(doc, msp, "AC_OUTDOOR_UNIT", 10400, 300, layer="A-BALC")

    # --- 실명 주석 & 방위표 & 표제란 ---
    _add_label(msp, 1200, 3500, "MASTER BED (안방)")
    _add_label(msp, 5100, 3100, "LIVING ROOM (거실)")
    _add_label(msp, 8800, 3500, "BEDROOM 2 (침실2)")
    _add_label(msp, 4900, 7200, "KITCHEN & DINING (주방·식당)")
    _add_label(msp, 1000, 6100, "MASTER BATH")
    _add_label(msp, 8000, 5800, "BATH 2")
    _add_label(msp, 9900, 7400, "ENTRANCE (현관)")
    _add_label(msp, 4800, 200, "VERANDA / BALCONY (베란다)")
    _place(doc, msp, "NORTH_ARROW", -600, h + 400, layer="A-ANNO-TEXT", values={"R": 300})
    _draw_title_frame(
        msp,
        w,
        h,
        "AX-CAD SAMPLE: APARTMENT 84m2 (3-BAY)",
        "SCALE 1:50 (mm) | STANDARD BLOCKS: 33 INSTANCES | LAYER 0 / ByLayer",
    )
    return _to_dxf_bytes(doc)


def build_office_150m2_dxf() -> bytes:
    """Build 150m2 IT & Engineering Office Sample Floorplan DXF with standard office/MEP blocks."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()

    _setup_layers(
        doc,
        [
            ("A-WALL", 7),
            ("A-DOOR", 3),
            ("A-WIND", 4),
            ("I-OFFC", 2),
            ("A-FURN", 30),
            ("A-KITCH", 6),
            ("A-BATH", 5),
            ("E-LTNG", 40),
            ("M-HVAC", 140),
            ("A-ANNO-TEXT", 1),
            ("A-TITLE", 8),
        ],
    )

    # Outer envelope: 15,000 x 10,000 mm (150m2)
    w, h = 15000.0, 10000.0
    _add_rect(msp, 0, 0, w, h, "A-WALL")
    # Executive office (top-left x=0..4500, y=6200..10000)
    _add_wall_line(msp, 0, 6200, 4500, 6200)
    _add_wall_line(msp, 4500, 6200, 4500, 10000)
    # Conference room (top-right x=10000..15000, y=5800..10000)
    _add_wall_line(msp, 10000, 5800, 15000, 5800)
    _add_wall_line(msp, 10000, 5800, 10000, 10000)
    # Restroom (bottom-right x=11600..15000, y=0..3000)
    _add_wall_line(msp, 11600, 3000, 15000, 3000)
    _add_wall_line(msp, 11600, 0, 11600, 3000)

    # --- 창호·문 ---
    _place(doc, msp, "DOOR_DOUBLE", 1200, 0, layer="A-DOOR", values={"W": 1800})
    _place(doc, msp, "DOOR_POCKET", 3200, 6200, layer="A-DOOR")
    _place(doc, msp, "DOOR_DOUBLE", 11200, 5800, layer="A-DOOR", values={"W": 1600})
    _place(doc, msp, "WINDOW_SLIDING", 1000, 10000, layer="A-WIND", values={"W": 2600})
    _place(doc, msp, "WINDOW_SLIDING", 11200, 10000, layer="A-WIND", values={"W": 2800})
    _place(doc, msp, "DOOR", 12100, 3000, layer="A-DOOR", values={"W": 800}, state="왼쪽")
    _place(doc, msp, "DOOR", 13600, 3000, layer="A-DOOR", values={"W": 800}, state="오른쪽")

    # --- 로비 & 리셉션 (x=0..4500, y=0..4500) ---
    _place(doc, msp, "RECEPTION_DESK", 1000, 2600, layer="I-OFFC")
    _place(doc, msp, "SOFA_3SEAT", 1800, 600, layer="A-FURN", state="일자형")
    _place(doc, msp, "COFFEE_TABLE", 3800, 1200, layer="A-FURN", state="원형")

    # --- 메인 업무존 (x=4500..10500, y=1000..9000) ---
    _place(doc, msp, "OFFICE_PARTITION", 6800, 3200, layer="I-OFFC", state="4인클러스터")
    _place(doc, msp, "OFFICE_PARTITION", 6800, 7400, layer="I-OFFC", state="4인클러스터")
    _place(doc, msp, "OFFICE_PARTITION", 9600, 3200, layer="I-OFFC", state="2인대면")
    _place(doc, msp, "FILE_CABINET", 4700, 100, layer="I-OFFC")
    _place(doc, msp, "FILE_CABINET", 5800, 100, layer="I-OFFC")

    # --- 임원실 (x=0..4500, y=6200..10000) ---
    _place(doc, msp, "OFFICE_DESK", 1200, 7600, layer="I-OFFC", state="L자형")
    _place(doc, msp, "BOOKSHELF", 200, 9400, layer="A-FURN", values={"W": 2400, "D": 400})

    # --- 대회의실 (x=10000..15000, y=5800..10000) ---
    _place(doc, msp, "CONFERENCE_TABLE", 12500, 7900, layer="I-OFFC")
    _place(doc, msp, "TV_CONSOLE", 14500, 7900, layer="A-FURN", rotation=90)

    # --- OA존 & 탕비실 (x=11200..15000, y=3200..5600) ---
    _place(doc, msp, "MFP_PRINTER", 12200, 4800, layer="I-OFFC")
    _place(doc, msp, "WATER_PURIFIER", 13200, 5000, layer="I-OFFC")
    _place(doc, msp, "KITCHEN_SINK", 13600, 4900, layer="A-KITCH", values={"W": 1100}, state="1조")
    _place(doc, msp, "REFRIGERATOR", 14400, 3600, layer="A-KITCH", state="단문형")
    _place(doc, msp, "PANEL_BOARD", 11200, 3400, layer="E-LTNG")

    # --- 화장실 (x=11600..15000, y=0..3000) ---
    _place(doc, msp, "TOILET", 12300, 200, layer="A-BATH")
    _place(doc, msp, "TOILET", 13300, 200, layer="A-BATH")
    _place(doc, msp, "BASIN", 12300, 2200, layer="A-BATH")
    _place(doc, msp, "BASIN", 13300, 2200, layer="A-BATH")
    _place(doc, msp, "BATH_DRAIN", 14300, 1400, layer="A-BATH")

    # --- 천장 공조 & 조명 (MEP) ---
    _place(doc, msp, "HVAC_FCU_4WAY", 6800, 5200, layer="M-HVAC")
    _place(doc, msp, "HVAC_FCU_4WAY", 12500, 7900, layer="M-HVAC")
    for lx in (5500, 8100):
        for ly in (2200, 8200):
            _place(doc, msp, "LED_PANEL", lx, ly, layer="E-LTNG", state="평판등")

    # --- 실명 주석 & 방위표 & 표제란 ---
    _add_label(msp, 1000, 4200, "LOBBY / RECEPTION (로비·안내)")
    _add_label(msp, 5500, 5200, "OPEN WORKSPACE (메인 업무존)")
    _add_label(msp, 800, 6600, "EXECUTIVE OFFICE (임원실)")
    _add_label(msp, 10800, 6200, "CONFERENCE ROOM (대회의실)")
    _add_label(msp, 11800, 3800, "OA / PANTRY (OA·탕비실)")
    _add_label(msp, 12000, 1200, "RESTROOM (화장실)")
    _place(doc, msp, "NORTH_ARROW", -600, h + 400, layer="A-ANNO-TEXT", values={"R": 300})
    _draw_title_frame(
        msp,
        w,
        h,
        "AX-CAD SAMPLE: OFFICE 150m2 (IT / ENGINEERING)",
        "SCALE 1:50 (mm) | STANDARD BLOCKS: 32 INSTANCES | LAYER 0 / ByLayer",
    )
    return _to_dxf_bytes(doc)


def generate_sample_floorplans(output_dir: Path) -> list[Path]:
    """Write pre-generated Apartment 84m2 & Office 150m2 sample DXF files to `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [
        ("AXCAD_Sample_Apartment_84m2.dxf", build_apartment_84m2_dxf()),
        ("AXCAD_Sample_Office_150m2.dxf", build_office_150m2_dxf()),
    ]
    written: list[Path] = []
    for fname, raw in files:
        path = output_dir / fname
        path.write_bytes(raw)
        written.append(path)
    return written
