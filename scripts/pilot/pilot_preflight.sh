#!/usr/bin/env bash
# Every MACHINE-CHECKABLE line of the RELEASE CHECKLIST (PHASE_5_REPORT.md) as PASS / FAIL, and every line a machine cannot check as MANUAL
# (never ticked for you). Exit 1 if any line FAILS; MANUAL lines do not change the exit code. Read-only: it changes nothing.
#   scripts/pilot/pilot_preflight.sh [--dry-run]
#   scripts/pilot/pilot_preflight.sh --record-restore-drill [YYYY-MM-DD]   write down that YOU did the monthly restore drill (default: today)
# Optional: PILOT_ADMIN_EMAIL + PILOT_ADMIN_PASSWORD in the environment let it sign in once and check the real session cookie's flags.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
RECORD=0 DRILL_DATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --record-restore-drill) RECORD=1; shift; if [ $# -gt 0 ] && [[ "$1" != --* ]]; then DRILL_DATE="$1"; shift; fi ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
done
DRILL_FILE="$PILOT_DATA/restore-drill.txt"
if [ "$RECORD" = 1 ]; then
  DRILL_DATE="${DRILL_DATE:-$(date -u +%F)}"
  [[ "$DRILL_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && date -u -d "$DRILL_DATE" +%F > /dev/null 2>&1 || die "the date must be YYYY-MM-DD"
  [ "$DRY" = 1 ] && { say "DRY-RUN would append '$DRILL_DATE' to $DRILL_FILE"; exit 0; }
  mkdir -p "$PILOT_DATA"; echo "$DRILL_DATE recorded-by-owner $(now_utc)" >> "$DRILL_FILE"
  say "recorded: restore drill on $DRILL_DATE (this is your own record: nothing here checks that the drill really passed)"; exit 0
fi
[ -f "$PILOT_DATA/schedule.env" ] && { set -a; . "$PILOT_DATA/schedule.env"; set +a; }
pilot_compose_env
HOST="$(env_get GRADEMIND_HOST)"; BIND="$(env_get GRADEMIND_HTTPS_BIND)"; BIND="${BIND:-127.0.0.1}"
BACKUP_DIR="${GRADEMIND_BACKUP_DIR:-$PILOT_ROOT/backups}"
CA="$PILOT_DATA/ca/root.crt"

if [ "$DRY" = 1 ]; then
  cat <<TXT
DRY-RUN: this would check (PASS/FAIL): .env mode 600 and no private key; HTTPS settings in .env; https://$HOST:8443 answers with the CA root and HSTS;
  the web container's Secure-cookie setting (and the real cookie flags if PILOT_ADMIN_* is set); only the proxy publishes a port; verify-snapshots;
  the backup recipient file (public key only); no age PRIVATE key on this host; the off-machine folder on a different device; a nightly backup < 26 h old;
  the schedule installed and the health check recent; the restore-drill date recorded (< 35 days); administrators, examiners and assignments present.
  And it would list as MANUAL: the walk-through, screen reader and real devices, D20 consent per booklet, examiners reading the explainer, the CA root on every
  device, LAN/VPN reachability from outside, the key pair made and stored safely, the owner's restore drill itself, and the unchanged owner items.
TXT
  exit 0
fi

PASS=0 FAIL=0 MANUAL=0
ok()   { PASS=$((PASS+1));   printf 'PASS    %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1));   printf 'FAIL    %s\n' "$*"; }
man()  { MANUAL=$((MANUAL+1)); printf 'MANUAL  %s\n' "$*"; }
psql_() { docker compose exec -T postgres psql -U "${GRADEMIND_PG_USER:-grademind}" -d "${GRADEMIND_PG_DB:-grademind}" -Atc "$1" 2> /dev/null | tr -d '\r'; }

say "== configuration"
if [ -f .env ]; then
  [ "$(stat -c %a .env)" = 600 ] && ok ".env exists and is private (mode 600)" || bad ".env must be mode 600 (chmod 600 .env): it holds every secret"
else bad ".env is missing (scripts/pilot/pilot_setup.sh makes it)"; fi
[ "$(env_get GRADEMIND_COOKIE_SECURE)" = true ] && ok "HTTPS settings: GRADEMIND_COOKIE_SECURE=true" || bad "GRADEMIND_COOKIE_SECURE is not true in .env"
[ -n "$HOST" ] && [ "$(env_get GRADEMIND_S3_PUBLIC_ENDPOINT_URL)" = "https://$HOST:9443" ] && ok "HTTPS settings: host '$HOST' and the object-store URL agree" || bad "GRADEMIND_HOST / GRADEMIND_S3_PUBLIC_ENDPOINT_URL are missing or disagree in .env"
case "$BIND" in
  0.0.0.0|"::") bad "GRADEMIND_HTTPS_BIND=$BIND listens on every network: set it to the LAN or VPN address" ;;
  127.0.0.1) ok "listens on 127.0.0.1 only (this machine): other devices cannot connect until GRADEMIND_HTTPS_BIND is the LAN/VPN address" ;;
  *) ok "listens on $BIND only (not on every network)" ;;
esac

say "== the running system"
if command -v docker > /dev/null && docker compose ps -q > /dev/null 2>&1; then
  if [ -f "$CA" ] && [ -n "$HOST" ]; then
    hdr="$(curl -sS -D - -o /dev/null --max-time 20 --cacert "$CA" --resolve "$HOST:8443:$BIND" "https://$HOST:8443/login" 2> /dev/null | tr -d '\r' || true)"
    echo "$hdr" | head -1 | grep -q ' 200' && ok "HTTPS reachable: https://$HOST:8443/login answers 200 and its certificate verifies against $CA" || bad "https://$HOST:8443/login does not answer 200 with the pilot CA (is the stack up? docker compose logs proxy)"
    echo "$hdr" | grep -qi '^strict-transport-security:' && ok "HSTS header present" || bad "no Strict-Transport-Security header"
    if [ -n "${PILOT_ADMIN_EMAIL:-}" ] && [ -n "${PILOT_ADMIN_PASSWORD:-}" ]; then
      ck="$(curl -sS -D - -o /dev/null --max-time 20 --cacert "$CA" --resolve "$HOST:8443:$BIND" -H "Origin: https://$HOST:8443" \
        --data-urlencode "email=$PILOT_ADMIN_EMAIL" --data-urlencode "password=$PILOT_ADMIN_PASSWORD" "https://$HOST:8443/api/session" 2> /dev/null | tr -d '\r' | grep -i '^set-cookie:' || true)"
      [ -n "$ck" ] && echo "$ck" | grep -qi 'secure' && echo "$ck" | grep -qi 'httponly' && ok "the real session cookie is Secure and HttpOnly" || bad "the session cookie is missing, or not Secure and HttpOnly (or the sign-in failed)"
    else man "session cookie flags: set PILOT_ADMIN_EMAIL and PILOT_ADMIN_PASSWORD to have this checked on a real sign-in"; fi
  else bad "no CA root at $CA or no host: run scripts/pilot/pilot_setup.sh (it saves the root)"; fi
  [ "$(docker compose exec -T web printenv GRADEMIND_COOKIE_SECURE 2> /dev/null | tr -d '\r')" = true ] && ok "the running web service has Secure cookies on" || bad "the running web service does not have GRADEMIND_COOKIE_SECURE=true"
  pub="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E '(0\.0\.0\.0|127\.0\.0\.1|\[::\]|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+):[0-9]+->' | cut -d' ' -f1 | sort -u | tr '\n' ' ' || true)"
  [[ "$pub" =~ ^[a-z0-9_.-]*proxy[a-z0-9_.-]*\ $ ]] && ok "only the HTTPS proxy publishes a port ($pub)" || bad "ports are published by: ${pub:-nothing (is the stack up?)}; only the proxy should"
  out="$(docker compose exec -T api python -m grademind_core.cli verify-snapshots 2>&1 || true)"
  echo "$out" | grep -q '^VERIFY OK' && ok "verify-snapshots: $(echo "$out" | head -1)" || bad "verify-snapshots did not print VERIFY OK: $(echo "$out" | head -1 | cut -c1-100)"
