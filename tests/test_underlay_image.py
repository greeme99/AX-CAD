"""Tests for 2D Image Underlay (PNG, JPG, SVG, WEBP, BMP) upload, overlay, and auto-trace."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from core.underlay import UnderlayError, image, pdf, trace


def _sample_png(*, rgba: bool = False) -> bytes:
    mode = "RGBA" if rgba else "RGB"
    bg = (255, 255, 255, 0) if rgba else (255, 255, 255)
    fg = (0, 0, 0, 255) if rgba else (0, 0, 0)
    im = Image.new(mode, (400, 300), bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([40, 40, 360, 260], outline=fg, width=4)
    draw.ellipse([150, 100, 250, 200], outline=fg, width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG", dpi=(96, 96))
    return buf.getvalue()


def _sample_jpeg() -> bytes:
    im = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.rectangle([30, 30, 290, 210], outline=(0, 0, 0), width=4)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _sample_svg() -> bytes:
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">'
        b'<rect x="10" y="10" width="180" height="80" fill="none" stroke="#000"/>'
        b'<circle cx="100" cy="50" r="20" fill="none" stroke="#000"/>'
        b'<line x1="10" y1="50" x2="190" y2="50" stroke="#000"/>'
        b'<g transform="translate(20, 10)">'
        b'<path d="M 0 0 L 30 0 L 30 20 Z" fill="none" stroke="#000"/>'
        b"</g>"
        b"</svg>"
    )


def test_png_to_pdf_and_auto_trace(tmp_path):
    pdf_bytes = image.image_to_pdf(_sample_png())
    assert pdf_bytes.startswith(b"%PDF")
    p = tmp_path / "part.pdf"
    p.write_bytes(pdf_bytes)

    meta = pdf.info(str(p))
    assert meta["page_count"] == 1
    assert meta["pages"][0]["width"] > 50

    res = trace.trace_page("pdf", str(p), 0, mode="auto")
    assert res["source"] == "raster"
    assert len(res["lines"]) + len(res["polylines"]) + len(res["circles"]) > 0
    assert res["traced_bbox_mm"] is not None


def test_rgba_png_and_jpeg_conversion(tmp_path):
    for name, raw in (("alpha.png", _sample_png(rgba=True)), ("photo.jpg", _sample_jpeg())):
        pdf_bytes = image.image_to_pdf(raw)
        assert pdf_bytes.startswith(b"%PDF")
        p = tmp_path / f"{name}.pdf"
        p.write_bytes(pdf_bytes)
        meta = pdf.info(str(p))
        assert meta["page_count"] == 1


def test_svg_vector_to_pdf_and_vector_trace(tmp_path):
    pdf_bytes = image.image_to_pdf(_sample_svg())
    assert pdf_bytes.startswith(b"%PDF")
    p = tmp_path / "drawing.pdf"
    p.write_bytes(pdf_bytes)

    meta = pdf.info(str(p))
    assert meta["page_count"] == 1
    assert meta["pages"][0]["width"] == pytest.approx(200.0, abs=1.0)
    assert meta["pages"][0]["height"] == pytest.approx(100.0, abs=1.0)

    vec = pdf.page_vectors(str(p), 0)
    assert vec["raster_only"] is False
    assert len(vec["paths"]) + len(vec["arcs"]) >= 4

    res = trace.trace_page("pdf", str(p), 0, mode="auto")
    assert res["source"] == "vector"
    assert len(res["lines"]) + len(res["polylines"]) + len(res["circles"]) >= 3


def test_security_rejects_xxe_svg_and_bad_bytes():
    xxe_svg = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>'
    with pytest.raises(UnderlayError) as exc:
        image.image_to_pdf(xxe_svg)
    assert exc.value.code == "UNDERLAY_FORMAT"

    with pytest.raises(UnderlayError) as exc2:
        image.image_to_pdf(b"not an image at all")
    assert exc2.value.code == "UNDERLAY_FORMAT"


def test_api_upload_image_and_trace_e2e(client, world):
    h = world.h["designer"]
    # 1. PNG upload -> 200 & kind='pdf' & original filename preserved
    r_png = client.post(
        f"/api/projects/{world.pid}/underlays",
        files={"file": ("bracket_sketch.png", _sample_png(), "image/png")},
        headers=h,
    )
    assert r_png.status_code == 200, r_png.text
    row_png = r_png.json()["data"]
    assert row_png["kind"] == "pdf"
    assert row_png["name"] == "bracket_sketch.png"

    tr_png = client.post(
        f"/api/underlays/{row_png['underlay_id']}/pages/0/trace",
        json={"mode": "auto"},
        headers=h,
    )
    assert tr_png.status_code == 200, tr_png.text
    assert tr_png.json()["data"]["source"] == "raster"

    # 2. SVG upload -> 200 & vector auto-trace
    r_svg = client.post(
        f"/api/projects/{world.pid}/underlays",
        files={"file": ("plate.svg", _sample_svg(), "image/svg+xml")},
        headers=h,
    )
    assert r_svg.status_code == 200, r_svg.text
    row_svg = r_svg.json()["data"]
    assert row_svg["name"] == "plate.svg"

    tr_svg = client.post(
        f"/api/underlays/{row_svg['underlay_id']}/pages/0/trace",
        json={"mode": "auto"},
        headers=h,
    )
    assert tr_svg.status_code == 200, tr_svg.text
    assert tr_svg.json()["data"]["source"] == "vector"
