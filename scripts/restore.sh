#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh and prove it is whole (4.6).
#
#   scripts/restore.sh BACKUP_DIR TARGET_DB TARGET_BUCKET [--replace-live]
#
# Side copy (a drill, or to inspect an old state): TARGET_DB and TARGET_BUCKET are NEW names. Nothing live is touched.
# Disaster recovery: stop api, worker, ocr-worker and web, keep postgres and minio running, then use the live names with --replace-live. That
# DROPS the live database and overwrites objects with the backup's.
#
# Steps: check SHA256SUMS; create the database and load db.dump; verify and load objects.tar; then `verify-restore`: schema revision, row
# counts equal to the counts taken at backup time, every page image / booklet source / paper source / line crop exists and matches the
# SHA-256 the database recorded, and every finalized result snapshot recomputes exactly. Exit 0 only if all of that holds.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP="${1:?usage: restore.sh BACKUP_DIR TARGET_DB TARGET_BUCKET [--replace-live]}"
TARGET_DB="${2:?target database name}"
TARGET_BUCKET="${3:?target bucket name}"
REPLACE="${4:-}"
PGUSER_="${GRADEMIND_PG_USER:-grademind}"
LIVE_DB="${GRADEMIND_PG_DB:-grademind}"

case "$TARGET_DB" in *[!a-zA-Z0-9_]*|"") echo "restore: the database name may contain only letters, digits and _" >&2; exit 2;; esac
[ -f "$BACKUP/SHA256SUMS" ] || { echo "restore: $BACKUP/SHA256SUMS not found" >&2; exit 2; }
( cd "$BACKUP" && sha256sum -c --quiet SHA256SUMS ) || { echo "restore: the backup files do not match SHA256SUMS (damaged or altered); nothing was restored" >&2; exit 2; }

psql_() { docker compose exec -T postgres psql -U "$PGUSER_" -d postgres -v ON_ERROR_STOP=1 "$@"; }
gm() { docker compose run --rm -T --no-deps api python -m grademind_core.cli "$@"; }

if [ "$TARGET_DB" = "$LIVE_DB" ] && [ "$REPLACE" != "--replace-live" ]; then
  echo "restore: $TARGET_DB is the LIVE database. Use a new name for a drill, or add --replace-live (with api, worker, ocr-worker and web stopped)." >&2; exit 2
fi
EXISTS="$(psql_ -Atc "select 1 from pg_database where datname = '$TARGET_DB'" | tr -d '\r')"
if [ -n "$EXISTS" ]; then
  if [ "$REPLACE" != "--replace-live" ]; then echo "restore: database $TARGET_DB already exists; choose a new name" >&2; exit 2; fi
  RUNNING="$(docker compose ps --status running --services 2>/dev/null | grep -E '^(api|worker|ocr-worker|web)$' || true)"
  if [ -n "$RUNNING" ]; then echo "restore: stop these first (they hold the database open): $(echo "$RUNNING" | tr "\n" " ")" >&2; exit 2; fi
  echo "restore: dropping $TARGET_DB (--replace-live)"
  psql_ -c "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$TARGET_DB' and pid <> pg_backend_pid()" >/dev/null
  psql_ -c "drop database \"$TARGET_DB\"" >/dev/null
fi

echo "restore: database -> $TARGET_DB"
psql_ -c "create database \"$TARGET_DB\"" >/dev/null
docker compose exec -T postgres pg_restore -U "$PGUSER_" -d "$TARGET_DB" --no-owner --no-privileges --exit-on-error < "$BACKUP/db.dump"

echo "restore: objects -> bucket $TARGET_BUCKET"
docker compose run --rm -T --no-deps -e "GRADEMIND_S3_BUCKET=$TARGET_BUCKET" api python -m grademind_core.cli restore-objects < "$BACKUP/objects.tar"

echo "restore: verifying ..."
gm verify-restore --database "$TARGET_DB" --bucket "$TARGET_BUCKET" --compare-counts "$(cat "$BACKUP/counts.json")"
