#!/usr/bin/env bash
# First-time pilot setup (D31; RUNBOOK "First start" + "HTTPS"):
#   scripts/pilot/pilot_setup.sh --host grademind.lan --bind 192.168.1.20 --org "My College" --admin-email admin@college.edu [--dry-run]
# Writes .env for HTTPS (random secrets, never printed), fetches the vendored OCR files, starts the stack with the https profile, waits until
# it is healthy, saves the local CA's root certificate to pilot-data/ca/root.crt and prints how to install it on devices.
# Refuses to overwrite an existing .env: --force replaces it (the old one is kept as .env.bak.<time>, mode 600); --keep-env reuses it as it is.
# Safe to run again with --keep-env. Needs: docker (with compose), python3, curl. It never creates or stores a backup key.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HOST="" BIND="" ORG="" ADMIN_EMAIL="" FORCE=0 KEEP=0 YES=0
usage() { sed -n '2,8p' "$0"; exit "${1:-2}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?--host needs a value}"; shift 2 ;;
    --bind) BIND="${2:?--bind needs a value}"; shift 2 ;;
    --org) ORG="${2:?--org needs a value}"; shift 2 ;;
    --admin-email) ADMIN_EMAIL="${2:?--admin-email needs a value}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --keep-env) KEEP=1; shift ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option $1" >&2; usage ;;
  esac
done
[ "$FORCE" = 1 ] && [ "$KEEP" = 1 ] && die "--force and --keep-env exclude each other"

ask() { # ask VAR "question" default: only when a person is there to answer
  local var="$1" q="$2" def="${3:-}" ans=""
  if [ -n "${!var}" ]; then return 0; fi
  if [ "$YES" = 0 ] && [ -t 0 ]; then read -r -p "$q${def:+ [$def]}: " ans; fi
  printf -v "$var" '%s' "${ans:-$def}"
  [ -n "${!var}" ] || die "$q: give it with a flag (no one to ask)"
}

if [ "$KEEP" = 1 ]; then
  [ -f .env ] || die "--keep-env: there is no .env here"
  HOST="${HOST:-$(env_get GRADEMIND_HOST)}"; BIND="${BIND:-$(env_get GRADEMIND_HTTPS_BIND)}"
fi
ask HOST "Name or fixed address people will type (for example grademind.lan)"
ask BIND "LAN or VPN address of THIS machine to listen on" "127.0.0.1"
if [ "$KEEP" = 0 ]; then
  ask ORG "Organisation (college) name" "My College"
  ask ADMIN_EMAIL "Administrator email"
