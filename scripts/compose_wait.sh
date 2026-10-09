#!/usr/bin/env bash
# Wait until every long-running compose service is healthy and the one-shot services exited 0. Exit 1 on timeout/failure.
set -euo pipefail
deadline=$(( $(date +%s) + ${1:-900} ))
while true; do
  bad=0; pending=""
  for svc in migrate bootstrap; do
    st=$(docker compose ps -a --format '{{.State}} {{.ExitCode}}' "$svc" 2>/dev/null || true)
    case "$st" in "exited 0") ;; exited*) echo "one-shot $svc failed: $st"; exit 1 ;; *) pending="$pending $svc" ;; esac
  done
  for svc in postgres redis minio api worker ocr web; do
    h=$(docker compose ps --format '{{.Health}}' "$svc" 2>/dev/null || true)
    [ "$h" = healthy ] || { pending="$pending $svc(${h:-down})"; bad=1; }
  done
  [ -z "$pending" ] && { echo "compose: all services healthy"; exit 0; }
  [ "$(date +%s)" -gt "$deadline" ] && { echo "compose: timed out waiting for:$pending"; exit 1; }
  sleep 5
done
