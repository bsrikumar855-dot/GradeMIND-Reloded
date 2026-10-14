#!/usr/bin/env bash
# What cron / the systemd timer runs every night (D30.4, D31): the nightly local backup, and on Sundays the encrypted copy to the off-machine
# folder, with retention 14 daily / 8 weekly. Settings come from pilot-data/schedule.env (written by pilot_schedule.sh: paths only, no secrets).
# Extra arguments go to backup_rotate.sh (for example --weekly to force the encrypted copy now, to try it out).
# Output is appended to pilot-data/backup.log. Exit code is that of scripts/backup_rotate.sh.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
CONF="$PILOT_DATA/schedule.env"
[ -f "$CONF" ] || die "$CONF is missing: run scripts/pilot/pilot_schedule.sh install first"
has_private_key "$CONF" && die "$CONF contains an age private key"
set -a; . "$CONF"; set +a
pilot_compose_env
mkdir -p "$PILOT_DATA"
{
  echo "== $(now_utc) backup job"
  ./scripts/backup_rotate.sh "$@"
} >> "$PILOT_DATA/backup.log" 2>&1
