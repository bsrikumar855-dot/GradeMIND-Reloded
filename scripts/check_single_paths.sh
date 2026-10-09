#!/usr/bin/env bash
# Spec rule 3: run before every commit (and in CI). import-linter contracts + the shadow-path scanner.
set -euo pipefail
cd "$(dirname "$0")/.."
a=0; b=0
PYTHONPATH=services/ocr/src uv run --frozen lint-imports || a=$?
python3 scripts/check_single_paths.py || b=$?
echo "check_single_paths: import-linter exit code = $a; scanner exit code = $b"
[ "$a" -eq 0 ] && [ "$b" -eq 0 ]
