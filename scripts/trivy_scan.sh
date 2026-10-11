#!/usr/bin/env bash
# Scan container images with Trivy (D30.6), as a REPORT:  scripts/trivy_scan.sh OUT_DIR IMAGE [IMAGE...]
# Trivy itself is pinned by digest (below). Its vulnerability database is downloaded at run time (that is how it works), so a report is "what is known today".
# One JSON file per image in OUT_DIR, then scripts/trivy_summary.py prints the owner-readable table. Exit 0 whatever is found: this is a report, not a gate.
set -uo pipefail
cd "$(dirname "$0")/.."
TRIVY="aquasec/trivy@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"   # Trivy 0.74.0 (published 2026-08-14)
OUT="${1:?usage: trivy_scan.sh OUT_DIR IMAGE [IMAGE...]}"
shift
[ "$#" -ge 1 ] || { echo "trivy_scan: no images given" >&2; exit 2; }
mkdir -p "$OUT"
OUT_ABS="$(cd "$OUT" && pwd)"
CACHE="${TRIVY_CACHE_DIR:-$HOME/.cache/trivy}"
mkdir -p "$CACHE"
for img in "$@"; do
  name="$(echo "$img" | sed -E 's#@sha256:[0-9a-f]+##; s#[^A-Za-z0-9._-]+#_#g')"
  echo "trivy_scan: $img"
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$CACHE:/root/.cache/" -v "$OUT_ABS:/out" "$TRIVY" \
    image --quiet --scanners vuln --format json --output "/out/$name.json" "$img" || echo "trivy_scan: could not scan $img" >&2
done
ls "$OUT_ABS"/*.json >/dev/null 2>&1 || echo "trivy_scan: no report was written. (A snap-installed Docker cannot see /tmp or hidden folders: use an OUT_DIR under your home folder.)" >&2
python3 scripts/trivy_summary.py "$OUT_ABS" || true
exit 0
