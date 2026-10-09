# Ground-Truth Transcription Guide

Ground truth for the OCR metrics in spec §19 (CER, WER, and accuracy on numerals, signs and units).
Files live in `data/transcriptions/<sheet_id>/` and are gitignored, because they contain student work.

## Status lifecycle

`manifest.json` → `status`:

| Status | Meaning | Used in metrics? |
|---|---|---|
| `DRAFT_UNVERIFIED` | Pre-filled draft (for example, Claude's visual reading) to save typing | **No** |
| `OWNER_VERIFIED` | A human checked every line against the image and corrected it; `verified_by` and `verified_at` are set | Yes |
| `AGENT_VERIFIED` | Owner override D17: the agent did the line-by-line check (on at least one page). Per-page `verified_by` says who | Yes, **labelled**; autocorrection and silent-error results on it are not evidence |

A draft is a convenience, not ground truth. A machine-drafted transcription can share an OCR engine's
"autocorrect" bias, so the verifier must check each word against the **image**, not just skim the draft.

## Conventions

1. **Literal.** Transcribe what the student wrote, not what they meant. Keep misspellings
   (`enviromental`, `amphiphians`), wrong grammar, and wrong digits.
2. **Intended letterforms.** If a student's `s` looks like `8`, write `s`. Only write `8` if it is the digit 8.
   If you genuinely cannot tell, append `[?]` to the token: `98[?]`.
3. **Line breaks.** One physical handwritten line per text line. Do not join lines that wrap.
4. **Symbols as typed ASCII:** `->` and `=>` for arrows, `*` for bullets, `:-` for colon-dash. Keep the punctuation as written (`90.%`).
5. **Question labels exactly as written** (`11.] a.]`, `13.]`, `19.] A.]`), including stray margin marks (`1'`).
6. **Excluded:** printed text (headers, logos, watermark "POWERING THE YOUTH"), ruled-line artefacts,
   and **bleed-through** (mirrored writing showing through from the other side of the page).
7. **Crossed-out text:** wrap it as `~~text~~`. Keep it, because the system must detect it and exclude it from grading.
8. **Illegible:** use `[illegible]` for a whole word you can't read.

## Scoring notes (for the spike scorer)

- CER/WER are computed after Unicode NFKC normalisation and whitespace collapse. `[?]` markers are stripped
  for CER, and uncertain tokens are reported separately.
- "Critical tokens" are digits, `+ - × ÷ = %`, units, and MCQ option letters (`a)`, `c]`). Their accuracy is reported separately.
