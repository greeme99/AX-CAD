#!/usr/bin/env bash
set -euo pipefail

# Read stdin JSON payload from Claude Code hook
raw=$(cat || true)
if [ -z "$raw" ]; then
  exit 0
fi

# Extract command string using Python or sed/grep fallback
command=$(python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('tool_input', {}).get('command', ''))" <<< "$raw" 2>/dev/null || echo "$raw")

# Dangerous command patterns
patterns=(
  'rm[[:space:]]+-rf[[:space:]]+/'
  'rmdir[[:space:]]+/s'
  'git[[:space:]]+push.*(--force|-f)'
  'git[[:space:]]+reset[[:space:]]+--hard'
  'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*f'
  '(drop|truncate)[[:space:]]+(database|table)'
  'terraform[[:space:]]+destroy'
  'kubectl[[:space:]]+delete[[:space:]]+(namespace|ns)'
)

for pattern in "${patterns[@]}"; do
  if echo "$command" | grep -Ei "$pattern" > /dev/null 2>&1; then
    echo "BLOCKED: 위험 명령은 Human-in-the-loop 명시적 승인이 필요합니다: $command" >&2
    exit 2
  fi
done

exit 0
