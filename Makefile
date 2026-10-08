# Rule 13: `make test` is the only accepted source of test results for commit messages; CI runs the same target.
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -ec
PYTEST := uvx --from pytest==9.1.1 pytest

.PHONY: test
test:
	@set +e; $(PYTEST) spike/tests -q -rs; code=$$?; echo "make test: pytest exit code = $$code"; exit $$code
