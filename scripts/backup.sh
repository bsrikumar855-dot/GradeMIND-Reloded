#!/usr/bin/env bash
# Back up the database and every stored object of a running compose stack (4.6).
#
#   scripts/backup.sh [OUT_DIR]        default OUT_DIR: backups/<UTC timestamp>
#
# Writes db.dump (pg_dump, custom format), objects.tar (every object, each with its own SHA-256, plus a closing manifest),
# counts.json (row counts, for the restore check), MANIFEST.txt and SHA256SUMS. The database is dumped FIRST and the objects second:
# objects are never deleted, so everything the dump refers to is in the archive.
#
# The backup holds student work. It is written with owner-only permissions and REFUSED inside the git repository unless git ignores
# the path (this repository is public). Keep it on an encrypted disk and off any shared folder.
set -euo pipefail
cd "$(dirname "$0")/.."

PGUSER_="${GRADEMIND_PG_USER:-grademind}"
PGDB_="${GRADEMIND_PG_DB:-grademind}"
OUT="${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}"

if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo "backup: $OUT already contains files; choose a new directory (a backup never overwrites)" >&2; exit 2
fi
mkdir -p "$OUT"
chmod 700 "$OUT"
ABS="$(cd "$OUT" && pwd)"
ROOT="$(pwd)"
case "$ABS/" in
  "$ROOT"/*)
    if ! git check-ignore -q "$ABS" 2>/dev/null; then
      echo "backup: $ABS is inside the git repository and git does not ignore it. This repository is PUBLIC: write the backup elsewhere (or under backups/)." >&2
      rmdir "$ABS" 2>/dev/null || true
      exit 2
    fi ;;
esac
umask 077

gm() { docker compose run --rm -T --no-deps api python -m grademind_core.cli "$@"; }

echo "backup: database ..."
docker compose exec -T postgres pg_dump -U "$PGUSER_" -d "$PGDB_" --format=custom --no-owner --no-privileges > "$ABS/db.dump"
echo "backup: objects ..."
gm backup-objects > "$ABS/objects.tar"
echo "backup: row counts ..."
gm count-rows > "$ABS/counts.json"
REV="$(docker compose exec -T postgres psql -U "$PGUSER_" -d "$PGDB_" -Atc 'select version_num from alembic_version' | tr -d '\r')"
{
  echo "GradeMIND backup"
  echo "created_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "schema_revision: $REV"
  echo "code_commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "db_dump_bytes: $(wc -c < "$ABS/db.dump")"
  echo "objects_tar_bytes: $(wc -c < "$ABS/objects.tar")"
  echo "row_counts: $(cat "$ABS/counts.json")"
} > "$ABS/MANIFEST.txt"
( cd "$ABS" && sha256sum db.dump objects.tar counts.json MANIFEST.txt > SHA256SUMS )
echo "backup: done -> $ABS"
cat "$ABS/MANIFEST.txt"
