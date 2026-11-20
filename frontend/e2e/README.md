# E2E Suites

Runs against a live stack; each test creates its own users, project and document (names end with a run
suffix). `tc82.e2e.ts` needs an ACTIVE master data version **in effect today**.

| File | Coverage |
|---|---|
| `tc82.e2e.ts` | Quote create → adjust → validate → official PDF download (Track 7) |
| `model3d.sheet.e2e.ts` | 3D modeler: Sheet Metal base/flange, flat pattern DXF download, UI Phase 3 `InlinePanel` & `FeatureTimeline` |
| `viewer.ui.e2e.ts` | 2D viewer: UI Phase 3 `DynamicInput` (`F12`), AI Copilot rule-based check, BOM navigation |

CI runs all `*.e2e.ts` on every PR in the `deploy` job against the docker compose stack (through the nginx proxy),
with a CLI-created admin, the fake bundle `fixtures/master.json` and `E2E_REQUIRE=1` (a missing
prerequisite fails instead of skipping).

```bash
# API with a non-sample supplier file, otherwise the official PDF is refused (409)
AXCAD_SUPPLIER_FILE=frontend/e2e/fixtures/supplier.json uv run uvicorn backend.api.main:app --port 8000
pnpm --dir frontend dev
# admin account of that environment
E2E_ADMIN_LOGIN=<admin> E2E_ADMIN_PASSWORD_FILE=<file> pnpm --dir frontend e2e
```

For Antigravity isolated ports (`8200` / `3200`):

```powershell
.\tools\agent-dev.ps1 -Agent antigravity
$env:E2E_BASE_URL = "http://localhost:3200"
pnpm --dir frontend e2e
```
