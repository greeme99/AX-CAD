# Photo-to-3D Auto-Trace & Real-Scale Reconstruction Spec (`TASK-024`)

- **Owner**: `ANTIGRAVITY-PM`
- **Branch**: `feat/antigravity-photo-to-3d`
- **Approved GRILL-ME Decisions**:
  1. **Output Mode**: **Hybrid** — editable Parametric B-Rep features (`PRIMITIVE` / `EXTRUDE` / `REVOLVE` / `HOLE`) + real-scale 3D Visual Hull solid (`OCCT B-Rep`).
  2. **Input Modes & Reference Scale**:
     - `ORTHO_MULTI`: Orthographic multi-angle photos (Front/Side/Top, 2–6 images).
     - `TURNTABLE`: Sequential circular photos (4–36 images, auto uniform azimuth or custom angles).
     - `FREE_ANGLE`: Single or arbitrary-angle photos with AI shape reasoning.
     - Scale calibration: overall reference dimension (`reference_mm` on `X`/`Y`/`Z`) OR interactive 2-point pixel calibration (`ref_p1_px`, `ref_p2_px`, `reference_mm`).
  3. **Antigravity SDK Integration**: Optional dynamic adapter (`google-antigravity`, read-only `LocalAgentConfig` with zero write/shell tools) + deterministic offline `OCCT 8.0 + NumPy + SciPy + Pillow` fallback (100% functional in CI/offline with zero new mandatory dependencies).
  4. **Data Privacy / Execution Policy**: Local geometric engine is default; Antigravity SDK AI reasoning runs ONLY when explicitly opted in (`use_ai=true`) and sends only structured geometric descriptors (never credentials or raw drawings).

## API Contract
- `GET /api/photo3d/status`: Reports `sdk_installed`, `provider`, `supported_modes`, `max_images` (36), `max_image_mb` (15).
- `POST /api/documents/{document_id}/photo3d/reconstruct`:
  - `multipart/form-data`: `files` (1–36 PNG/JPEG/WEBP images, magic-byte validated), `meta` JSON (`mode`, `output_mode`, `reference_mm`, `reference_axis`, `ref_p1_px`, `ref_p2_px`, `view_labels`, `angles_deg`, `use_ai`, `create_feature`).
  - Returns reconstructed `bbox_mm`, `volume_mm3`, `surface_area_mm2`, `feature_plan`, `feature_id` (when persisted to `3D Model` FeatureTree), `ai_used`, `ai_provider`, `warnings`.

## Verification Matrix (`tests/test_photo3d.py` & `photo3d.test.ts`)
- `P3D-T01`: Silhouette segmentation & 2-point pixel-to-mm scale calibration accuracy (`<= 2%`).
- `P3D-T02`: Orthographic multi-view (`FRONT` + `SIDE` + `TOP`) B-Rep reconstruction (box & cylinder dimensions within `±3%`).
- `P3D-T03`: Sequential 12-photo Turntable Visual Hull 3D solid reconstruction (volume & bbox within tolerance, valid OCCT B-Rep).
- `P3D-T04`: Antigravity SDK adapter (read-only config, mocked `google.antigravity.Agent` JSON response parsing, schema validation & deterministic fallback).
- `P3D-T05`: Security validation (non-image magic-byte rejection `422`, image count/size bounds).
