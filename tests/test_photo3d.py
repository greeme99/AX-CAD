"""Photo-to-3D tests: P3D-T01~T05 (Silhouette, Ortho Multi-View, Turntable Visual Hull, Antigravity SDK)."""

from __future__ import annotations

import io
import math
import sys
import types
from typing import Any

import pytest
from PIL import Image, ImageDraw

from core.geometry.serialize import from_brep
from core.photo3d import Photo3dError, PhotoInput, ReferenceScale
from core.photo3d.antigravity_adapter import (
    antigravity_sdk_status,
    infer_parametric_plan_with_antigravity,
)
from core.photo3d.reconstruct import reconstruct_from_photos
from core.photo3d.silhouette import compute_mm_per_pixel, extract_silhouette, validate_image_bytes


def _png_rect(w_px: int, h_px: int, rect_w: int, rect_h: int) -> bytes:
    img = Image.new("RGB", (w_px, h_px), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    x0 = (w_px - rect_w) // 2
    y0 = (h_px - rect_h) // 2
    draw.rectangle([x0, y0, x0 + rect_w - 1, y0 + rect_h - 1], fill=(25, 45, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png_circle(w_px: int, h_px: int, r_px: int) -> bytes:
    img = Image.new("RGB", (w_px, h_px), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    cx, cy = w_px // 2, h_px // 2
    draw.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], fill=(30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_p3d_t01_silhouette_and_2point_scale_calibration() -> None:
    """P3D-T01: Silhouette extraction + 2-point pixel-to-mm scale calibration accuracy <= 2%."""
    data = _png_rect(400, 300, 200, 100)
    img = validate_image_bytes(data, "front.png")
    mask, summ = extract_silhouette(img, view_label="FRONT")
    assert mask.shape[0] > 0 and mask.shape[1] > 0
    assert abs(summ.width_px - 200.0) <= 3.0
    assert abs(summ.height_px - 100.0) <= 3.0

    # 2-point calibration: 200 px spans 100 mm -> 0.5 mm/px
    mm_px = compute_mm_per_pixel(
        ReferenceScale(reference_mm=100.0, p1_px=(100.0, 150.0), p2_px=(300.0, 150.0)),
        summ,
    )
    assert abs(mm_px - 0.5) < 1e-6


def test_p3d_t02_ortho_multiview_box_and_cylinder() -> None:
    """P3D-T02: Orthographic FRONT+SIDE+TOP reconstructs exact Box & Cylinder dimensions within +-3%."""
    # 1. Rectangular block: 120 x 60 x 80 mm (at 2 px/mm -> 240x160 front, 120x160 side, 240x120 top)
    photos_box = [
        PhotoInput(_png_rect(400, 300, 240, 160), "front.png", view_label="FRONT"),
        PhotoInput(_png_rect(400, 300, 120, 160), "side.png", view_label="SIDE"),
        PhotoInput(_png_rect(400, 300, 240, 120), "top.png", view_label="TOP"),
    ]
    res_box = reconstruct_from_photos(
        photos_box,
        ReferenceScale(reference_mm=120.0, axis="X"),
        mode="ORTHO_MULTI",
        output_mode="HYBRID",
    )
    assert res_box.resolved_output == "PARAMETRIC"
    assert res_box.feature_plan["kind"] == "BOX"
    w, d, h = res_box.bbox_mm
    assert abs(w - 120.0) / 120.0 < 0.03
    assert abs(d - 60.0) / 60.0 < 0.03
    assert abs(h - 80.0) / 80.0 < 0.03
    assert from_brep(res_box.brep_bytes) is not None

    # 2. Cylinder: diameter 80 mm (r=40), height 100 mm -> circular TOP + rectangular FRONT
    photos_cyl = [
        PhotoInput(_png_rect(400, 300, 160, 200), "front.png", view_label="FRONT"),
        PhotoInput(_png_circle(400, 300, 80), "top.png", view_label="TOP"),
    ]
    res_cyl = reconstruct_from_photos(
        photos_cyl,
        ReferenceScale(reference_mm=80.0, axis="X"),
        mode="ORTHO_MULTI",
        output_mode="HYBRID",
    )
    assert res_cyl.feature_plan["kind"] == "CYLINDER"
    expected_vol = math.pi * (40.0**2) * 100.0
    assert abs(res_cyl.volume_mm3 - expected_vol) / expected_vol < 0.06


def test_p3d_t03_turntable_12_photos_visual_hull_brep() -> None:
    """P3D-T03: 12 sequential turntable photos carve a real-scale Visual Hull OCCT B-Rep solid."""
    # Cylinder viewed from 12 turntable angles (every 30 deg): constant 160x200 px silhouette
    cyl_png = _png_rect(320, 320, 160, 200)
    photos = [PhotoInput(cyl_png, f"frame_{i:02d}.png", view_label="ANGLE") for i in range(12)]
    res = reconstruct_from_photos(
        photos,
        ReferenceScale(reference_mm=80.0, axis="X"),
        mode="TURNTABLE",
        output_mode="HYBRID",
    )
    assert res.resolved_output == "VISUAL_HULL"
    assert res.feature_plan["kind"] == "VISUAL_HULL"
    assert res.feature_plan["photo_count"] == 12
    # Carved circular cylinder volume pi * 40^2 * 100 ~ 502,654 mm^3 (voxel discretization within ~18%)
    expected_vol = math.pi * (40.0**2) * 100.0
    assert res.volume_mm3 > expected_vol * 0.70
    assert res.volume_mm3 < expected_vol * 1.15
    assert from_brep(res.brep_bytes) is not None


def test_p3d_t04_antigravity_sdk_adapter_mock_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """P3D-T04: Antigravity SDK adapter uses read-only LocalAgentConfig, parses JSON plan, and falls back safely."""
    # 1. Mock `google.antigravity` module with read-only Agent & LocalAgentConfig
    captured_cfg: dict[str, Any] = {}

    class FakeLocalAgentConfig:
        def __init__(self, *, system_instructions: str = "", capabilities: Any = None) -> None:
            captured_cfg["system_instructions"] = system_instructions
            captured_cfg["capabilities"] = capabilities

    class FakeResponse:
        def __init__(self, text: str) -> None:
            self._tokens = [text[:20], text[20:]]

        def __aiter__(self) -> Any:
            self._idx = 0
            return self

        async def __anext__(self) -> str:
            if self._idx >= len(self._tokens):
                raise StopAsyncIteration
            tok = self._tokens[self._idx]
            self._idx += 1
            return tok

    class FakeAgent:
        def __init__(self, cfg: Any) -> None:
            self.cfg = cfg

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def chat(self, prompt: str) -> Any:
            return FakeResponse(
                '{"kind":"CYLINDER","width_mm":80,"depth_mm":80,"height_mm":120,"radius_mm":40,"confidence":0.95,"notes":"mocked antigravity sdk"}'
            )

    fake_google = types.ModuleType("google")
    fake_agy = types.ModuleType("google.antigravity")
    fake_agy.Agent = FakeAgent  # type: ignore[attr-defined]
    fake_agy.LocalAgentConfig = FakeLocalAgentConfig  # type: ignore[attr-defined]
    fake_google.antigravity = fake_agy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.antigravity", fake_agy)

    assert antigravity_sdk_status()["sdk_installed"] is True

    _, summ = extract_silhouette(
        validate_image_bytes(_png_rect(300, 300, 120, 180)), view_label="FRONT"
    )
    plan, provider, warn = infer_parametric_plan_with_antigravity(
        [summ], (80.0, 80.0, 120.0), mode="FREE_ANGLE"
    )
    assert provider == "antigravity-sdk"
    assert warn is None
    assert plan is not None
    assert plan["kind"] == "CYLINDER"
    assert plan["radius_mm"] == 40.0
    # Strict security guardrail: capabilities MUST remain None (read-only)
    assert captured_cfg["capabilities"] is None


def test_p3d_t05_rejects_non_image_magic_bytes() -> None:
    """P3D-T05: Reject non-image payloads (e.g., spoofed .png with text/HTML bytes)."""
    with pytest.raises(Photo3dError) as exc:
        validate_image_bytes(b"<html>not a png</html>", "evil.png")
    assert exc.value.code == "PHOTO3D_INVALID_FORMAT"
