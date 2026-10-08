#!/usr/bin/env bash
# Phase 0b driver (owner decision D1 a-c). Process rule 9: snapshots spike/ into the run dir and executes the COPY.
# Usage: spike/run_phase0b.sh
# Variants: raw (data/samples/<sheet>/pages) and st010 (spike/pages/<sheet>_st010/pages, show-through v0.1.0).
# Engines per variant/sheet, sequential: b_mobile, b_server, c_trocr (on b_server line boxes).
# Output: spike/runs/<run_id>/{code/,env.json,<variant>/<sheet>/<engine>/page_XX.json,logs/}. Gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_phase0b"
RUN="$ROOT/spike/runs/$RUN_ID"
mkdir -p "$RUN/logs"
rsync -a --exclude '.venv' --exclude 'runs' --exclude 'pages' --exclude '__pycache__' "$ROOT/spike/" "$RUN/code/"
CODE="$RUN/code"
TROCR_DIR="${TROCR_DIR:-$HOME/models/trocr-large-handwritten}"
TROCR_REV="e68501f437cd2587ae5d68ee457964cac824ddee"

cat > "$RUN/env.json" <<EOF
{"run_id": "$RUN_ID", "git_commit": "$(git -C "$ROOT" rev-parse HEAD)",
 "git_dirty": $([[ -n "$(git -C "$ROOT" status --porcelain)" ]] && echo true || echo false),
 "code_snapshot": "code/", "gpu": "$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)",
 "gpu_used_mib_before": $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits),
 "trocr": {"dir": "$TROCR_DIR", "revision": "$TROCR_REV"},
 "show_through": "$(python3 -c "import json;print(json.load(open('$ROOT/spike/pages/sheet_001_st010/show_through.json'))['version'])")"}
EOF

PY_B="$ROOT/spike/engine_b/.venv/bin/python"
PY_C="$ROOT/spike/engine_c/.venv/bin/python"
for VARIANT in raw st010; do
    for SHEET in sheet_001 sheet_002; do
        if [[ "$VARIANT" == raw ]]; then PDIR="$ROOT/data/samples/$SHEET/pages"; else PDIR="$ROOT/spike/pages/${SHEET}_st010/pages"; fi
        mapfile -t PAGES < <(ls "$PDIR"/*.jpg | sort)
        OUT="$RUN/$VARIANT/$SHEET"
        echo "== $VARIANT/$SHEET b_mobile"
        "$PY_B" "$CODE/engine_b/run_engine_b.py" --pages "${PAGES[@]}" --out "$OUT/b_mobile" > "$RUN/logs/$VARIANT.$SHEET.b_mobile.log" 2>&1
        echo "== $VARIANT/$SHEET b_server"
        "$PY_B" "$CODE/engine_b/run_engine_b.py" --rec-model PP-OCRv5_server_rec --pages "${PAGES[@]}" \
            --out "$OUT/b_server" > "$RUN/logs/$VARIANT.$SHEET.b_server.log" 2>&1
        echo "== $VARIANT/$SHEET c_trocr"
        "$PY_C" "$CODE/engine_c/run_line_htr.py" --model "$TROCR_DIR" --revision "$TROCR_REV" --pages "${PAGES[@]}" \
            --b-dir "$OUT/b_server" --out "$OUT/c_trocr" > "$RUN/logs/$VARIANT.$SHEET.c_trocr.log" 2>&1
    done
done
echo "== done: $RUN"
