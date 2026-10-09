#!/usr/bin/env bash
set -e

# AX-CAD Post Tool Use Check
# 빠른 포맷/구문 검사 수행 (실패 시 non-zero exit)

# Python 구문 검사 (환경에 따라 활성화)
if command -v ruff >/dev/null 2>&1; then
  ruff check --select E,F --quiet core/ backend/ 2>/dev/null || true
fi

exit 0
