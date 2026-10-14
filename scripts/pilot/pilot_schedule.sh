#!/usr/bin/env bash
# Install, remove or show the pilot's schedule (D30.4, D31): the nightly backup (Sundays: also the weekly ENCRYPTED copy to the off-machine folder;
# kept 14 daily / 8 weekly) and the daily health check. Cron (default) or systemd user timers. No email, no cloud: the owner reads docs/PILOT_DAY.md.
#   scripts/pilot/pilot_schedule.sh install   --recipients FILE --offsite DIR [--backup-dir DIR] [--backup-time 02:17] [--health-time 07:30]
#                                             [--systemd] [--allow-same-device] [--dry-run]
#   scripts/pilot/pilot_schedule.sh uninstall [--systemd] [--dry-run]
#   scripts/pilot/pilot_schedule.sh status    [--systemd]
# FILE holds the owner's PUBLIC age key (age1...). A file with a private key in it is refused: the private key never lives on this machine.
# Safe to run again: the same entries are replaced, never duplicated.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
CMD="${1:-}"; [ $# -gt 0 ] && shift
RECIP="" OFFSITE="" BACKUP_DIR="" BTIME="02:17" HTIME="07:30" MODE=cron SAMEDEV=0
while [ $# -gt 0 ]; do
  case "$1" in
    --recipients) RECIP="${2:?}"; shift 2 ;;
    --offsite) OFFSITE="${2:?}"; shift 2 ;;
    --backup-dir) BACKUP_DIR="${2:?}"; shift 2 ;;
    --backup-time) BTIME="${2:?}"; shift 2 ;;
    --health-time) HTIME="${2:?}"; shift 2 ;;
    --systemd) MODE=systemd; shift ;;
    --cron) MODE=cron; shift ;;
    --allow-same-device) SAMEDEV=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
done
BEGIN="# BEGIN grademind-pilot (managed by scripts/pilot/pilot_schedule.sh; edit with that script)"
END="# END grademind-pilot"
UNITDIR="${PILOT_UNIT_DIR:-$HOME/.config/systemd/user}"
hm() { # "HH:MM" -> "M H"
  [[ "$1" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]] || die "time '$1' must be HH:MM (24 h)"
  echo "$((10#${BASH_REMATCH[2]})) $((10#${BASH_REMATCH[1]}))"
}

cron_block() {
  echo "$BEGIN"
  echo "PATH=/usr/local/bin:/usr/bin:/bin"
  echo "$(hm "$BTIME") * * * cd $PILOT_ROOT && ./scripts/pilot/pilot_backup_job.sh"
  echo "$(hm "$HTIME") * * * cd $PILOT_ROOT && ./scripts/pilot/pilot_health.sh > /dev/null 2>&1"
  echo "$END"
}
current_cron() { crontab -l 2> /dev/null || true; }
strip_block() { awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}'; }

unit() { # unit NAME DESCRIPTION EXEC TIME
  cat > "$UNITDIR/$1.service" <<U
[Unit]
Description=$2

[Service]
Type=oneshot
WorkingDirectory=$PILOT_ROOT
ExecStart=$PILOT_ROOT/scripts/pilot/$3
U
  cat > "$UNITDIR/$1.timer" <<U
[Unit]
Description=$2 (timer)

[Timer]
OnCalendar=*-*-* $4:00
Persistent=true

[Install]
WantedBy=timers.target
U
}

