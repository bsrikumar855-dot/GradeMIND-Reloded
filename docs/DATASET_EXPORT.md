# Exporting examiner corrections as a labelled dataset (Phase 3.5)

Every correction an examiner makes to a machine-read line is stored as one immutable row (D19). This command turns them into a
dataset for **later, local** benchmarking or fine-tuning. It is **owner-only by construction**: a command-line operation that needs
shell and database access, an active administrator account, and it is recorded in the audit log. There is **no HTTP route** for it
(a test asserts that).

```bash
# inside the api container, or anywhere the app's environment (database + storage) is configured
python -m grademind_core.cli export-corrections --as-admin you@college.edu --scope local --out datasets/2026-10-10
```

| Option | Meaning |
|---|---|
| `--scope local` | **Every** correction, including booklets **without** public-release consent. For local benchmarks and local training only. The manifest says so, and this data **must never leave the machine**. |
| `--scope public` | Only corrections whose booklet has `PUBLIC_RELEASE` consent (D20). The crops still show student handwriting: run the D20 redaction review before publishing anything. |
| `--out DIR` | A **new** directory. Refused if it already has files, and refused if it is inside a git working tree that does not git-ignore it (the repository is public). Use `datasets/` (git-ignored) or a path outside the repository. |
| `--exam ID` | Only one exam. |
| `--include-history` | Every correction, not only the current one of each line (the chain is in `supersedes_id`). |

Exit codes: `0` complete; `2` refused, nothing written; `3` written, but some rows could not be verified and are listed under
`skipped` in `manifest.json`. A pipeline must not treat exit code 3 as a complete dataset.

## What is written

```
DIR/manifest.json       scope, warning, counts per consent scope, subjects, skipped rows, sha256 of corrections.jsonl
DIR/corrections.jsonl   one JSON object per correction (stable key order, UTF-8)
DIR/crops/<sha256>.jpg  the line image, content-addressed
```

Each record: the crop (`image`, `image_sha256`), `ocr_text` (what the machine read), `corrected_text` (the examiner's literal reading,
misspellings kept, `[?]` for illegible parts), `edit_ops` (character-level operations; replaying them on `ocr_text` gives
`corrected_text`), `crop_bbox` and `crop_polygon` (page pixels, so a better crop can be made later), `page_image_sha256`,
`preprocessing_version`, the engine that read it (`ocr.provider`, `model_names`, `weights_sha256`), `subject`, `consent_scope`, and two
pseudonyms: `submission_ref` and `examiner_ref`, stable **within one exam** so a train/test split can keep a booklet or an examiner
together, but not linkable to a person or across exams.

Not exported: student reference (roll number), names, emails, booklet or user ids, page images, marks, verdicts.

## Integrity

Before a row is exported its crop is read back and its sha256 checked, and the edit operations are replayed on the original text. A row
that fails (`crop_missing`, `crop_hash_mismatch`, `edit_ops_do_not_reproduce_the_correction`) is **not** dropped silently: it is listed
in the manifest and the exit code is 3.

## What this does not do

It does not train anything, does not send anything anywhere, and says nothing about how good the OCR is: a handful of corrections from
two booklets is not a benchmark (D23: at least 10 students, 2 subjects and 1 numerical subject are required before any AI-suggestion
feature ships).
