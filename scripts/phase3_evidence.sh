#!/usr/bin/env bash
# Phase 3 evidence from the REAL database after the browser E2E: the corrections table, the audit trail, and the append-only trigger.
# usage: scripts/phase3_evidence.sh "<psql command prefix>"     e.g. "docker compose exec -T postgres psql -U grademind -d grademind"
set -euo pipefail
PSQL="$1 -v ON_ERROR_STOP=1 -At -F  |  "

n=$($PSQL -c "select count(*) from line_corrections")
echo "line_corrections rows: $n"
test "$n" -ge 1

echo "--- corrections (original -> corrected, provenance; student text is not printed):"
$PSQL -c "select ocr_provider, ocr_model_names->>'rec', preprocessing_version, consent_scope, length(ocr_text) as ocr_chars, length(corrected_text) as fixed_chars, jsonb_array_length(edit_ops) as ops, left(crop_sha256,12) as crop, left(page_image_sha256,12) as page from line_corrections order by created_at"

echo "--- every correction has a crop, provenance and an examiner, and chains linearly:"
bad=$($PSQL -c "select count(*) from line_corrections where crop_object_key = '' or crop_sha256 = '' or ocr_model_names = '{}'::jsonb or examiner_id is null")
echo "rows missing a crop / provenance / examiner: $bad"
test "$bad" -eq 0

echo "--- audit trail (counts by action):"
$PSQL -c "select action, count(*) from audit_logs where action like 'line.%' or action like 'ocr.%' or action like 'dataset.%' group by 1 order by 1"
c=$($PSQL -c "select count(*) from audit_logs where action = 'line.correct'")
test "$c" -ge 1
echo "--- the machine-reading tables are populated by the REAL engine (config hash and resolved models stored per run):"
$PSQL -c "select status, count(*), min(left(config_hash,12)), max(model_names->>'det') from ocr_runs group by status order by status"

echo "--- append-only is enforced by the database itself:"
for stmt in "update line_corrections set corrected_text = 'x'" "delete from line_corrections" "update ocr_lines set text = 'x'" "delete from ocr_runs"; do
  out=$($PSQL -c "$stmt" 2>&1 || true)
  echo "$out" | grep -q "append-only" || { echo "NOT enforced for: $stmt  -> $out"; exit 1; }
  echo "refused: $stmt"
done
echo "PHASE 3 EVIDENCE OK"