else bad "docker compose cannot be reached (is Docker running, and are you in the docker group?)"; fi

say "== backups"
RECIP="${GRADEMIND_BACKUP_RECIPIENTS:-}"; OFFSITE="${GRADEMIND_OFFSITE_DIR:-}"
if [ -n "$RECIP" ] && [ -f "$RECIP" ] && grep -qE '^age1[a-z0-9]{20,}' "$RECIP"; then ok "backup recipient set: $RECIP holds a public key"; else bad "no backup recipient: run scripts/pilot/pilot_schedule.sh install --recipients <file with the PUBLIC key> --offsite <dir>"; fi
KEYRE='AGE-SECRET-'; KEYRE="${KEYRE}KEY-1[A-Z0-9]{20,}"   # (built in two parts so this script does not match itself)
found=""
for f in .env "$RECIP" "$PILOT_DATA/schedule.env"; do [ -n "$f" ] && has_private_key "$f" && found="$found $f"; done
for d in "$PILOT_ROOT" "$BACKUP_DIR" "${OFFSITE:-/nonexistent}" "$HOME/.config/age" "$HOME/.age" "$HOME/Downloads" "$HOME/Desktop" "$HOME/Documents"; do
  [ -d "$d" ] && found="$found $(grep -rIlE --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=tools -m1 "$KEYRE" "$d" 2> /dev/null | head -3 | tr '\n' ' ')" || true
done
found="$(echo "$found" | xargs -n1 2> /dev/null | sort -u | tr '\n' ' ')"
[ -z "${found// /}" ] && ok "no age PRIVATE key found on this host (looked in .env, the repository, the backup and off-machine folders, ~/.config/age, ~/.age, Downloads, Desktop, Documents)" || bad "an age PRIVATE key is on this host: ${found}: move it off this machine (D30.4)"
if [ -n "$OFFSITE" ] && [ -d "$OFFSITE" ]; then
  [ "$(stat -c %d "$OFFSITE")" != "$(stat -c %d "$BACKUP_DIR" 2> /dev/null || echo x)" ] && ok "the off-machine folder is on a different device from the backups" || bad "the off-machine folder $OFFSITE is on the SAME device as the backups: it protects against nothing"
