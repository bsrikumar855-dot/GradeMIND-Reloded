#!/usr/bin/env bash
# After a pilot batch (D31.2): the machine-reading error numbers, with the warning that must stay attached to them. Numbers only: never student text.
#   scripts/pilot/pilot_report.sh [--exam ID ...] [--sample FOLDER ...] [--no-auto-samples] [--dry-run]
# 1. pilot-cer: the error rate AMONG LINES EXAMINERS CORRECTED (not the system's accuracy: lines the machine read right leave no row).
# 2. For each transcribed sample folder (--sample, or every ~/pilot-samples/*/ that has transcriptions): pilot-sample-cer, the owner's random sample.
#    The folder holds student handwriting; it is copied into the api container only for the run and removed again.
# Exit: 0 both parts printed numbers; 2 a part refused (too few lines: see its message); 1 an error.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
EXAMS=() SAMPLES=() AUTO=1
while [ $# -gt 0 ]; do
  case "$1" in
    --exam) EXAMS+=("--exam" "${2:?}"); shift 2 ;;
    --sample) SAMPLES+=("${2:?}"); shift 2 ;;
    --no-auto-samples) AUTO=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
done
pilot_compose_env
if [ "$AUTO" = 1 ] && [ "${#SAMPLES[@]}" -eq 0 ]; then
  for d in "$HOME"/pilot-samples/*/; do [ -f "${d}transcriptions.json" ] && grep -q '"text": "' "${d}transcriptions.json" && SAMPLES+=("${d%/}"); done
fi
note_exit() { case "$1" in 0) ;; 2) refused=1 ;; *) err=1 ;; esac; }
banner() {
  printf '\n##########################################################################################\n'
  printf '#  %-84s  #\n' "$1"
  printf '#  %-84s  #\n' "$2"
  printf '##########################################################################################\n'
}
refused=0 err=0
banner "ERROR AMONG CORRECTED LINES ONLY, NOT SYSTEM ACCURACY" "(lines the machine read right and examiners left alone are not in this number)"
if [ "$DRY" = 1 ]; then
  say "DRY-RUN would run: docker compose exec -T api python -m grademind_core.cli pilot-cer ${EXAMS[*]:-}"
else
  set +e; docker compose exec -T api python -m grademind_core.cli pilot-cer "${EXAMS[@]}"; c=$?; set -e
  note_exit $c
fi
for s in "${SAMPLES[@]}"; do
  [ -f "$s/transcriptions.json" ] || { echo "skipping $s: not a sample folder" >&2; err=1; continue; }
  tmp="/tmp/pilot_sample_$(printf '%s' "$(basename "$s")" | tr -c 'A-Za-z0-9_-' '_')"
  banner "RANDOM SAMPLE, TRANSCRIBED BY THE OWNER: batch folder $(basename "$s")" "(holds for these booklets, pens and subjects only; see docs/PILOT_PROTOCOL.md)"
  if [ "$DRY" = 1 ]; then say "DRY-RUN would copy $s into the api container ($tmp), run pilot-sample-cer on it, and remove the copy"; continue; fi
  docker compose exec -T api rm -rf "$tmp" > /dev/null 2>&1 || true
  docker compose cp "$s" "api:$tmp" > /dev/null
  set +e; docker compose exec -T api python -m grademind_core.cli pilot-sample-cer "$tmp"; c=$?; set -e
  docker compose exec -T api rm -rf "$tmp" > /dev/null 2>&1 || true
  note_exit $c
done
[ "${#SAMPLES[@]}" -eq 0 ] && say "
(no transcribed sample found: for the real accuracy figure, draw and transcribe one: RUNBOOK 'Measure the machine's real accuracy')"
[ "$err" = 1 ] && exit 1
[ "$refused" = 1 ] && exit 2
exit 0
