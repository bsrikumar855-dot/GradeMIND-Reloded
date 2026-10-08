#!/usr/bin/env bash
# Phase 0b controlled rerun: every PaddleOCR det/rec model named EXPLICITLY (PaddleOCR 3.7 silently substituted
# PP-OCRv6_medium_det in run 20261008T083940Z_phase0b when only the recogniser was named).
# Process rule 9: snapshots spike/ into the run dir and executes the COPY.
# Configs (x variants raw/st010 x sheets):
#   b_v5s   = PP-OCRv5_server_det + PP-OCRv5_server_rec      (owner D1a, controlled)
#   b_v6    = PP-OCRv6_medium_det + PP-OCRv6_medium_rec       (spec 5.1 "current stable successor")
#   c_v5det = TrOCR-large-handwritten on b_v5s line boxes     (owner D1b, same detector as baseline b_v5m)
# Baseline b_v5m (PP-OCRv5_server_det + en_PP-OCRv5_mobile_rec) = b_mobile in run 20261008T083940Z_phase0b.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_phase0b2"
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
 "configs": {"b_v5s": ["PP-OCRv5_server_det", "PP-OCRv5_server_rec"], "b_v6": ["PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"],
             "c_v5det": ["b_v5s boxes", "microsoft/trocr-large-handwritten@$TROCR_REV"]},
 "baseline_b_v5m": "20261008T083940Z_phase0b/<variant>/<sheet>/b_mobile",
 "show_through": "$(python3 -c "import json;print(json.load(open('$ROOT/spike/pages/sheet_001_st010/show_through.json'))['version'])")"}
EOF

PY_B="$ROOT/spike/engine_b/.venv/bin/python"
PY_C="$ROOT/spike/engine_c/.venv/bin/python"
for VARIANT in raw st010; do
    for SHEET in sheet_001 sheet_002; do
        if [[ "$VARIANT" == raw ]]; then PDIR="$ROOT/data/samples/$SHEET/pages"; else PDIR="$ROOT/spike/pages/${SHEET}_st010/pages"; fi
        mapfile -t PAGES < <(ls "$PDIR"/*.jpg | sort)
        OUT="$RUN/$VARIANT/$SHEET"
        echo "== $VARIANT/$SHEET b_v5s"
        "$PY_B" "$CODE/engine_b/run_engine_b.py" --det-model PP-OCRv5_server_det --rec-model PP-OCRv5_server_rec \
            --pages "${PAGES[@]}" --out "$OUT/b_v5s" > "$RUN/logs/$VARIANT.$SHEET.b_v5s.log" 2>&1
        echo "== $VARIANT/$SHEET b_v6"
        "$PY_B" "$CODE/engine_b/run_engine_b.py" --det-model PP-OCRv6_medium_det --rec-model PP-OCRv6_medium_rec \
            --pages "${PAGES[@]}" --out "$OUT/b_v6" > "$RUN/logs/$VARIANT.$SHEET.b_v6.log" 2>&1
        echo "== $VARIANT/$SHEET c_v5det"
        "$PY_C" "$CODE/engine_c/run_line_htr.py" --model "$TROCR_DIR" --revision "$TROCR_REV" --pages "${PAGES[@]}" \
            --b-dir "$OUT/b_v5s" --out "$OUT/c_v5det" > "$RUN/logs/$VARIANT.$SHEET.c_v5det.log" 2>&1
    done
done
echo "== done: $RUN"