case "$CMD" in
  install)
    hm "$BTIME" > /dev/null; hm "$HTIME" > /dev/null
    [ -n "$RECIP" ] && [ -n "$OFFSITE" ] || die "install needs --recipients FILE (the owner's PUBLIC key) and --offsite DIR (a different disk or share)"
    [ -f "$RECIP" ] || die "$RECIP does not exist"
    has_private_key "$RECIP" && die "$RECIP contains an age PRIVATE key. Refusing: make the key pair on another machine and give this script only the public line (age1...)."
    grep -qE '^age1[a-z0-9]{20,}' "$RECIP" || die "$RECIP has no public key line (age1...)"
    has_private_key .env && die ".env contains an age PRIVATE key; remove it first"
    BACKUP_DIR="${BACKUP_DIR:-$PILOT_ROOT/backups}"
    RECIP="$(cd "$(dirname "$RECIP")" && pwd)/$(basename "$RECIP")"
    if [ "$DRY" = 0 ]; then
      mkdir -p "$OFFSITE" "$BACKUP_DIR"
      OFFSITE="$(cd "$OFFSITE" && pwd)"; BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
      if [ "$SAMEDEV" = 0 ] && [ "$(stat -c %d "$OFFSITE")" = "$(stat -c %d "$BACKUP_DIR")" ]; then
        die "the off-machine folder $OFFSITE is on the same disk as the backups ($BACKUP_DIR): a disk failure would take both. Use another disk or share (or --allow-same-device if you know better)."
      fi
    fi
    say "settings -> $PILOT_DATA/schedule.env (paths only)"
    if [ "$DRY" = 0 ]; then
      mkdir -p "$PILOT_DATA"
      ( umask 077; cat > "$PILOT_DATA/schedule.env" <<S
GRADEMIND_BACKUP_DIR=$BACKUP_DIR
GRADEMIND_BACKUP_RECIPIENTS=$RECIP
GRADEMIND_OFFSITE_DIR=$OFFSITE
GRADEMIND_KEEP_DAILY=14
GRADEMIND_KEEP_WEEKLY=8
S
      )
    else
      say "DRY-RUN would write: GRADEMIND_BACKUP_DIR=${BACKUP_DIR:-$PILOT_ROOT/backups} GRADEMIND_BACKUP_RECIPIENTS=$RECIP GRADEMIND_OFFSITE_DIR=$OFFSITE (keep 14 daily / 8 weekly)"
    fi
    run ./scripts/fetch_age.sh
    if [ "$MODE" = cron ]; then
      block="$(cron_block)"
      if [ "$DRY" = 1 ]; then say "DRY-RUN would put this in your crontab (replacing any earlier grademind-pilot block):"; echo "$block" | sed 's/^/    /'
      else { current_cron | strip_block; echo "$block"; } | crontab -; say "cron entries installed:"; echo "$block" | sed 's/^/    /'; fi
    else
      if [ "$DRY" = 1 ]; then
        say "DRY-RUN would write systemd user units grademind-backup and grademind-health into $UNITDIR (backup $BTIME, health $HTIME) and enable the timers"
      else
        mkdir -p "$UNITDIR"
        unit grademind-backup "GradeMIND nightly backup" pilot_backup_job.sh "$BTIME"
        unit grademind-health "GradeMIND daily health check" pilot_health.sh "$HTIME"
        systemctl --user daemon-reload
        systemctl --user enable --now grademind-backup.timer grademind-health.timer
        say "systemd user timers enabled (they keep running while you are logged out only if 'loginctl enable-linger $USER' was done)"
      fi
    fi
    ;;
  uninstall)
    if [ "$MODE" = cron ]; then
      if [ "$DRY" = 1 ]; then say "DRY-RUN would remove the grademind-pilot block from your crontab"
      else current_cron | strip_block | crontab -; say "cron entries removed"; fi
    else
      if [ "$DRY" = 1 ]; then say "DRY-RUN would disable the grademind timers and remove their unit files from $UNITDIR"
      else
        systemctl --user disable --now grademind-backup.timer grademind-health.timer 2> /dev/null || true
        rm -f "$UNITDIR"/grademind-backup.* "$UNITDIR"/grademind-health.*; systemctl --user daemon-reload; say "systemd timers removed"
      fi
    fi
    say "(pilot-data/schedule.env and the backups themselves are left alone)" ;;
  status)
    if [ "$MODE" = cron ]; then
      if current_cron | grep -qF "$BEGIN"; then say "INSTALLED (cron):"; current_cron | awk -v b="$BEGIN" -v e="$END" '$0==b{on=1} on{print "    " $0} $0==e{on=0}'
      else say "NOT INSTALLED (cron)"; exit 1; fi
    else
      if systemctl --user is-enabled grademind-backup.timer grademind-health.timer > /dev/null 2>&1; then say "INSTALLED (systemd)"; systemctl --user list-timers 'grademind-*' --no-pager || true
      else say "NOT INSTALLED (systemd)"; exit 1; fi
    fi ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
