#!/bin/bash
# Check the API's DB role (axcad_app): it can read data but cannot lift the database's protections.
# Run after install/upgrade/restore (CI runs it too). Its password itself is proven by the api
# healthcheck: inside the db container loopback logins are trusted, so this checks rights only.
set -euo pipefail
cd "$(dirname "$0")"
q() { docker compose exec -T db psql -U axcad_app -d axcad -h localhost -tAc "$1" 2>&1 || true; }
n=$(q 'SELECT count(*) FROM users')
[[ "$n" =~ ^[0-9]+$ ]] || { echo "app role cannot read: $n" >&2; exit 1; }
for stmt in 'ALTER TABLE audit_logs DISABLE TRIGGER ALL' 'DELETE FROM quote_reports' \
    'UPDATE audit_logs SET action = action' 'TRUNCATE quote_headers'; do
    out=$(q "$stmt")
    grep -Eq 'must be owner|permission denied' <<<"$out" || { echo "NOT blocked: $stmt -> $out" >&2; exit 1; }
done
echo "app role ok: reads data, cannot disable triggers, delete evidence, edit or truncate"
