# data/

## Source and approval

| Item | Value |
|---|---|
| Sheets | `sheet_001` (12 pages, Adobe Scan for Android PDF) and `sheet_002` (15 pages, WhatsApp scan PDF): two university Continuous Internal Assessment answer booklets (Environmental Science and Sustainability, CIAT-I), two different students |
| Supplied by | Repository owner, 2026-10-08 |
| Commit approval | Owner decision **D6**, 2026-10-08 (`docs/DECISIONS.md`): sample sheets, transcriptions, run outputs and real-page fixtures may be committed **only in redacted form** |
| Repository visibility | **PUBLIC**; owner decision D20 (2026-10-09): stays public. The institution watermark stays in the redacted images |

## Per-sheet register (D20)

A sheet is committed only if consent for **public** release is recorded as **yes**. Otherwise it stays local (gitignored) and is used only for local benchmarks.

| Sheet | Source | Consent for PUBLIC release | Redaction |
|---|---|---|---|
| sheet_001 | Owner-supplied scan (Adobe Scan PDF), 2026-10-08 | **NOT RECORDED: owner to confirm** (committed earlier under D6, before D20 required this) | Cover page full black box; identifier search + visual contact sheet (see below) |
| sheet_002 | Owner-supplied scan (WhatsApp scan PDF), 2026-10-08 | **NOT RECORDED: owner to confirm** (committed earlier under D6, before D20 required this) | Same as sheet_001 |

## Redaction method

Defined in `data/redaction.json` and applied by `spike/redact.py`:

1. **Cover pages** (`page_01` of each sheet) are covered by a filled black box over the whole page. They hold only identity
   fields (register number, name, course, date), the hall superintendent's name and signature, and an empty marks grid.
2. An identifier search (student names, register numbers, superintendent names) over **all** OCR outputs found hits only on
   cover pages. The pattern list itself is kept local, because it contains the identifiers.
3. In every committed OCR output or fixture for a redacted page, all text fields are replaced with `[REDACTED]`, and
   token ids and log-probabilities are dropped, because token ids decode back to the text.
4. `spike/redact.py --verify` must report **0 hits** before anything is committed.
5. **Unredacted originals stay local only** in `data/originals/` (gitignored), together with the source PDFs.

## Layout

| Path | Contents | Committed? |
|---|---|---|
| `data/originals/<sheet>/` | Unredacted page images and source PDFs | **No** (gitignored) |
| `data/samples/<sheet>/pages/` | Redacted page images | Yes (Git LFS) |
| `data/transcriptions/<sheet>/` | Ground-truth transcriptions + `manifest.json` (status lifecycle in `docs/TRANSCRIPTION_GUIDE.md`) | Yes |
| `data/fixtures/` | Real-page detector regression fixtures (redacted) | Yes |
| `data/golden/` | Future golden set (spec §19) | n/a yet |
| `data/synthetic/` | SYNTHETIC smoke-test sheets only; never counted in metrics | n/a yet |

Large binaries (page images, and model outputs over 1 MB) are stored with **Git LFS** (`.gitattributes`).
