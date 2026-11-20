"""Assembly A6: Kinematics Motion Study — Motors, Gear, Rack-Pinion & Screw Couplings."""

from __future__ import annotations

import math
from typing import Any

from core.geometry.assembly_large import AABB, bvh_overlapping_pairs
from core.geometry.errors import GeomError


def _norm3(v: list[float] | tuple[float, ...]) -> tuple[float, float, float]:
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    n = math.sqrt(x * x + y * y + z * z)
    if n <= 1e-12:
        raise GeomError("GEOM_INVALID_PARAM", "motion axis/direction cannot be zero vector")
    return x / n, y / n, z / n


def simulate_motion(
    components: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    couplings: list[dict[str, Any]] | None = None,
    *,
    duration_s: float = 2.0,
    steps: int = 20,
    check_clashes: bool = True,
) -> dict[str, Any]:
    """Run kinematic motion timeline for motors (revolute/prismatic) and mechanical couplings (GEAR, RACK_PINION, SCREW).

    Driver types:
      - MOTOR_ROTATE: {"type": "MOTOR_ROTATE", "component_id": int, "axis": [ax,ay,az], "deg_per_s": float}
      - MOTOR_LINEAR: {"type": "MOTOR_LINEAR", "component_id": int, "direction": [dx,dy,dz], "mm_per_s": float}
    Coupling types (driven by master component's incremental angle/translation):
      - GEAR: {"type": "GEAR", "master_id": int, "slave_id": int, "ratio": float, "axis": [ax,ay,az]}
              -> slave_deg = -ratio * master_deg (external meshing reversal)
      - RACK_PINION: {"type": "RACK_PINION", "master_id": int, "slave_id": int, "pitch_radius_mm": float, "direction": [dx,dy,dz]}
              -> slave_mm = pitch_radius_mm * radians(master_deg)
      - SCREW: {"type": "SCREW", "master_id": int, "slave_id": int, "pitch_mm": float, "direction": [dx,dy,dz]}
              -> slave_mm = (pitch_mm / 360.0) * master_deg
    """
    if duration_s <= 0 or not math.isfinite(duration_s):
        raise GeomError("GEOM_INVALID_PARAM", "duration_s must be > 0")
    if not (1 <= steps <= 500):
        raise GeomError("GEOM_INVALID_PARAM", "steps must be in 1..500")

    base_map: dict[int, dict[str, Any]] = {}
    for c in components:
        cid = int(c["component_id"])
        pl = c.get("placement", {"t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]})
        base_map[cid] = {
            "t": [float(pl["t"][0]), float(pl["t"][1]), float(pl["t"][2])],
            "r": [float(pl["r"][0]), float(pl["r"][1]), float(pl["r"][2])],
            "bbox": c.get("bbox"),
        }

    frames: list[dict[str, Any]] = []
    clash_frames: list[int] = []

    for step_idx in range(steps + 1):
        t_sec = (duration_s * step_idx) / steps
        rot_deg: dict[int, float] = {}
        lin_mm: dict[int, float] = {}
        cur_t = {cid: list(info["t"]) for cid, info in base_map.items()}
        cur_r = {cid: list(info["r"]) for cid, info in base_map.items()}

        # 1. Apply primary motor drivers
        for drv in drivers:
            dtype = str(drv.get("type", "")).upper()
            cid = int(drv["component_id"])
            if cid not in base_map:
                raise GeomError("GEOM_INVALID_PARAM", f"unknown component_id in driver: {cid}")
            if dtype == "MOTOR_ROTATE":
                ax = _norm3(drv.get("axis", [0.0, 0.0, 1.0]))
                d_deg = float(drv.get("deg_per_s", 0.0)) * t_sec
                rot_deg[cid] = rot_deg.get(cid, 0.0) + d_deg
                cur_r[cid][0] += ax[0] * d_deg
                cur_r[cid][1] += ax[1] * d_deg
                cur_r[cid][2] += ax[2] * d_deg
            elif dtype == "MOTOR_LINEAR":
                dvec = _norm3(drv.get("direction", [1.0, 0.0, 0.0]))
                dist = float(drv.get("mm_per_s", 0.0)) * t_sec
                lin_mm[cid] = lin_mm.get(cid, 0.0) + dist
                cur_t[cid][0] += dvec[0] * dist
                cur_t[cid][1] += dvec[1] * dist
                cur_t[cid][2] += dvec[2] * dist
            else:
                raise GeomError("GEOM_INVALID_PARAM", f"unsupported motion driver type: {dtype}")

        # 2. Propagate mechanical couplings (GEAR, RACK_PINION, SCREW)
        for cp in couplings or []:
            ctype = str(cp.get("type", "")).upper()
            m_id = int(cp["master_id"])
            s_id = int(cp["slave_id"])
            if m_id not in base_map or s_id not in base_map:
                raise GeomError("GEOM_INVALID_PARAM", "unknown component_id in coupling")
            m_deg = rot_deg.get(m_id, 0.0)
            if ctype == "GEAR":
                ratio = float(cp.get("ratio", 1.0))
                ax = _norm3(cp.get("axis", [0.0, 0.0, 1.0]))
                s_deg = -ratio * m_deg
                rot_deg[s_id] = rot_deg.get(s_id, 0.0) + s_deg
                cur_r[s_id][0] += ax[0] * s_deg
                cur_r[s_id][1] += ax[1] * s_deg
                cur_r[s_id][2] += ax[2] * s_deg
            elif ctype == "RACK_PINION":
                r_pitch = float(cp.get("pitch_radius_mm", 10.0))
                dvec = _norm3(cp.get("direction", [1.0, 0.0, 0.0]))
                s_dist = r_pitch * math.radians(m_deg)
                lin_mm[s_id] = lin_mm.get(s_id, 0.0) + s_dist
                cur_t[s_id][0] += dvec[0] * s_dist
                cur_t[s_id][1] += dvec[1] * s_dist
                cur_t[s_id][2] += dvec[2] * s_dist
            elif ctype == "SCREW":
                pitch = float(cp.get("pitch_mm", 2.0))
                dvec = _norm3(cp.get("direction", [0.0, 0.0, 1.0]))
                s_dist = (pitch / 360.0) * m_deg
                lin_mm[s_id] = lin_mm.get(s_id, 0.0) + s_dist
                cur_t[s_id][0] += dvec[0] * s_dist
                cur_t[s_id][1] += dvec[1] * s_dist
                cur_t[s_id][2] += dvec[2] * s_dist
            else:
                raise GeomError("GEOM_INVALID_PARAM", f"unsupported coupling type: {ctype}")

        # 3. Optional BVH broad-phase clash check at current frame
        overlaps: list[list[int]] = []
        if check_clashes:
            aabbs: list[AABB] = []
            for cid, info in base_map.items():
                bb = info["bbox"]
                if bb and "min" in bb and "max" in bb:
                    dt = [cur_t[cid][k] - info["t"][k] for k in range(3)]
                    aabbs.append(
                        AABB(
                            comp_id=cid,
                            min_pt=(
                                float(bb["min"][0]) + dt[0],
                                float(bb["min"][1]) + dt[1],
                                float(bb["min"][2]) + dt[2],
                            ),
                            max_pt=(
                                float(bb["max"][0]) + dt[0],
                                float(bb["max"][1]) + dt[1],
                                float(bb["max"][2]) + dt[2],
                            ),
                        )
                    )
            if len(aabbs) >= 2:
                overlaps = [[a, b] for a, b in bvh_overlapping_pairs(aabbs, clearance=0.0)]
                if overlaps:
                    clash_frames.append(step_idx)

        frames.append(
            {
                "step": step_idx,
                "time_s": round(t_sec, 4),
                "placements": {
                    str(cid): {
                        "t": [round(v, 4) for v in cur_t[cid]],
                        "r": [round(v, 4) for v in cur_r[cid]],
                    }
                    for cid in base_map
                },
                "overlap_pairs": overlaps,
            }
        )

    return {
        "duration_s": duration_s,
        "steps": steps,
        "clash_frame_count": len(clash_frames),
        "clash_frames": clash_frames,
        "frames": frames,
    }
