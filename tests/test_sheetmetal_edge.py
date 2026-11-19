"""Sheet Metal S1~S5 & DWG acadrust boundary / regression tests (review TASK-007).

Passing tests guard S2/S4/S5/DWG invariants today.
Tests marked ``@pytest.mark.xfail(strict=True)`` lock in verified P0/P1 defects
from ``docs/reviews/sheet-ui-dwg-review.md``; remove ``xfail`` as each defect is fixed.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

import pytest

from core.dxf import dwg
from core.dxf.reader import DxfError
from core.geometry import sheetmetal as sm
from core.geometry.errors import GeomError
from core.geometry.metrics import measure
from core.geometry.serialize import from_brep


def _rect(w: float, h: float, x: float = 0.0, y: float = 0.0) -> list[dict]:
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [
        {"type": "LINE", "start": list(a), "end": list(b)}
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True)
    ]


def _spec(
    geoms: list[dict],
    flanges: list[dict],
    *,
    t: float = 2.0,
    r: float = 2.0,
    k: float = 0.5,
    cuts: list[dict] | None = None,
    forms: list[dict] | None = None,
    show_flat: bool = False,
) -> dict:
    s: dict = {
        "outline": sm.outline(geoms),
        "thickness": t,
        "bend_radius": r,
        "k_factor": k,
        "flanges": flanges,
        "plane": None,
    }
    if cuts:
        s["cuts"] = cuts
    if forms:
        s["forms"] = forms
    if show_flat:
        s["show_flat"] = True
    return s


def _fl(
    edge: int,
    length: float,
    *,
    angle: float = 90.0,
    up: bool = True,
    inset_start: float = 0.0,
    inset_end: float = 0.0,
    **extra: object,
) -> dict:
    return {
        "edge": edge,
        "length": length,
        "angle": angle,
        "up": up,
        "inset_start": inset_start,
        "inset_end": inset_end,
        **extra,
    }


# ---------------------------------------------------------------------------
# 1. Active boundary guards (must PASS today)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form_type", ["EMBOSS", "DIMPLE", "RIB", "BRIDGE", "LOUVER", "LANCE"])
def test_s4_form_6_types_valid_depth_and_directions(form_type: str) -> None:
    """All 6 S4 forming types succeed at depth >= t for up=True and up=False."""
    for up in (True, False):
        s = _spec(
            _rect(100, 80),
            [],
            t=2.0,
            forms=[
                {
                    "form_type": form_type,
                    "geoms": _rect(20, 10, x=30, y=30),
                    "depth": 4.0,
                    "up": up,
                    "after": 0,
                }
            ],
        )
        shape = from_brep(sm.sheet_job(s)[0])
        m = measure(shape)
        assert m["solids"] == 1
        assert m["volume_mm3"] > 0


def test_s2_s5_180_hem_and_acute_bend_metrics() -> None:
    """180-deg hem counts as hem (not acute); 120-deg bend counts as acute."""
    s_hem = _spec(_rect(100, 60), [_fl(0, 15, angle=180.0)], t=2.0, r=1.0, k=0.5)
    fp_hem = sm.flat(s_hem)
    assert fp_hem["metrics"]["hem_count"] == 1
    assert fp_hem["metrics"]["acute_bend_count"] == 0

    s_acute = _spec(_rect(100, 60), [_fl(0, 20, angle=120.0)], t=2.0, r=2.0, k=0.5)
    fp_acute = sm.flat(s_acute)
    assert fp_acute["metrics"]["hem_count"] == 0
    assert fp_acute["metrics"]["acute_bend_count"] == 1


def test_s3b_show_flat_with_flat_cut_succeeds() -> None:
    """show_flat=True with a flat-mode cut produces a valid single-solid body."""
    s = _spec(
        _rect(100, 60),
        [_fl(0, 30)],
        t=2.0,
        r=2.0,
        cuts=[{"geoms": _rect(10, 10, x=40, y=25), "flat": True, "after": 1}],
        show_flat=True,
    )
    shape = from_brep(sm.sheet_job(s)[0])
    m = measure(shape)
    assert m["solids"] == 1
    assert m["volume_mm3"] > 0


# ---------------------------------------------------------------------------
# 2. Verified P0/P1 defect locks (strict xfail — remove xfail when fixed)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="SH-01 (P0): _flat_cut_tool extrudes ±(t*10+100)mm and pierces opposite U-channel flange",
)
def test_sh01_flat_cut_does_not_pierce_opposite_u_channel_flange() -> None:
    """A flat cut placed on Flange 1 must remove only one hole's volume, not pierce Flange 2."""
    base = _spec(_rect(100, 50), [_fl(0, 30), _fl(2, 30)], t=2.0, r=2.0, k=0.5)
    vol_uncut = measure(from_brep(sm.sheet_job(base)[0]))["volume_mm3"]

    # Place a 10x10 hole squarely on Flange 1 (y < 0 in flat coordinates, beyond bend allowance)
    ba = (math.pi / 2) * (2.0 + 0.5 * 2.0)
    cut_y = -(ba + 15.0)
    with_cut = _spec(
        _rect(100, 50),
        [_fl(0, 30), _fl(2, 30)],
        t=2.0,
        r=2.0,
        k=0.5,
        cuts=[{"geoms": _rect(10, 10, x=45, y=cut_y - 5.0), "flat": True, "after": 2}],
    )
    vol_cut = measure(from_brep(sm.sheet_job(with_cut)[0]))["volume_mm3"]
    removed = vol_uncut - vol_cut
    expected_single_hole = 10.0 * 10.0 * 2.0  # 200 mm^3 (bug removes ~400 mm^3)
    assert removed == pytest.approx(expected_single_hole, rel=0.05)


