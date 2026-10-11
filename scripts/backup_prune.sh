#!/usr/bin/env bash
# Keep the newest KEEP backups in DIR and delete the rest (D30.4: 14 daily, 8 weekly):  scripts/backup_prune.sh DIR KEEP
# Only entries whose name starts with a UTC timestamp (YYYYMMDDTHHMMSSZ, a folder or a .tar.age) are ever considered; anything else in DIR is left alone.
set -euo pipefail
DIR="${1:?usage: backup_prune.sh DIR KEEP}"
KEEP="${2:?how many to keep}"
case "$KEEP" in ''|*[!0-9]*) echo "backup_prune: KEEP must be a number" >&2; exit 2;; esac
[ "$KEEP" -ge 1 ] || { echo "backup_prune: KEEP must be at least 1" >&2; exit 2; }
[ -d "$DIR" ] || { echo "backup_prune: $DIR is not a folder" >&2; exit 2; }
mapfile -t entries < <(find "$DIR" -mindepth 1 -maxdepth 1 -regextype posix-extended -regex '.*/[0-9]{8}T[0-9]{6}Z(\.tar\.age)?' -printf '%f\n' | sort)
total="${#entries[@]}"
if [ "$total" -le "$KEEP" ]; then echo "backup_prune: $total in $DIR, keeping all (limit $KEEP)"; exit 0; fi
for e in "${entries[@]:0:$((total - KEEP))}"; do
  rm -rf -- "${DIR:?}/$e" "${DIR:?}/$e.sha256"
  echo "backup_prune: removed $e"
done
