#!/bin/sh
# Back up AX-CAD: the database (pg_dump custom format) and the file volume (/data).
# Usage: deploy/backup.sh [output dir]      default: deploy/backups, keeps the newest $BACKUP_KEEP (14)
# Another compose project (e.g. UAT):  COMPOSE_PROJECT_NAME=axcad-uat deploy/backup.sh
set -eu
cd "$(dirname "$0")"
OUT="${1:-backups}"
KEEP="${BACKUP_KEEP:-14}"
TS=$(date +%Y%m%d-%H%M%S)
umask 077  # dumps hold prices, quotes and accounts
mkdir -p "$OUT"

docker compose exec -T db pg_dump -U axcad -d axcad -Fc > "$OUT/axcad-$TS.dump"
docker compose exec -T api tar -C /data -cf - . > "$OUT/data-$TS.tar"
# a backup that cannot be read back is no backup
docker compose exec -T db pg_restore --list < "$OUT/axcad-$TS.dump" > /dev/null
tar -tf "$OUT/data-$TS.tar" > /dev/null

# retention: drop the oldest pairs beyond $KEEP
ls -1t "$OUT"/axcad-*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old" "$OUT/data-$(basename "$old" .dump | cut -d- -f2-).tar"
done
echo "backup ok: $OUT/axcad-$TS.dump $OUT/data-$TS.tar"
