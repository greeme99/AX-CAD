# Canonical Commands (AX-CAD)

## 1. Python (Core CAD & FastAPI Backend)
- **Install**: `uv sync` 또는 `poetry install`
- **Lint**: `uv run ruff check .`
- **Format Check**: `uv run ruff format --check .`
- **Typecheck**: `uv run mypy core backend`
- **Unit Test (CAD & Quote)**: `uv run pytest -v tests/`
- **Dev Server**: `uv run uvicorn backend.api.main:app --reload --port 8000`

## 2. Frontend (Next.js & shadcn/ui)
- **Install**: `pnpm install`
- **Lint**: `pnpm lint`
- **Typecheck**: `pnpm typecheck`
- **Test**: `pnpm test`
- **Dev Server**: `pnpm dev`
- **Build**: `pnpm build`

## 3. Integrated Verification
- **Quick Verify**: `bash .claude/skills/project-workflow/scripts/verify.sh`
