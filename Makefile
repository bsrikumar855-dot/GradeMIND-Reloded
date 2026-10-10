# Rule 13: `make test` is the only accepted source of test results for commit messages; CI runs the same target.
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -ec
SPIKE_PYTEST := uvx --from pytest==9.1.1 pytest -c spike/pytest.ini
# D25 c: ScoreComputer, the grading validators and (3.2) the OCR geometry must keep 100% branch coverage; below that, `make test` fails
COV_GATE := --cov=grademind_core.scoring --cov=grademind_core.grading --cov=grademind_core.ocr_geometry --cov-branch --cov-report=term-missing:skip-covered --cov-fail-under=100

.PHONY: test test-spike test-app lint paths smoke ocr-vendor
test:
	@set +e; \
	$(SPIKE_PYTEST) spike/tests -q -rs; a=$$?; \
	uv run --frozen pytest -q -rs $(COV_GATE); b=$$?; \
	echo "make test: spike pytest exit code = $$a; app pytest exit code = $$b"; \
	[ $$a -eq 0 ] && [ $$b -eq 0 ]

test-spike:
	$(SPIKE_PYTEST) spike/tests -q -rs

test-app:
	uv run --frozen pytest -q -rs

lint:
	uv run --frozen ruff check packages apps services scripts tests
	uv run --frozen mypy
	./scripts/check_single_paths.sh

# spec rule 3: import-linter contracts + shadow-path scanner (also part of `make lint`)
paths:
	./scripts/check_single_paths.sh

# end-to-end check of a running `docker compose` stack
smoke:
	./scripts/compose_wait.sh
	python3 scripts/compose_smoke.py

# vendored OCR wheels + weights (needed before building the OCR image; sha256-verified)
ocr-vendor:
	./scripts/fetch_ocr_vendor.sh
