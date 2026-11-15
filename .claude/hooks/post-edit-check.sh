#!/usr/bin/env bash
set -e

# AX-CAD Post Tool Use Check — Python 파일 편집 시에만 ruff 실행
raw=$(cat || true)
if [ -z "$raw" ]; then
  exit 0
fi

file=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" <<< "$raw" 2>/dev/null || echo "")

case "$file" in
  *.py)
    if command -v ruff >/dev/null 2>&1; then
      ruff check --select E,F --quiet "$file" 2>/dev/null || true
    fi
    ;;
esac

exit 0
