#!/usr/bin/env bash
# Shared helpers for scripts/pilot/*.sh (D31 pilot automation). Source it; do not run it.
#   PILOT_ROOT   the repository folder (tests point it at a fixture)
#   PILOT_DATA   where this automation keeps its files (default $PILOT_ROOT/pilot-data; git-ignored)
#   DRY          1 = print what would be done, change nothing
# Nothing here prints a secret: values are read from .env only by the commands that need them, and never echoed.
set -euo pipefail
PILOT_ROOT="${PILOT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PILOT_DATA="${PILOT_DATA:-$PILOT_ROOT/pilot-data}"
DRY="${DRY:-0}"
cd "$PILOT_ROOT"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
# run CMD...: execute, or in a dry run only say what would be executed
run() { if [ "$DRY" = 1 ]; then printf 'DRY-RUN would run: %s\n' "$*"; else "$@"; fi; }

# env_get KEY [FILE]: the value of KEY in the env file (default .env), or empty. Never sourced (a value may contain spaces).
env_get() { local f="${2:-.env}"; [ -f "$f" ] || return 0; grep -E "^$1=" "$f" | tail -1 | cut -d= -f2- || true; }

# Compose the way the pilot runs it: with the https profile when .env says secure cookies.
pilot_compose_env() {
  if [ "$(env_get GRADEMIND_COOKIE_SECURE)" = true ]; then
    export COMPOSE_FILE="compose.yaml:compose.https.yaml" COMPOSE_PROFILES="https"
  fi
}

# A private key must never be on this machine (D30.4): true when the file holds one.
has_private_key() { [ -f "$1" ] && grep -q 'AGE-SECRET-KEY-' "$1"; }

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
