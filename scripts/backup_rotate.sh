#!/usr/bin/env bash
# The nightly backup job (D30.4):  scripts/backup_rotate.sh [--weekly]
#
#   every run   a local backup in $GRADEMIND_BACKUP_DIR/daily/<UTC time>/ (scripts/backup.sh), then keep the newest 14
#   weekly      (--weekly, or automatically on Sundays) an ENCRYPTED copy $GRADEMIND_BACKUP_DIR/weekly/<UTC time>.tar.age, encrypted to the owner's PUBLIC
#               key(s) in $GRADEMIND_BACKUP_RECIPIENTS; copied to $GRADEMIND_OFFSITE_DIR if set (a mounted drive, or a folder an rsync/rclone job ships off
#               the machine); the newest 8 are kept in both places
#
# Run it from cron at night, for example:   17 2 * * *  cd /path/to/GradeMIND-v2 && ./scripts/backup_rotate.sh >> backups/rotate.log 2>&1
# The private key never touches this machine: only the owner's restore (scripts/restore.sh ... --identity FILE) needs it.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="${GRADEMIND_BACKUP_DIR:-backups}"
RECIP="${GRADEMIND_BACKUP_RECIPIENTS:-}"
OFFSITE="${GRADEMIND_OFFSITE_DIR:-}"
KEEP_DAILY="${GRADEMIND_KEEP_DAILY:-14}"
KEEP_WEEKLY="${GRADEMIND_KEEP_WEEKLY:-8}"
WEEKLY=0
[ "${1:-}" = "--weekly" ] && WEEKLY=1
[ "$(date -u +%u)" = "7" ] && WEEKLY=1

if [ -f .env ] && grep -q 'AGE-SECRET-KEY-' .env; then
  echo "backup_rotate: .env contains an age PRIVATE key. Remove it: the key is held by the owner, off this machine (D30.4)." >&2; exit 2
fi
if [ "$WEEKLY" = 1 ] && { [ -z "$RECIP" ] || [ ! -f "$RECIP" ]; }; then
  echo "backup_rotate: a weekly encrypted copy needs GRADEMIND_BACKUP_RECIPIENTS (a file with the owner's public key, age1...)" >&2; exit 2
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ROOT/daily" "$ROOT/weekly"
./scripts/backup.sh "$ROOT/daily/$TS" > /dev/null
echo "backup_rotate: daily backup $ROOT/daily/$TS"
./scripts/backup_prune.sh "$ROOT/daily" "$KEEP_DAILY"

if [ "$WEEKLY" = 1 ]; then
  ./scripts/backup_encrypt.sh "$ROOT/daily/$TS" "$RECIP" "$ROOT/weekly/$TS.tar.age"
  ./scripts/backup_prune.sh "$ROOT/weekly" "$KEEP_WEEKLY"
  if [ -n "$OFFSITE" ]; then
    mkdir -p "$OFFSITE"
    cp "$ROOT/weekly/$TS.tar.age" "$OFFSITE/$TS.tar.age.partial"
    mv "$OFFSITE/$TS.tar.age.partial" "$OFFSITE/$TS.tar.age"
    ( cd "$OFFSITE" && sha256sum "$TS.tar.age" > "$TS.tar.age.sha256" )
    ./scripts/backup_prune.sh "$OFFSITE" "$KEEP_WEEKLY"
    echo "backup_rotate: encrypted copy shipped to $OFFSITE/$TS.tar.age"
  else
    echo "backup_rotate: GRADEMIND_OFFSITE_DIR is not set: the encrypted copy stays on this machine until you move it off" >&2
  fi
fi
echo "backup_rotate: done"
