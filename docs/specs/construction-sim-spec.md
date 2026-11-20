# Construction Process, Material Takeoff & 5D Cost Simulation Spec (`TASK-025`)

- **Owner**: `ANTIGRAVITY-PM`
- **Branch**: `feat/antigravity-construction-sim`
- **Approved GRILL-ME Decisions**:
  1. **6 Built-in Standard Domain Templates + Custom WBS Editing**:
     - `BUILDING` (건축 신축·증축)
     - `CIVIL_LANDSCAPE` (토목·기초·조경)
     - `MEP_ELECTRICAL` (전기 설비 설치·교체·해체)
     - `MEP_PLUMBING` (배관·위생 설비 설치·교체·해체)
     - `MEP_HVAC` (공조·냉난방기 HVAC 설치·교체·해체)
     - `INTERIOR_RENO` (인테리어 리노베이션·개조)
     - Work modes: `NEW_BUILD` | `INSTALL` | `REPLACE` | `DEMOLISH` | `RENOVATE`.
  2. **Simulation Engines**:
     - **CPM** (Topological sort, cycle detection, ES/EF/LS/LF, Total Float, Critical Path, Gantt bars).
     - **Material Takeoff (BOM)** (`qty * (1 + loss_rate)`, `Decimal` KRW totals).
     - **Korean Standard Cost Statement** (공사원가계산서: 직접재료비·직접노무비·직접경비 → 간접노무비·간접공사비 → 순공사원가 → 일반관리비 → 이윤 → 공급가액 → VAT 10% → 총공사비).
     - **S-Curve Cash Flow** (daily incremental & cumulative spend).
     - **3-Scenario Comparison** (`STANDARD`, `CRASH_NIGHT`, `VALUE_ENGINEER`).
     - **Monte Carlo Risk Simulation** (`NumPy` triangular distribution, P50/P80/P95 cost & duration).
  3. **Persistence & UI**:
     - Migration `0045_construction_simulations.py` (`construction_plans` table).
     - Dedicated Studio page `/projects/[projectId]/construction` + CSV/JSON export.
  4. **Optional AI Advisor**:
     - Read-only `Antigravity SDK` (`google-antigravity`) opt-in toggle for schedule/cost risk advice, with 100% deterministic local fallback.
