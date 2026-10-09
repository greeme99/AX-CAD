# ADR-06 (pending approval): OCCT Python binding = cadquery-ocp-novtk 8.0.1.1.0, raw OCP API

- Date: 2026-10-09 · Phase: W3 (gate G3) · Status: proposed (needs user approval to merge into MEMORY.md)

## Decision
- Use `cadquery-ocp-novtk==8.0.1.1.0` (OCCT 8.0.1), pinned exactly. Call the raw OCP API; do not add full `cadquery`.
- `pythonocc-core` rejected: no PyPI wheels (conda only), incompatible with the uv toolchain.
- `cadquery-ocp` (with VTK) rejected for the server: pulls vtk 9.x (~590 MB installed) for nothing (server only tessellates).

## Evidence (W3 spike, 12/12 tests, re-run by lead)
Extrude 40×30×10 = 12000; open wire / degenerate edge / bow-tie self-intersection rejected before kernel calls; Revolve π·10²·20; Cut/Fuse/Common exact, coincident-face fuse no crash, empty Common detected; STEP AP242 (XCAF) round trip preserves volume/area (1e-6) and product name; IGES BRep mode round trip exact; AddOptimal bbox exact; mesh 0.1 mm → face-indexed JSON; BREP bytes across spawn workers.

## Consequences / rules
- Shapes cross process boundaries only as BREP bytes (`TopoDS_Shape` is not picklable); `BinTools.Write_s(shape, buf, False, False, VERSION_4)` (no triangles, fixed version).
- Feature-parameter JSON is the source of truth; BREP is a cache keyed by sha256(feature chain + OCP version); mesh/STEP are derived.
- Kernel runs in a persistent spawn process pool (import OCP ≈ 0.5 s), one job per worker (Interface_Static is global); worker death → 422 GEOM_*.
- OCCT 8 pitfalls: inconsistent `_s` suffix (`TopoDS.Face` has none), collections in `OCP.collections`, no `HasErrors()` on Boolean API, set STEP schema after creating the writer and check the return, writers print to stdout, use `AddOptimal_s` for quote bboxes.
- Deploy: Linux needs manylinux_2_28 (glibc ≥ 2.28; no Alpine). License: OCP Apache-2.0, OCCT LGPL-2.1 + exception (internal server use: no obligations; desktop distribution needs replaceable shared libs + notice).

Reference PoC: scratchpad `w3-spike/poc.py` (to be moved into `core/geometry/` in S5).