else bad "the off-machine folder is not set or does not exist"; fi
newest="$(ls -1 "$BACKUP_DIR/daily" 2> /dev/null | grep -E '^[0-9]{8}T[0-9]{6}Z$' | sort | tail -1 || true)"
if [ -n "$newest" ]; then
  age_h=$(( ( $(date -u +%s) - $(date -u -d "${newest:0:4}-${newest:4:2}-${newest:6:2}T${newest:9:2}:${newest:11:2}:${newest:13:2}Z" +%s) ) / 3600 ))
  [ "$age_h" -lt 26 ] && ok "newest nightly backup is ${age_h} h old" || bad "newest nightly backup is ${age_h} h old (limit 26)"
else bad "no nightly backup exists yet"; fi
crontab -l 2> /dev/null | grep -qF "BEGIN grademind-pilot" && sched=cron || sched=""
[ -z "$sched" ] && systemctl --user is-enabled grademind-backup.timer > /dev/null 2>&1 && sched=systemd
[ -n "$sched" ] && ok "the schedule is installed ($sched)" || bad "the nightly backup and daily health check are not scheduled (scripts/pilot/pilot_schedule.sh install ...)"
last="$(tail -1 "$PILOT_DATA/health.log" 2> /dev/null || true)"
if [ -n "$last" ]; then
  t="$(echo "$last" | cut -d' ' -f1)"; h=$(( ( $(date -u +%s) - $(date -u -d "$t" +%s 2> /dev/null || echo 0) ) / 3600 ))
  [ "$h" -lt 26 ] && echo "$last" | grep -q ' OK ' && ok "the last health check is ${h} h old and OK" || bad "the last health check failed or is ${h} h old: $last"
else bad "the health check has not run yet (pilot-data/health.log is empty)"; fi
d="$(tail -1 "$DRILL_FILE" 2> /dev/null | cut -d' ' -f1 || true)"
if [ -n "$d" ] && days=$(( ( $(date -u +%s) - $(date -u -d "$d" +%s 2> /dev/null || echo 0) ) / 86400 )) && [ "$days" -lt 35 ]; then ok "restore drill recorded on $d (${days} days ago; your own record)"
else bad "no restore drill recorded in the last 35 days: do it (RUNBOOK, Backups) and then run: $0 --record-restore-drill"; fi

say "== people"
if docker compose ps -q > /dev/null 2>&1; then
  adm="$(psql_ "select count(*) from users where role='admin' and is_active")"; exm="$(psql_ "select count(*) from users where role='examiner' and is_active")"
  unas="$(psql_ "select count(*) from users u where role='examiner' and is_active and not exists (select 1 from exam_assignments a where a.user_id = u.id)")"
  tmp="$(psql_ "select count(*) from users where must_change_password")"
  [ "${adm:-0}" -ge 1 ] 2> /dev/null && ok "administrators present: $adm" || bad "no active administrator found"
  [ "${exm:-0}" -ge 1 ] 2> /dev/null && ok "examiners present: $exm" || bad "no active examiner: create them (scripts/pilot/pilot_users.py)"
  [ "${exm:-0}" -ge 1 ] 2> /dev/null && { [ "${unas:-1}" = 0 ] && ok "every active examiner is assigned to at least one exam" || bad "${unas:-?} active examiner(s) have no exam assigned"; }
  say "INFO    ${tmp:-?} account(s) still hold a temporary password (they must choose their own at first sign-in)"
  consent="$(psql_ "select consent_scope || ': ' || count(*) from submissions group by 1 order by 1" | tr '\n' ' ')"
  say "INFO    booklets by data-use choice so far: ${consent:-none yet}"
else bad "the database cannot be reached"; fi

say "== for you to do or confirm (MANUAL: never ticked by a script)"
man "the walk-through (docs/PILOT_WALKTHROUGH.md) is done by a screen-reader user and on a real tablet and phone and signed; or the pilot report says NOT done"
man "D20: every pilot booklet's 'Data use' is set on purpose (the INFO line above only counts them)"
man "examiners have read docs/WHAT_THIS_SYSTEM_DOES.md"
man "the CA root ($CA) is installed on every examiner device and the sign-in page opens there without a warning"
man "LAN/VPN only: from a phone on mobile data the address does NOT answer; from the college network it does"
man "the backup key pair was made on ANOTHER machine and the private key is stored safely (password manager + printed copy)"
man "you have done the restore drill with that key (RUNBOOK, Backups), then recorded it: $0 --record-restore-drill"
man "unchanged owner items: OWNER_VERIFIED manifests (Phase 0c blocked, D27), D20 consent for sheets 001 and 002, D23 data"

echo
echo "SUMMARY: $PASS PASS, $FAIL FAIL, $MANUAL MANUAL"
[ "$FAIL" = 0 ]
