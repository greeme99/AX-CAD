# E2E (TC-82)

Runs against a live stack; it creates its own users, project and drawing (names end with a run
suffix) and needs an ACTIVE master data version.

```bash
# API with a non-sample supplier file, otherwise the official PDF is refused (409)
AXCAD_SUPPLIER_FILE=frontend/e2e/fixtures/supplier.json uv run uvicorn backend.api.main:app --port 8000
pnpm --dir frontend dev
# admin account of that environment
E2E_ADMIN_LOGIN=<admin> E2E_ADMIN_PASSWORD_FILE=<file> pnpm --dir frontend e2e
```