fi
[[ "$HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "--host '$HOST' is not a host name or address"
[[ "$BIND" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "--bind '$BIND' is not an IPv4 address"
[ "$KEEP" = 1 ] || [[ "$ADMIN_EMAIL" =~ ^[^@\ ]+@[^@\ ]+\.[^@\ ]+$ ]] || die "--admin-email '$ADMIN_EMAIL' is not an email address"
[ "$KEEP" = 1 ] || [[ "$ORG" != *$'\n'* && "$ORG" != *=* ]] || die "--org may not contain '=' or a line break"

# ---------------------------------------------------------------------------------------------------------- .env
if [ "$KEEP" = 1 ]; then
  say "keeping the existing .env"
  [ "$(env_get GRADEMIND_COOKIE_SECURE)" = true ] || die ".env does not have GRADEMIND_COOKIE_SECURE=true: it is not an HTTPS setup (use --force to write a new one)"
elif [ -f .env ] && [ "$FORCE" = 0 ]; then
  die ".env already exists: not overwriting it. Use --keep-env to run with it as it is, or --force to replace it (a copy is kept)."
else
  if [ "$DRY" = 1 ]; then
    say "DRY-RUN would write .env (mode 600) with random values for GRADEMIND_JWT_SECRET, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, GRADEMIND_ADMIN_PASSWORD,"
    say "DRY-RUN and GRADEMIND_HOST=$HOST GRADEMIND_COOKIE_SECURE=true GRADEMIND_S3_PUBLIC_ENDPOINT_URL=https://$HOST:9443 GRADEMIND_HTTPS_BIND=$BIND"
    [ -f .env ] && say "DRY-RUN would first keep the old .env as .env.bak.<time> (mode 600)"
  else
    if [ -f .env ]; then bak=".env.bak.$(date -u +%Y%m%dT%H%M%SZ)"; ( umask 077; cp .env "$bak" ); say "old .env kept as $bak (mode 600; delete it when you no longer need it)"; fi
    ( umask 077
      HOST="$HOST" BIND="$BIND" ORG="$ORG" ADMIN_EMAIL="$ADMIN_EMAIL" python3 - <<'PY' > .env.new
import os, secrets
g = lambda n: secrets.token_urlsafe(n)
print("# Written by scripts/pilot/pilot_setup.sh. Secrets are random; this file is mode 600 and git-ignored. Never share it.")
print(f"GRADEMIND_JWT_SECRET={g(48)}")
print(f"POSTGRES_PASSWORD={g(24)}")
print("MINIO_ROOT_USER=grademind")
print(f"MINIO_ROOT_PASSWORD={g(24)}")
print(f"GRADEMIND_ADMIN_ORG={os.environ['ORG']}")
print(f"GRADEMIND_ADMIN_EMAIL={os.environ['ADMIN_EMAIL'].lower()}")
print(f"GRADEMIND_ADMIN_PASSWORD={g(18)}")
print(f"GRADEMIND_HOST={os.environ['HOST']}")
print("GRADEMIND_COOKIE_SECURE=true")
print(f"GRADEMIND_S3_PUBLIC_ENDPOINT_URL=https://{os.environ['HOST']}:9443")
print(f"GRADEMIND_HTTPS_BIND={os.environ['BIND']}")
PY
    )
    mv .env.new .env; chmod 600 .env
    say ".env written (mode 600)"
  fi
fi
has_private_key .env && die ".env contains an age PRIVATE key. The key must live off this machine (D30.4): remove it."
pilot_compose_env
[ "$DRY" = 1 ] && export COMPOSE_FILE="compose.yaml:compose.https.yaml" COMPOSE_PROFILES="https"

# ---------------------------------------------------------------------------------------------------------- stack
if [ "$DRY" = 0 ]; then command -v docker > /dev/null || die "docker is not installed or not on PATH"; fi
run ./scripts/fetch_ocr_vendor.sh
run docker compose -f compose.yaml -f compose.https.yaml --profile https up -d --build
run ./scripts/compose_wait.sh 1500

# ---------------------------------------------------------------------------------------------------------- CA root + proof
CA="$PILOT_DATA/ca/root.crt"
if [ "$DRY" = 1 ]; then
  say "DRY-RUN would copy the proxy's local CA root certificate (a certificate, not a key) to $CA"
  say "DRY-RUN would check that https://$HOST:8443/login answers with a chain that verifies against it"
else
  mkdir -p "$PILOT_DATA/ca"
  docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt "$CA" > /dev/null
  [ -s "$CA" ] || die "could not copy the CA root certificate out of the proxy"
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --cacert "$CA" --resolve "$HOST:8443:${BIND}" "https://$HOST:8443/login" || true)"
  [ "$code" = 200 ] || die "https://$HOST:8443/login answered '$code' (expected 200) with the proxy's own certificate chain: look at 'docker compose logs proxy'"
  say "HTTPS works: https://$HOST:8443/login answers 200 and its certificate chain verifies against the root saved below"
  FP="$(openssl x509 -in "$CA" -noout -fingerprint -sha256 2> /dev/null | cut -d= -f2 || true)"
fi

if [ "$DRY" = 1 ]; then say ""; say "DRY RUN finished: nothing was changed."; exit 0; fi
cat <<TXT

==================================================================================================
 THE STACK IS UP:  https://$HOST:8443
 Administrator:    $(env_get GRADEMIND_ADMIN_EMAIL)   (the password is in .env, mode 600: read it there once, sign in,
                   then change it under Account. It is not printed here on purpose.)
 CA root (a certificate, not a key):  $CA${FP:+
 Its SHA-256 fingerprint (check it on the device when you install it): $FP}

 INSTALL THE ROOT ON EVERY DEVICE (once; without it browsers warn):
   Windows   double-click root.crt > Install Certificate > Local Machine > "Trusted Root Certification Authorities"
   macOS     double-click > Keychain Access > System keychain; open it > Trust > "Always Trust"
   iPhone/iPad  send/AirDrop the file > Settings > "Profile Downloaded" > Install; then Settings > General > About >
             Certificate Trust Settings > switch it on
   Android   Settings > Security > Encryption & credentials > Install a certificate > CA certificate
   Linux / Firefox   import it under Settings > Certificates > Authorities (Firefox keeps its own list)
 Then open https://$HOST:8443 on the device: no warning page should appear.

 NEXT:  1) make the backup key pair on ANOTHER machine (age-keygen); only the PUBLIC line comes here.
        2) scripts/pilot/pilot_users.py     create the accounts from a CSV
        3) scripts/pilot/pilot_schedule.sh  install the nightly backup and the daily health check
        4) scripts/pilot/pilot_preflight.sh  every machine-checkable line of the release checklist
==================================================================================================
TXT
