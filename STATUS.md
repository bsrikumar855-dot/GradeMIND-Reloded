# STATUS

Current phase: **Phase 0b: OCR strategy evaluation** (owner decisions in docs/DECISIONS.md). Phase 0 history follows. The spike runs are done; the gate is waiting on owner transcription verification,
the vLLM FP8 measurement, and owner decisions. See [docs/OCR_SPIKE.md](docs/OCR_SPIKE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

No application code exists yet (spec §23: no scaffolding before the OCR spike is approved). Everything below is **spike tooling** under
`spike/`, not product code.

## Implemented and tested

- `spike/score.py`: CER/WER, critical-token recall, and autocorrect candidates. It refuses unverified ground truth unless `--allow-draft`
  is given (then the output is stamped NOT_REPORTABLE). Self-test: identity gives CER 0, and an injected digit error and a spelling autocorrection are detected (commit `cb3d906`).
- Engine A degenerate-output detector (`degenerate_checks` in `spike/engine_a/run_engine_a.py`). Checked against every degenerate output
  from the spike runs (all flagged) and against the full 27-page run (no new flags on non-degenerate pages after the markup-leak fix). It has no
  unit tests yet; they are planned as Phase 2 regression fixtures.

## Implemented but untested

- `spike/engine_a/run_engine_a.py` modes `int8` and `offload` (exercised on real pages, with no automated tests).
- `spike/engine_b/run_engine_b.py` (exercised on 27 pages, with no automated tests).
- `spike/run_spike.sh` and `spike/summarize.py`.
- `spike/engine_a/run_engine_a_vllm.py` (vLLM client): **Implemented, untested at scale.** It ran 16 pages before the server OOM'd (OCR_SPIKE A7). Kept per D2.

## Not implemented

- Everything in [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md) §3–§20 (application, API, DB, UI, ScoreComputer, invariants I1–I12).
- Reportable OCR metrics (need owner-verified transcriptions).
