#!/usr/bin/env bash
# Phase 0 OCR spike driver.
# Usage: spike/run_spike.sh [--mode gundam|base] [--engines ab|a|b] [--quant int8|offload|none] [--max-length N] <sheet_dir>...
#   <sheet_dir> is e.g. data/samples/sheet_001 (pages read from <sheet_dir>/pages/*.jpg|png)
# Writes spike/runs/<run_id>/{env.json,<sheet_id>/engine_a/,<sheet_id>/engine_b/,*.log}.
# Outputs contain student text: gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE=gundam
ENGINES=ab
QUANT=int8
MAXLEN=4096
while [[ $# -gt 0 && "$1" == --* ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --engines) ENGINES="$2"; shift 2 ;;
        --quant) QUANT="$2"; shift 2 ;;
        --max-length) MAXLEN="$2"; shift 2 ;;
        *) echo "unknown flag $1" >&2; exit 2 ;;
    esac
done
[[ $# -ge 1 ]] || { echo "usage: $0 [--mode M] [--engines ab|a|b] <sheet_dir>..." >&2; exit 2; }

MODEL_DIR="${UNLIMITED_OCR_DIR:-$HOME/models/Unlimited-OCR}"
MODEL_REV="${UNLIMITED_OCR_REV:?set UNLIMITED_OCR_REV to the pinned HF revision}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${MODE}_${QUANT}"
RUN="$ROOT/spike/runs/$RUN_ID"
mkdir -p "$RUN"

cat > "$RUN/env.json" <<EOF
{"run_id": "$RUN_ID", "git_commit": "$(git -C "$ROOT" rev-parse HEAD)",
 "git_dirty": $([[ -n "$(git -C "$ROOT" status --porcelain)" ]] && echo true || echo false),
 "gpu": "$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)",
 "gpu_used_mib_before": $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits),
 "engine_a": {"model_dir": "$MODEL_DIR", "revision": "$MODEL_REV", "mode": "$MODE", "quant": "$QUANT", "max_length": $MAXLEN},
 "engines": "$ENGINES", "sheets": "$*"}
EOF

for SHEET_DIR in "$@"; do
    SHEET="$(basename "$SHEET_DIR")"
    mapfile -t PAGES < <(ls "$SHEET_DIR"/pages/*.jpg "$SHEET_DIR"/pages/*.png 2>/dev/null | sort)
    echo "== $RUN_ID / $SHEET: ${#PAGES[@]} pages"
    if [[ "$ENGINES" == *b* ]]; then
        "$ROOT/spike/engine_b/.venv/bin/python" "$ROOT/spike/engine_b/run_engine_b.py" \
            --pages "${PAGES[@]}" --out "$RUN/$SHEET/engine_b" 2>&1 | tee "$RUN/$SHEET.engine_b.log"
    fi
    if [[ "$ENGINES" == *a* ]]; then
        PYTORCH_ALLOC_CONF=expandable_segments:True "$ROOT/spike/engine_a/.venv/bin/python" "$ROOT/spike/engine_a/run_engine_a.py" \
            --model "$MODEL_DIR" --revision "$MODEL_REV" --mode "$MODE" --quant "$QUANT" --max-length "$MAXLEN" \
            --pages "${PAGES[@]}" --out "$RUN/$SHEET/engine_a" 2>&1 | tee "$RUN/$SHEET.engine_a.log"
    fi
done
echo "== done: $RUN"
