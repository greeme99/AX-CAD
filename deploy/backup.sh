#!/bin/sh
# Back up AX-CAD: the database (pg_dump custom format) and the file volume (/data).
# Usage: deploy/backup.sh [output dir]      default: deploy/backups, keeps the newest $BACKUP_KEEP (14)
# Another compose project (e.g. UAT):  COMPOSE_PROJECT_NAME=axcad-uat deploy/backup.sh
# Not included (keep them separately for a new server): deploy/.env, deploy/config/ (TLS, supplier).
set -eu
cd "$(dirname "$0")"
OUT="${1:-backups}"
KEEP="${BACKUP_KEEP:-14}"
case "$KEEP" in '' | *[!0-9]* | 0) echo "BACKUP_KEEP must be a positive number" >&2; exit 2 ;; esac
TS=$(date +%Y%m%d-%H%M%S)
umask 077  # dumps hold prices, quotes and accounts
mkdir -p "$OUT"
chmod 700 "$OUT"
DUMP="$OUT/axcad-$TS.dump"
DATA="$OUT/data-$TS.tar"
trap 'rm -f "$DUMP.partial" "$DATA.partial"' EXIT  # a failed run leaves no half file behind

docker compose exec -T db pg_dump -U axcad -d axcad -Fc > "$DUMP.partial"
docker compose exec -T api tar -C /data -cf - . > "$DATA.partial"
# a backup that cannot be read back is no backup
docker compose exec -T db pg_restore --list < "$DUMP.partial" > /dev/null
tar -tf "$DATA.partial" > /dev/null
mv "$DUMP.partial" "$DUMP"
mv "$DATA.partial" "$DATA"

# retention: by the timestamp in the name (not mtime, which copying changes); other files untouched
ls -1r "$OUT"/axcad-[0-9]*-[0-9]*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do
    ts=$(basename "$old" .dump)
    rm -f "$old" "$OUT/data-${ts#axcad-}.tar"
done
echo "backup ok: $DUMP $DATA"
