#!/usr/bin/env bash
# The daily health check (D31): ONE line appended to pilot-data/health.log, the same line on stdout, exit 1 when anything is wrong. No email, no cloud.
#   scripts/pilot/pilot_health.sh [--dry-run] [--max-queue N] [--max-backup-age-h 26] [--min-free-pct 10] [--min-free-gb 5]
# Checks: every service healthy; OCR queue at most N waiting (default 0); newest nightly backup younger than 26 h; free disk space on the machine
# and on the backup folder; verify-snapshots reproduces every finalized result. docs/PILOT_DAY.md says what to do with each failure.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
MAXQ=0 MAXAGE=26 MINPCT=10 MINGB=5
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --max-queue) MAXQ="${2:?}"; shift 2 ;;
    --max-backup-age-h) MAXAGE="${2:?}"; shift 2 ;;
    --min-free-pct) MINPCT="${2:?}"; shift 2 ;;
    --min-free-gb) MINGB="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
done
[ -f "$PILOT_DATA/schedule.env" ] && { set -a; . "$PILOT_DATA/schedule.env"; set +a; }
BACKUP_DIR="${GRADEMIND_BACKUP_DIR:-$PILOT_ROOT/backups}"
pilot_compose_env
if [ "$DRY" = 1 ]; then
  say "DRY-RUN would check: all services healthy; redis queue 'ocr' <= $MAXQ; newest backup in $BACKUP_DIR/daily < ${MAXAGE}h old;"
  say "DRY-RUN free disk >= ${MINPCT}% and >= ${MINGB} GB on $PILOT_ROOT and $BACKUP_DIR; verify-snapshots; then append one line to $PILOT_DATA/health.log"
  exit 0
fi

problems=() facts=()
# 1 services
bad=""
SERVICES="postgres redis minio api worker ocr-worker ocr web"
[ "$(env_get GRADEMIND_COOKIE_SECURE)" = true ] && SERVICES="$SERVICES proxy"
for s in $SERVICES; do
  h="$(docker compose ps --format '{{.Health}}' "$s" 2> /dev/null | tr -d '\r' || true)"
  [ "$h" = healthy ] || bad="$bad $s(${h:-down})"
done
[ -z "$bad" ] && facts+=("services=healthy") || problems+=("services not healthy:$bad")
# 2 OCR queue
q="$(docker compose exec -T redis redis-cli llen ocr 2> /dev/null | tr -d '\r\n ' || true)"
if [[ "$q" =~ ^[0-9]+$ ]]; then
  facts+=("ocr_queue=$q"); [ "$q" -le "$MAXQ" ] || problems+=("OCR queue has $q waiting (limit $MAXQ)")
else problems+=("OCR queue could not be read"); fi
# 3 backup age
newest="$(ls -1 "$BACKUP_DIR/daily" 2> /dev/null | grep -E '^[0-9]{8}T[0-9]{6}Z$' | sort | tail -1 || true)"
if [ -z "$newest" ]; then problems+=("no nightly backup found in $BACKUP_DIR/daily")
else
  t="${newest:0:4}-${newest:4:2}-${newest:6:2}T${newest:9:2}:${newest:11:2}:${newest:13:2}Z"
  age_h=$(( ( $(date -u +%s) - $(date -u -d "$t" +%s) ) / 3600 ))
  facts+=("backup_age_h=$age_h"); [ "$age_h" -lt "$MAXAGE" ] || problems+=("newest backup is ${age_h}h old (limit ${MAXAGE}h)")
fi
# 4 disk
for d in "$PILOT_ROOT" "$BACKUP_DIR"; do
  [ -d "$d" ] || continue
  read -r total avail < <(df -Pk "$d" | awk 'NR==2{print $2, $4}')
  pct=$(( avail * 100 / total )); gb=$(( avail / 1048576 ))
  facts+=("free[$(basename "$d")]=${pct}%/${gb}GB")
  { [ "$pct" -ge "$MINPCT" ] && [ "$gb" -ge "$MINGB" ]; } || problems+=("low disk space on $d: ${pct}% free, ${gb} GB")
done
# 5 results intact
if out="$(docker compose exec -T api python -m grademind_core.cli verify-snapshots 2>&1)"; then facts+=("verify=ok")
else problems+=("verify-snapshots FAILED: finalized results do not reproduce"); fi

mkdir -p "$PILOT_DATA"
if [ "${#problems[@]}" -eq 0 ]; then line="$(now_utc) OK ${facts[*]}"; rc=0
else line="$(now_utc) FAIL $(printf '%s; ' "${problems[@]}")| ${facts[*]}"; rc=1; fi
echo "$line" >> "$PILOT_DATA/health.log"
echo "$line"
exit "$rc"
