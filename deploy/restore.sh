#!/bin/sh
# Restore AX-CAD from a backup pair made by backup.sh. REPLACES the current database and files.
# A snapshot of the current state is taken first (backups/pre-restore-*), so a failed or wrong
# restore can be undone with this same script.
# Usage: deploy/restore.sh backups/axcad-<ts>.dump backups/data-<ts>.tar
set -eu
cd "$(dirname "$0")"
[ $# -eq 2 ] && [ -f "$1" ] && [ -f "$2" ] || { echo "usage: $0 <axcad-*.dump> <data-*.tar>" >&2; exit 2; }
DUMP=$(realpath "$1")
DATA=$(realpath "$2")
docker compose exec -T db pg_restore --list < "$DUMP" > /dev/null || { echo "unreadable dump: $DUMP" >&2; exit 1; }
tar -tf "$DATA" > /dev/null || { echo "unreadable archive: $DATA" >&2; exit 1; }

PROJECT=$(docker compose config --format json | sed -n 's/^ *"name": *"\([^"]*\)".*/\1/p' | head -n 1)
printf 'Project "%s": this replaces ALL current data (database + files) with\n  %s\n  %s\nType RESTORE to continue: ' \
    "$PROJECT" "$DUMP" "$DATA"
read -r answer
[ "$answer" = "RESTORE" ] || { echo "cancelled"; exit 1; }

echo "snapshot of the current state first:"
BACKUP_KEEP=1000 ./backup.sh "backups/pre-restore-$(date +%Y%m%d-%H%M%S)"

docker compose stop proxy web api
# database: recreate empty, then load in one transaction (any error -> nothing loaded, script stops)
# --no-privileges: grants come back from migrate (app-role), also on a new server without that role
docker compose exec -T db dropdb -U axcad --force axcad
docker compose exec -T db createdb -U axcad axcad
docker compose exec -T db pg_restore -U axcad -d axcad --no-owner --no-privileges \
    --single-transaction --exit-on-error < "$DUMP"
# files: empty the volume, then unpack (runs as the api user that owns /data)
docker compose run --rm --no-deps -T --entrypoint sh api \
    -c 'find /data -mindepth 1 -delete && tar -C /data --no-same-owner --no-same-permissions -xf -' < "$DATA"
# migrate brings an older backup up to the current schema and re-grants the api role, then all starts
docker compose up -d
echo "restore done. If anything is wrong, restore the pre-restore snapshot printed above."
echo "Note: an old backup also brings back old users and passwords as they were then."
