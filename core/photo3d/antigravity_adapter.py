"""Antigravity Python SDK (`google-antigravity`) Backend Adapter for Photo-to-3D AI Reasoning.

Uses official `google.antigravity` (`Agent`, `LocalAgentConfig`) in strict read-only mode
(no write/shell capabilities) when installed and explicitly opted in (`use_ai=True`).
Gracefully falls back to deterministic local geometry inference when the SDK is not installed
or offline/CI environments have no runtime credentials.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import re
from typing import Any

from . import SilhouetteSummary

_SYSTEM_INSTRUCTIONS = (
    "You are a precision 3D CAD reconstruction planner for AX-CAD. "
    "Given multi-view silhouette summaries (view labels, pixel bounding boxes, fill ratios, "
    "circularities, hole counts) and calibrated real-world dimensions in millimeters, "
    "output ONLY a single JSON object (no markdown, no explanation) choosing the best parametric "
    "3D CAD primitive or extrusion plan:\n"
    '{"kind": "BOX"|"CYLINDER"|"CONE"|"SPHERE", '
    '"width_mm": float, "depth_mm": float, "height_mm": float, '
    '"radius_mm": float, "top_radius_mm": float, "confidence": float, "notes": str}'
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def antigravity_sdk_status() -> dict[str, Any]:
    """Check whether `google-antigravity` Python SDK is installed and importable."""
    try:
        mod = importlib.import_module("google.antigravity")
        has_agent = hasattr(mod, "Agent") and hasattr(mod, "LocalAgentConfig")
        return {
            "sdk_installed": has_agent,
            "package": "google-antigravity",
            "provider": "antigravity-sdk" if has_agent else "local-geometry",
        }
    except (ImportError, OSError, RuntimeError):
        return {
            "sdk_installed": False,
            "package": "google-antigravity",
            "provider": "local-geometry",
        }


def _validate_ai_plan(
    raw_text: str, fallback_dims: tuple[float, float, float]
) -> dict[str, Any] | None:
    """Parse and strictly validate the JSON feature plan returned by Antigravity Agent."""
    m = _JSON_OBJ_RE.search(raw_text.strip())
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None

    kind = str(obj.get("kind", "")).upper()
    if kind not in ("BOX", "CYLINDER", "CONE", "SPHERE"):
        return None

    def _pos(key: str, default: float) -> float:
        val = obj.get(key, default)
        try:
            f = float(val)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(f) or f <= 0.1 or f > 1e5:
            return default
        return round(f, 3)

    w, d, h = fallback_dims
    plan: dict[str, Any] = {
        "kind": kind,
        "width_mm": _pos("width_mm", w),
        "depth_mm": _pos("depth_mm", d),
        "height_mm": _pos("height_mm", h),
        "radius_mm": _pos("radius_mm", 0.5 * min(w, d)),
        "top_radius_mm": _pos("top_radius_mm", 0.25 * min(w, d)),
        "confidence": max(0.0, min(1.0, float(obj.get("confidence", 0.8)))),
        "notes": str(obj.get("notes", ""))[:200],
    }
    return plan


async def _run_antigravity_agent(prompt: str, timeout_s: float = 30.0) -> str:
    """Spawn a read-only Antigravity SDK Agent (no write tools) and collect its response."""
    mod = importlib.import_module("google.antigravity")
    agent_cls = mod.Agent
    cfg_cls = mod.LocalAgentConfig
    # Strict read-only: omit CapabilitiesConfig() so write/shell tools stay disabled
    config = cfg_cls(system_instructions=_SYSTEM_INSTRUCTIONS)

    async def _chat() -> str:
        async with agent_cls(config) as agent:
            response = await agent.chat(prompt)
            chunks: list[str] = []
            async for token in response:
                chunks.append(str(token))
            return "".join(chunks)

    return await asyncio.wait_for(_chat(), timeout=timeout_s)


def infer_parametric_plan_with_antigravity(
    summaries: list[SilhouetteSummary],
    estimated_dims_mm: tuple[float, float, float],
    *,
    mode: str,
    timeout_s: float = 30.0,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Invoke Antigravity SDK to infer an editable parametric 3D CAD plan from view summaries.

    Returns (validated_plan_or_none, provider_name, warning_or_none).
    Never raises on SDK unavailability — always degrades safely to local geometry fallback.
    """
    st = antigravity_sdk_status()
    if not st["sdk_installed"]:
        return (
            None,
            "local-geometry",
            "Antigravity SDK(google-antigravity)가 서버에 미설치되어 로컬 기하 복원 엔진으로 실행했습니다.",
        )

    payload = {
        "mode": mode,
        "estimated_dims_mm": {
            "width_x": round(estimated_dims_mm[0], 3),
            "depth_y": round(estimated_dims_mm[1], 3),
            "height_z": round(estimated_dims_mm[2], 3),
        },
        "views": [
            {
                "label": s.view_label,
                "angle_deg": s.angle_deg,
                "width_px": s.width_px,
                "height_px": s.height_px,
                "fill_ratio": s.fill_ratio,
                "circularity": s.circularity,
                "hole_count": s.hole_count,
            }
            for s in summaries
        ],
    }
    prompt = (
        f"Analyze these calibrated multi-view silhouettes and return JSON:\n{json.dumps(payload)}"
    )

    try:
        try:
            asyncio.get_running_loop()
            # If called inside an active event loop, run in a dedicated thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                raw_text = pool.submit(
                    asyncio.run, _run_antigravity_agent(prompt, timeout_s)
                ).result(timeout=timeout_s + 5.0)
        except RuntimeError:
            raw_text = asyncio.run(_run_antigravity_agent(prompt, timeout_s))

        plan = _validate_ai_plan(raw_text, estimated_dims_mm)
        if plan is None:
            return (
                None,
                "local-geometry",
                "Antigravity SDK 응답 JSON 검증에 실패하여 로컬 기하 엔진으로 폴백했습니다.",
            )
        return plan, "antigravity-sdk", None
    except (TimeoutError, OSError, RuntimeError, ValueError, ImportError) as exc:
        return (
            None,
            "local-geometry",
            f"Antigravity SDK 호출 오류({type(exc).__name__})로 로컬 기하 엔진으로 폴백했습니다.",
        )
