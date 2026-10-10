#!/bin/sh
# Runs INSIDE the postgres container (POSIX sh): dump the database AND count its rows from the SAME point in time (4.6).
#
# A backup of a live system must not compare a dump with counts taken a moment later: rows keep arriving (a machine reading finishes, a
# grade is saved), and a restore would then look as if it lost data. So one session exports a REPEATABLE READ snapshot, pg_dump reads
# exactly that snapshot, and the counts are taken in the same session before it ends.
#
# Environment: PGU (user), PGD (database), COUNT_SQL (a query returning one JSON line). Writes /tmp/gm.dump and /tmp/gm.counts.
set -eu
IN=/tmp/gm.psql.in
OUT=/tmp/gm.psql.out
rm -f "$IN" "$OUT"
mkfifo "$IN" "$OUT"
psql -U "$PGU" -d "$PGD" -Atq -v ON_ERROR_STOP=1 < "$IN" > "$OUT" &
PSQL_PID=$!
exec 3> "$IN"
exec 4< "$OUT"
echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" >&3
echo "SELECT pg_export_snapshot();" >&3
read -r SNAP <&4
case "$SNAP" in [0-9A-F]*-[0-9A-F]*-[0-9]*) ;; *) echo "pg_snapshot_dump: unexpected snapshot id: $SNAP" >&2; exit 1;; esac
pg_dump -U "$PGU" -d "$PGD" --format=custom --no-owner --no-privileges --snapshot="$SNAP" -f /tmp/gm.dump
echo "$COUNT_SQL" >&3
read -r COUNTS <&4
echo "$COUNTS" > /tmp/gm.counts
echo "COMMIT;" >&3
exec 3>&-
wait "$PSQL_PID"
rm -f "$IN" "$OUT"
