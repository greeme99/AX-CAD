# E2E (TC-82)

Runs against a live stack; it creates its own users, project and drawing (names end with a run
suffix) and needs an ACTIVE master data version **in effect today**.

CI runs it on every PR in the `deploy` job against the docker compose stack (through the nginx proxy),
with a CLI-created admin, the fake bundle `fixtures/master.json` and `E2E_REQUIRE=1` (a missing
prerequisite fails instead of skipping).

```bash
# API with a non-sample supplier file, otherwise the official PDF is refused (409)
AXCAD_SUPPLIER_FILE=frontend/e2e/fixtures/supplier.json uv run uvicorn backend.api.main:app --port 8000
pnpm --dir frontend dev
# admin account of that environment
E2E_ADMIN_LOGIN=<admin> E2E_ADMIN_PASSWORD_FILE=<file> pnpm --dir frontend e2e
```
