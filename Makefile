# Rule 13: `make test` is the only accepted source of test results for commit messages; CI runs the same target.
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -ec
SPIKE_PYTEST := uvx --from pytest==9.1.1 pytest -c spike/pytest.ini

.PHONY: test test-spike test-app lint
test:
	@set +e; \
	$(SPIKE_PYTEST) spike/tests -q -rs; a=$$?; \
	uv run --frozen pytest -q -rs; b=$$?; \
	echo "make test: spike pytest exit code = $$a; app pytest exit code = $$b"; \
	[ $$a -eq 0 ] && [ $$b -eq 0 ]

test-spike:
	$(SPIKE_PYTEST) spike/tests -q -rs

test-app:
	uv run --frozen pytest -q -rs

lint:
	uv run --frozen ruff check packages apps services scripts tests
	uv run --frozen mypy
