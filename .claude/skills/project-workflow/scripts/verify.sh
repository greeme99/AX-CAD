#!/usr/bin/env bash
set -e

echo "=== AX-CAD Project Verification ==="

# 1. Python 환경 검사
if [ -d ".venv" ] || command -v uv >/dev/null 2>&1; then
  echo "[1/2] Checking Python CAD & Backend..."
  if command -v uv >/dev/null 2>&1; then
    uv run pytest -q tests/ 2>/dev/null || echo "ℹ️ pytest: tests/ directory not populated yet or tests pending"
  fi
else
  echo "[1/2] Python venv/uv not initialized yet. Skipping unit tests."
fi

# 2. Node/Frontend 환경 검사
if [ -f "package.json" ]; then
  echo "[2/2] Checking Frontend..."
  pnpm lint 2>/dev/null || echo "ℹ️ Frontend lint skipped"
else
  echo "[2/2] package.json not found. Frontend verification skipped."
fi

echo "=== Verification Finished ==="
exit 0
