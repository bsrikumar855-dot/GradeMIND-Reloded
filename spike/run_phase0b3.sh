#!/usr/bin/env bash
# Phase 0b-3: conditional-preprocessing variants (D8), Engine C v2 (D9), Engine A base int8 (D10).
# Process rule 9: snapshots spike/ into the run dir and executes the COPY. Rule 12: every runner asserts resolved config.
# Usage: spike/run_phase0b3.sh <phase0b2_run_dir>   (its raw/st010 b_v5s outputs supply Engine C boxes for those variants)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
P0B2="$(cd "$1" && pwd)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_phase0b3"
RUN="$ROOT/spike/runs/$RUN_ID"
mkdir -p "$RUN/logs"
rsync -a --exclude '.venv' --exclude 'runs' --exclude 'pages' --exclude '__pycache__' "$ROOT/spike/" "$RUN/code/"
CODE="$RUN/code"
TROCR_DIR="$HOME/models/trocr-large-handwritten"; TROCR_REV="e68501f437cd2587ae5d68ee457964cac824ddee"
TROCR_SHA="954bf2b50a871bb8e6e90ba0343d64d21055712f3d95d468995ea074481cb837"
UOCR_DIR="$HOME/models/Unlimited-OCR"; UOCR_REV="07dea832e22aefee32ad281d4b80551282e1c168"
UOCR_SHA="2bc48a7a110061ea58fff65d3169367eebe3aee371ca6968dc2219c1b2855fc6"
PY_B="$ROOT/spike/engine_b/.venv/bin/python"; PY_C="$ROOT/spike/engine_c/.venv/bin/python"; PY_A="$ROOT/spike/engine_a/.venv/bin/python"
NEW_VARIANTS=(st011b st011g st011g_rl)

cat > "$RUN/env.json" <<EOF
{"run_id": "$RUN_ID", "git_commit": "$(git -C "$ROOT" rev-parse HEAD)",
 "git_dirty": $([[ -n "$(git -C "$ROOT" status --porcelain)" ]] && echo true || echo false), "code_snapshot": "code/",
 "gpu": "$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)",
 "gpu_used_mib_before": $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits),
 "c_boxes_for_raw_st010_from": "$P0B2", "variants": "$(printf '%s ' "${NEW_VARIANTS[@]}")",
 "trocr": ["$TROCR_REV", "$TROCR_SHA"], "unlimited_ocr": ["$UOCR_REV", "$UOCR_SHA"]}
EOF

pages_dir() {  # variant sheet
    case "$1" in
        raw) echo "$ROOT/data/samples/$2/pages" ;;
        st010) echo "$ROOT/spike/pages/${2}_st010/pages" ;;
        *) echo "$ROOT/spike/pages/${2}_v011/$1/pages" ;;
    esac
}

echo "== 1. characterise + variants"
for SHEET in sheet_001 sheet_002; do
    "$PY_B" "$CODE/preprocess/variants.py" --src "$(pages_dir raw $SHEET)" --dst-root "$ROOT/spike/pages/${SHEET}_v011" \
        > "$RUN/logs/variants.$SHEET.log" 2>&1
    cp "$ROOT/spike/pages/${SHEET}_v011/features.json" "$RUN/features.$SHEET.json"
done

echo "== 2. detection-only timing (raw)"
for SHEET in sheet_001 sheet_002; do
    mapfile -t PAGES < <(ls "$(pages_dir raw $SHEET)"/*.jpg | sort)
    for DET in PP-OCRv5_server_det PP-OCRv6_medium_det; do
        "$PY_B" "$CODE/engine_b/run_engine_b.py" --det-only --det-model "$DET" --rec-model none_det_only \
            --pages "${PAGES[@]}" --out "$RUN/raw/$SHEET/det_only_$DET" > "$RUN/logs/raw.$SHEET.det_only_$DET.log" 2>&1
    done
done

echo "== 3. B configs on new variants"
for V in "${NEW_VARIANTS[@]}"; do for SHEET in sheet_001 sheet_002; do
    mapfile -t PAGES < <(ls "$(pages_dir $V $SHEET)"/*.jpg | sort)
    "$PY_B" "$CODE/engine_b/run_engine_b.py" --det-model PP-OCRv5_server_det --rec-model PP-OCRv5_server_rec \
        --pages "${PAGES[@]}" --out "$RUN/$V/$SHEET/b_v5s" > "$RUN/logs/$V.$SHEET.b_v5s.log" 2>&1
    "$PY_B" "$CODE/engine_b/run_engine_b.py" --det-model PP-OCRv6_medium_det --rec-model PP-OCRv6_medium_rec \
        --pages "${PAGES[@]}" --out "$RUN/$V/$SHEET/b_v6" > "$RUN/logs/$V.$SHEET.b_v6.log" 2>&1
done; done

echo "== 4. Engine C v2 (warp + gate) on b_v5s boxes, all variants; no-gate ablation on raw"
for V in raw st010 "${NEW_VARIANTS[@]}"; do for SHEET in sheet_001 sheet_002; do
    mapfile -t PAGES < <(ls "$(pages_dir $V $SHEET)"/*.jpg | sort)
    if [[ "$V" == raw || "$V" == st010 ]]; then BDIR="$P0B2/$V/$SHEET/b_v5s"; else BDIR="$RUN/$V/$SHEET/b_v5s"; fi
    "$PY_C" "$CODE/engine_c/run_line_htr.py" --model "$TROCR_DIR" --revision "$TROCR_REV" --weights-sha256 "$TROCR_SHA" \
        --pages "${PAGES[@]}" --b-dir "$BDIR" --out "$RUN/$V/$SHEET/c2_v5det" > "$RUN/logs/$V.$SHEET.c2_v5det.log" 2>&1
    if [[ "$V" == raw ]]; then
        "$PY_C" "$CODE/engine_c/run_line_htr.py" --model "$TROCR_DIR" --revision "$TROCR_REV" --weights-sha256 "$TROCR_SHA" \
            --no-gate --pages "${PAGES[@]}" --b-dir "$BDIR" --out "$RUN/$V/$SHEET/c2_v5det_nogate" \
            > "$RUN/logs/$V.$SHEET.c2_v5det_nogate.log" 2>&1
    fi
done; done

echo "== 5. Engine A base int8 (sequential, last)"
for V in raw "${NEW_VARIANTS[@]}"; do for SHEET in sheet_001 sheet_002; do
    mapfile -t PAGES < <(ls "$(pages_dir $V $SHEET)"/*.jpg | sort)
    PYTORCH_ALLOC_CONF=expandable_segments:True "$PY_A" "$CODE/engine_a/run_engine_a.py" --model "$UOCR_DIR" \
        --revision "$UOCR_REV" --weights-sha256 "$UOCR_SHA" --mode base --quant int8 --max-length 4096 \
        --pages "${PAGES[@]}" --out "$RUN/$V/$SHEET/a_base_int8" > "$RUN/logs/$V.$SHEET.a_base_int8.log" 2>&1
done; done
echo "== done: $RUN"
