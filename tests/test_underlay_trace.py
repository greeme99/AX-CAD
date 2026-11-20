"""Tests for 2D Overlay PDF Image Auto-Trace & Scale/Dimension Detection."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from core.underlay.trace import detect_scale_and_dimensions, trace_page


def _make_raster_pdf(dest: Path) -> None:
    """Create a 200x150 mm PDF containing ONLY a scanned raster image (rectangle + circle, no scale/dims)."""
    w_px, h_px = 1000, 750  # 5 px per mm
    img = Image.new("RGB", (w_px, h_px), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Filled dark rectangle: x in [20, 120] mm (w=100 mm), y in [25, 85] mm (h=60 mm)
    # PIL y is top-down: y_px = h_px - y_mm * 5
    draw.rectangle([20 * 5, h_px - 85 * 5, 120 * 5, h_px - 25 * 5], fill=(0, 0, 0))
    # Filled dark circle: center (160, 75) mm, radius 18 mm
    cx, cy, r = 160 * 5, h_px - 75 * 5, 18 * 5
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0))

    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_buf.seek(0)

    c = canvas.Canvas(str(dest), pagesize=(200 * mm, 150 * mm))
    c.drawImage(ImageReader(png_buf), 0, 0, width=200 * mm, height=150 * mm)
    c.save()


def _make_vector_pdf(dest: Path, *, with_scale_and_dims: bool) -> None:
    c = canvas.Canvas(str(dest), pagesize=(200 * mm, 150 * mm))
    c.rect(20 * mm, 20 * mm, 100 * mm, 50 * mm)
    c.circle(160 * mm, 50 * mm, 15 * mm)
    if with_scale_and_dims:
        c.drawString(20 * mm, 130 * mm, "SCALE 1:50")
        c.drawString(65 * mm, 12 * mm, "100.0")
    c.save()


def test_trace_raster_pdf_needs_reference_dim_and_extracts_shapes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan_bare.pdf"
    _make_raster_pdf(pdf_path)

    res = trace_page("pdf", str(pdf_path), 0, mode="auto")
    assert res["source"] == "raster"
    assert res["scale_detected"] is None
    assert res["has_dimensions"] is False
    assert res["needs_reference_dimension"] is True
    assert res["traced_bbox_mm"] is not None

    # Should detect the 100x60 mm rectangle as a closed polyline and the R=18 mm circle
    assert res["stats"]["polyline_count"] >= 1
    assert res["stats"]["circle_count"] >= 1
    circ = res["circles"][0]
    assert abs(circ["center"][0] - 160.0) < 2.0
    assert abs(circ["center"][1] - 75.0) < 2.0
    assert abs(circ["radius"] - 18.0) < 2.0

    # Overall traced width should span from ~20 to ~178 mm
    bbox = res["traced_bbox_mm"]
    assert abs(bbox[0] - 20.0) < 2.5
    assert abs(bbox[2] - 178.0) < 2.5


def test_trace_vector_pdf_scale_detection(tmp_path: Path) -> None:
    pdf_with = tmp_path / "vec_scaled.pdf"
    pdf_bare = tmp_path / "vec_bare.pdf"
    _make_vector_pdf(pdf_with, with_scale_and_dims=True)
    _make_vector_pdf(pdf_bare, with_scale_and_dims=False)

    res_with = trace_page("pdf", str(pdf_with), 0, mode="auto")
    assert res_with["source"] == "vector"
    assert res_with["scale_detected"] == 50.0
    assert res_with["has_dimensions"] is True
    assert res_with["needs_reference_dimension"] is False
    assert res_with["stats"]["polyline_count"] >= 1
    assert res_with["stats"]["circle_count"] >= 1

    res_bare = trace_page("pdf", str(pdf_bare), 0, mode="auto")
    assert res_bare["scale_detected"] is None
    assert res_bare["has_dimensions"] is False
    assert res_bare["needs_reference_dimension"] is True


def test_detect_scale_patterns() -> None:
    s1, d1 = detect_scale_and_dimensions({"texts": [{"v": "척도 1/100"}, {"v": "Ø25"}]})
    assert s1 == 100.0
    assert d1 is True
    s2, d2 = detect_scale_and_dimensions({"texts": [{"v": "TITLE BLOCK ONLY"}]})
    assert s2 is None
    assert d2 is False
