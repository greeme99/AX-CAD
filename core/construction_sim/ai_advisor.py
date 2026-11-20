"""Optional Read-Only Antigravity SDK Advisor for Construction Process & Cost Risk Optimization."""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

from core.photo3d.antigravity_adapter import antigravity_sdk_status

_SYSTEM_PROMPT = (
    "당신은 한국 건설·설비·인테리어 공정관리(CPM) 및 공사원가 전문가입니다. "
    "주어진 공정 시뮬레이션 요약(주공정, 총공기, 공사원가계산서, 몬테카를로 P50/P80/P95, 리스크 요인)을 보고 "
    "한국어로 5줄 이내의 실무 공기단축·원가절감·안전관리 핵심 조언만 간결하게 제시하세요."
)


def _deterministic_advice(domain: str, sim: dict[str, Any]) -> str:
    mc = sim.get("monte_carlo", {})
    stmt = sim.get("cost_statement", {})
    crit = ", ".join(sim.get("critical_path", [])[:4])
    drivers = ", ".join(d["name"] for d in mc.get("top_risk_drivers", [])[:2]) or "주요 직공정"
    p50 = int(mc.get("cost_p50_krw", stmt.get("grand_total_krw", 0)))
    p95 = int(mc.get("cost_p95_krw", stmt.get("grand_total_krw", 0)))
    reserve = max(0, p95 - p50)
    return (
        f"[{domain} 로컬 리스크 분석] 주공정({crit})의 공기 지연이 전체 준공에 직결됩니다. "
        f"원가 변동성이 가장 큰 '{drivers}' 공정의 자재 선발주 및 작업 인원 집중 배치를 권장하며, "
        f"P95 기준 예비비 약 {reserve:,}원(P50 대비 +{((reserve / max(1, p50)) * 100):.1f}%) 확보가 안전합니다."
    )


async def _ask_antigravity(prompt: str, timeout_s: float = 25.0) -> str:
    mod = importlib.import_module("google.antigravity")
    cfg = mod.LocalAgentConfig(system_instructions=_SYSTEM_PROMPT)

    async def _run() -> str:
        async with mod.Agent(cfg) as agent:
            resp = await agent.chat(prompt)
            chunks: list[str] = []
            async for tok in resp:
                chunks.append(str(tok))
            return "".join(chunks).strip()

    return await asyncio.wait_for(_run(), timeout=timeout_s)


def advise_construction_simulation(
    domain: str,
    work_mode: str,
    sim: dict[str, Any],
    *,
    use_ai: bool = False,
) -> dict[str, Any]:
    """Return construction schedule/cost advice via Antigravity SDK (when opted in & installed) or local engine."""
    fallback = _deterministic_advice(domain, sim)
    if not use_ai:
        return {"provider": "local-engine", "ai_used": False, "advice": fallback}

    st = antigravity_sdk_status()
    if not st["sdk_installed"]:
        return {
            "provider": "local-engine",
            "ai_used": False,
            "advice": fallback,
            "note": "Antigravity SDK 미설치로 로컬 리스크 분석을 반환했습니다.",
        }

    summary = {
        "domain": domain,
        "work_mode": work_mode,
        "duration_days": sim["project_duration_days"],
        "critical_path": sim["critical_path"],
        "grand_total_krw": sim["cost_statement"]["grand_total_krw"],
        "monte_carlo": sim["monte_carlo"],
    }
    prompt = f"시뮬레이션 요약:\n{json.dumps(summary, ensure_ascii=False)}"
    try:
        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                text = pool.submit(asyncio.run, _ask_antigravity(prompt)).result(timeout=30.0)
        except RuntimeError:
            text = asyncio.run(_ask_antigravity(prompt))
        if text:
            return {"provider": "antigravity-sdk", "ai_used": True, "advice": text[:1500]}
    except (TimeoutError, OSError, RuntimeError, ValueError, ImportError):
        pass
    return {"provider": "local-engine", "ai_used": False, "advice": fallback}
