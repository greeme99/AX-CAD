#!/bin/sh
# Restore AX-CAD from a backup pair made by backup.sh. REPLACES the current database and files.
# Usage: deploy/restore.sh backups/axcad-<ts>.dump backups/data-<ts>.tar
set -eu
cd "$(dirname "$0")"
[ $# -eq 2 ] && [ -f "$1" ] && [ -f "$2" ] || { echo "usage: $0 <axcad-*.dump> <data-*.tar>" >&2; exit 2; }
DUMP=$(realpath "$1")
DATA=$(realpath "$2")
pg_list() { docker compose exec -T db pg_restore --list < "$DUMP" > /dev/null; }
pg_list || { echo "unreadable dump: $DUMP" >&2; exit 1; }
tar -tf "$DATA" > /dev/null || { echo "unreadable archive: $DATA" >&2; exit 1; }

printf 'This replaces ALL current data (database + files) with the backup.\nType RESTORE to continue: '
read -r answer
[ "$answer" = "RESTORE" ] || { echo "cancelled"; exit 1; }

docker compose stop proxy web api
# database: recreate empty, then load (the dump carries schema, triggers and data)
docker compose exec -T db dropdb -U axcad --force axcad
docker compose exec -T db createdb -U axcad axcad
# --no-privileges: grants come back from migrate (app-role), also on a new server without that role
docker compose exec -T db pg_restore -U axcad -d axcad --no-owner --no-privileges < "$DUMP"
# files: empty the volume, then unpack (runs as the api user that owns /data)
docker compose run --rm --no-deps -T --entrypoint sh api \
    -c 'find /data -mindepth 1 -delete && tar -C /data -xf -' < "$DATA"
# migrate brings an older backup up to the current schema, then everything starts
docker compose up -d
echo "restore done: check http://<server>:${AXCAD_PORT:-8080} and docker compose ps"
