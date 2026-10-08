#!/usr/bin/env bash
# Phase 0 OCR spike driver. Usage: spike/run_spike.sh <pages_dir> [engine_a_mode]
# Writes spike/runs/<run_id>/{env.json,engine_a/,engine_b/,*.log}. Outputs contain student text: gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAGES_DIR="$1"
MODE="${2:-gundam}"
MODEL_DIR="${UNLIMITED_OCR_DIR:-$HOME/models/Unlimited-OCR}"
MODEL_REV="${UNLIMITED_OCR_REV:?set UNLIMITED_OCR_REV to the pinned HF revision}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${MODE}"
RUN="$ROOT/spike/runs/$RUN_ID"
mkdir -p "$RUN"
mapfile -t PAGES < <(ls "$PAGES_DIR"/*.jpg "$PAGES_DIR"/*.png 2>/dev/null | sort)

cat > "$RUN/env.json" <<EOF
{"run_id": "$RUN_ID", "git_commit": "$(git -C "$ROOT" rev-parse HEAD)",
 "gpu": "$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)",
 "engine_a": {"model_dir": "$MODEL_DIR", "revision": "$MODEL_REV", "mode": "$MODE"},
 "pages_dir": "$PAGES_DIR", "n_pages": ${#PAGES[@]}}
EOF

echo "== run $RUN_ID: ${#PAGES[@]} pages"
"$ROOT/spike/engine_b/.venv/bin/python" "$ROOT/spike/engine_b/run_engine_b.py" \
    --pages "${PAGES[@]}" --out "$RUN/engine_b" 2>&1 | tee "$RUN/engine_b.log"
"$ROOT/spike/engine_a/.venv/bin/python" "$ROOT/spike/engine_a/run_engine_a.py" \
    --model "$MODEL_DIR" --revision "$MODEL_REV" --mode "$MODE" \
    --pages "${PAGES[@]}" --out "$RUN/engine_a" 2>&1 | tee "$RUN/engine_a.log"
echo "== done: $RUN"