@pytest.mark.xfail(
    strict=True,
    reason="SH-02 (P0): convert_sharp_spec fillets inside & outside with same R (r_in = R - t <= 0)",
)
def test_sh02_convert_sharp_radius_smaller_than_thickness_positive_r_in() -> None:
    """Converting a sharp L-bracket with R < t must not produce negative inner radius on unfold."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    t = 2.0
    a = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 60.0, 40.0, t).Shape()
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 60.0, t, 30.0).Shape()
    fused = BRepAlgoAPI_Fuse(a, b)
    fused.Build()
    l_shape = fused.Shape()

    spec_sharp = sm.convert_sharp_spec(l_shape, bend_radius=1.0, k_factor=0.5)
    fp = sm.flat(spec_sharp)
    assert all(bend["radius"] > 0 for bend in fp["bends"])
    assert all(bend["allowance"] > 0 for bend in fp["bends"])


@pytest.mark.xfail(
    strict=True,
    reason="SH-08 (P1): outline() skips _simple() on hole loops and check() accepts NaN length",
)
def test_sh08_check_rejects_nan_flange_length() -> None:
    """Direct call to sm.check() with NaN flange length must raise GeomError."""
    s = _spec(_rect(100, 50), [_fl(0, float("nan"))])
    with pytest.raises(GeomError):
        sm.check(s)


@pytest.mark.xfail(
    strict=True,
    reason="SH-07 (P1): dwg.info() advertises R12 for acadrust even though DWG forbids R12",
)
def test_sh07_acadrust_info_excludes_r12() -> None:
    """acadrust only writes DWG, which forbids R12; info()['versions'] must omit R12."""
    with patch.object(dwg, "converter", return_value=("C:/tools/acadrust.exe", "acadrust")):
        meta = dwg.info()
    assert meta["kind"] == "acadrust"
    assert "R12" not in meta["versions"]


@pytest.mark.xfail(
    strict=True,
    reason="SH-07 (P1): export_drawing() ignores non-zero exit code from acadrust",
)
def test_sh07_acadrust_nonzero_exit_raises_dwg_convert_failed() -> None:
    """Non-zero exit code from acadrust must raise DxfError even if partial header bytes exist."""

    def fake_run(args: list[str], tmp: str, size: int) -> int:
        out_idx = args.index("--output") + 1
        Path(args[out_idx]).write_bytes(b"AC1032partial-corrupt-output")
        return 1

    with (
        patch.object(dwg, "converter", return_value=("C:/tools/acadrust.exe", "acadrust")),
        patch.object(dwg, "_run", side_effect=fake_run),
        pytest.raises(DxfError) as exc,
    ):
        dwg.export_drawing(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", "DWG", "2018")
    assert exc.value.code == "DWG_CONVERT_FAILED"
