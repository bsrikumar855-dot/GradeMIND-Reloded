# Phase 0c: cloud ceiling reference, pre-registered plan (D22)

**This file is committed before any request is sent and before any result is computed.** The git history of this file is the pre-registration
record. Any change after results exist must be a new, dated section that says why. It must not be an edit.

## Purpose

Benchmark only: how well can the strongest available cloud vision models read these handwritten answer pages? This is a **ceiling
reference** for the product decision in D19/D22. Outputs are labelled `CEILING_REFERENCE` and are **never wired into the product path**.

## Gates (nothing is sent before all hold)

1. Both `data/transcriptions/sheet_00{1,2}/manifest.json` have status **`OWNER_VERIFIED`** (D21). The runner checks this and refuses otherwise.
2. The owner has confirmed this in chat, and the runner is started with `--owner-confirmed-in-chat` by the agent only after that confirmation.
3. API credentials are present in the environment. None are stored in the repo.

## Inputs (exactly what is sent)

- The **25 redacted answer pages**: `data/redacted/sheet_001/pages/page_02..12.jpg` (11) and `data/redacted/sheet_002/pages/page_02..15.jpg` (14).
  Cover pages are never sent. The images are the committed, redacted, public files: the same bytes as in this public repo.
- Prompt: `prompts/ocr_ceiling/v1.md` (the text between the markers, verbatim). Its sha256 is logged per request.
- Every request is logged to `runs/<run_id>/ceiling/request_log.jsonl`: provider, requested model, resolved model, parameters, prompt sha256,
  image path and sha256, timestamps. Image bytes are not duplicated into the log.

## Models (resolved at runtime, rule 12)

| Provider | Rule | Parameters |
|---|---|---|
| Anthropic | Top widely released Claude model: **`claude-fable-5-1`**, which must appear in `models.list()` with `capabilities.image_input.supported`. **No silent substitution**: if it is unavailable, the run stops. | `max_tokens=16000`, `output_config.effort="high"`. Thinking is always on for this model (omitted parameter = adaptive). **`temperature` cannot be set**: Claude Fable 5.1 removes sampling parameters, and the SDK `messages.create` has no `temperature` argument. **No refusal fallbacks**: a fallback would answer with a different model (rule 12). |
| Google | Top Gemini vision model: from `models.list()`, names matching `gemini-<major>.<minor>-pro*` with `generateContent` in `supported_actions`, excluding `preview`/`exp`/`latest` aliases; highest `<major>.<minor>` wins. The full candidate list is logged. | `temperature=0`, `seed=20261009`, `max_output_tokens=16000`. Thinking left at the model default; level/budget logged as returned. |

Rule 12: the response's own model identifier (`response.model` / `response.model_version`) must match the resolved model; otherwise the page result
is marked `RULE12_MISMATCH` and excluded. Anthropic: any `fallback_message` entry in `usage.iterations` also counts as a mismatch.

**Determinism deviation from D22 (disclosed before results):** D22 asks for temperature 0. That is applied to Gemini. It is **not possible** for
`claude-fable-5-1`. Run-to-run variance (2 runs per page per model) is therefore reported for both models, and is expected to be larger for Claude.

**Retention (disclosed before results):** neither provider offers a per-request zero-retention switch in these SDK calls.
- `claude-fable-5-1` **requires 30-day data retention** (not available under ZDR without Anthropic authorisation).
- The Gemini API does not use paid-tier API data for training. The owner must use a **paid-tier** key; this is recorded as an owner attestation flag.
- Only redacted pages that are already public in this repo are sent.

## Runs

2 runs per page per model: 25 pages × 2 models × 2 runs = **100 requests**. Each run is independent: no caching and no shared context.
Failures (HTTP errors after SDK retries, refusals, safety blocks, `max_tokens` truncation) are recorded per page, never silently retried beyond the SDK default.

## Scoring (existing tooling; OWNER_VERIFIED only)

- `spike/score.py` and `spike/gate_metrics.py` against the 7 OWNER_VERIFIED GT pages (5 on sheet_001, 2 on sheet_002), using the `text` reader (one output line per line).
- Metrics: CER/WER, omissions, autocorrection candidates, label precision/recall, option letters, spurious digits, with 95% intervals (Wilson for rates, page-level bootstrap for CER).
- Run-to-run variance: normalised edit distance between run 1 and run 2, per page, all 25 pages (needs no GT).
- **Circularity:** any Claude-model metric computed against GT that is not fully OWNER_VERIFIED is labelled **CIRCULAR** and excluded from conclusions. (The gate above should make this impossible; the label is a second line of defence.)
- PP-OCRv6 (`b_v6`, raw) from Phase 0b is reported alongside, on the same GT and the same projection, as the product baseline.

## Pre-registered decision rule

**Line-level CER** = the GT-line-projected CER of `spike/gate_metrics.py` (every model's output word-aligned to the GT lines; edits summed over
GT lines / GT characters), pooled over both sheets and **both runs**. Each page with its two runs is one bootstrap unit: page-level bootstrap, 2000
resamples, seed 20261008, 95% percentile interval.

**Best cloud model** = the model with the lower pooled line-level CER point estimate.

> **If the best cloud model's line-level CER upper 95% bound is ≤ 0.10 on OWNER_VERIFIED GT, AI verdict suggestions become a P1 candidate
> (consented cloud mode + local fine-tuning track). Otherwise D19 stands as the long-term product shape.**

Applies only to OWNER_VERIFIED GT. Results on any other basis cannot trigger the rule.

**Known limitation, stated in advance:** 7 GT pages from 2 students (D23). Even a rule-passing result is a P1 *candidate*. Shipping anything requires the D23 data requirement.
