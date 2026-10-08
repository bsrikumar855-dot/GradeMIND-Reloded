# PHASE 0B REPORT: OCR strategy evaluation

**Status: STOP. The engine-role decision is NOT made yet.** Every accuracy-dependent table (T5–T11) is
`PENDING_VERIFICATION`, because the 7 transcription pages are not yet `OWNER_VERIFIED` (verifier: `python3 spike/verify_ui/serve.py`).
Once they are, one command regenerates every table, including the gate metrics the recommendation must rest on (D11):

```bash
python3 spike/report_0b.py
```

The prose below is hand-written. **Every number in a table comes from the generated block** (`spike/report_0b.py` → run outputs;
spec rule 1). The prose only cites table IDs and figures copied from them.

## 1. What was run

| Run | Content | Code |
|---|---|---|
| `20261008T083940Z_phase0b` | First Phase 0b run. **Confounded**: naming only the recogniser made PaddleOCR 3.7 silently load `PP-OCRv6_medium_det` (labelled `b_v6det_v5rec` / `c1_v6det`). This led to rule 12. | `b1b73e9` |
| `20261008T091415Z_phase0b2` | Controlled rerun, every det/rec named explicitly: `b_v5s`, `b_v6`, TrOCR v1 on v5 boxes. 12/12 steps, 0 errors; logs show exactly the 4 requested models loaded. | `cd93d80` |
| `20261008T105442Z_phase0b3` | Final batch: page features, variants `st011b`/`st011g`/`st011g_rl`, detection-only timing, Engine C v2 (perspective warp + empty-crop gate, plus a no-gate ablation), Engine A base int8 on raw + 3 variants. **38/38 steps, 0 errors, 0 rule-12 violations.** | `fd9af19` (code snapshot in run dir, rule 9) |

All runs read the unredacted originals locally. Redacted exports are committed under `data/run_outputs/`, and redacted page images under `data/redacted/` (LFS).

## 2. Findings that do NOT depend on verified ground truth

**F1. Budget and cost (T1, T2).**
- Engine A base int8 peaks at **4361 MiB** on every variant, so it is in the ≤ 6 GB budget (D3, D10); no page is `OVER_BUDGET`.
- Engine C (TrOCR) peaks at about **2.7 GB** VRAM and takes about **1.6 s/page** for crop plus recognition.
- **On CPU, detection dominates Engine B and Engine C:** `PP-OCRv5_server_det` takes **18.3–19.9 s/page**, while `PP-OCRv6_medium_det` takes **7.5–8.2 s/page**.
- The v5 server pipelines peak at **15.8–17.2 GB RSS**, against **3.2–3.3 GB for v6**. The 16 GB footprint comes from the v5 *detector*.
- So Engine C end-to-end on v5 boxes takes about **20–22 s/page**, almost all of it detection.

**F2. Engine A is still unreliable even in budget (T1).** Base int8 produced degenerate output on **4 of 11** answer pages of sheet_001
and **3 of 14** of sheet_002 (raw). On sheet_001, every suppression variant cuts that to **1 of 11**; on sheet_002 the variants give 2–4 of 14.
The detector caught them, but that is a backstop, not a gate (rule 10).

**F3. Preprocessing must be conditional (T3 = PROXY_NON_NUMERICAL_SHEETS_ONLY, T4).**
- On sheet_001 (show-through, Adobe Scan), every suppression variant sharply reduces out-of-label digit tokens for `b_v5s` (104 → 23–36).
- On sheet_002 (already near-binarised: extreme-pixel share ≈ 0.88–0.92, against ≈ 0 on sheet_001), every variant **increases** detected rows and digits.
- **Every `PAGE_DETECTION_ANOMALY` is on sheet_002, almost all on preprocessed variants.**
- These are proxies, not accuracy. The selection rule itself (T10) needs verified GT, and its cross-sheet validation is **WEAK (n = 2 sheets)** by construction.

**F4. Detectors differ in what they call "text" (T3).** On raw sheet_001, `b_v6` returns 346 non-empty lines (11 below score 0.6) against 436 for
`b_v5s` (124 below 0.6), and 31 against 104 out-of-label digits. Whether v6 is *dropping show-through* or *dropping real faint writing* can only be told from T8 (line-quality) after verification.

**F5. The empty-crop gate is aggressive on sheet_001 (T1: 122 `NO_TEXT` lines on sheet_001 vs 21 on sheet_002).** Its threshold (ink fraction 0.04) was set by inspecting
**one** page, sheet_001/page_03, which is a GT page. T9's sheet_001 rows are therefore **in-sample**, and only sheet_002 is out-of-sample.

## 3. Engineering and process changes in this phase

- **Rule 12 (resolved config):** all runners read back what the library actually loaded and fail on mismatch. Wrong weight hashes fail Engine A and Engine C with exit 1 and no output (smoke tests, commit `fd9af19`). `b_v5s`/`b_v6` raw and st010 outputs predate the rule-12 code (T1: "no (pre-rule-12 run)"); their logs show the requested models were the ones loaded (§1).
- **Rule 13 (`make test`):** 25 passed, 4 skipped, exit 0 (commit `1411815`). It exits non-zero on a planted failure. **The CI workflow is written but not pushed**: GitHub refused it because the `gh` token lacks the `workflow` scope. It waits on local branch `ci-pending`.
- **Detector (rule 10):** 8 rules have real-page fixtures and 4 have SYNTHETIC fixtures, which are reported as "no real-page fixture yet". There are 0 false positives on 31 reviewed-clean pages.
- **Bug found and fixed (variants 0.1.2):** the ruled-line feature counted fragments (33–107 "lines"/page), which also disabled `PAGE_DETECTION_ANOMALY`. It now gives 25/page on sheet_001, and undercounts curved sheet_002 pages (13–26). It was tuned on 6 pages against a visual count. The features were recomputed to `features_v012.*.json`; variant images are unaffected.
- **Known feature caveat:** `show_through_est` also counts faint ruled lines as rejected ink (a near-blank ruled page scores 0.81), so it is not a clean show-through measure.
- **Privacy:** history audit (`docs/PRIVACY_AUDIT.md`): no student data or identifiers ever pushed. The redacted data commit passed a visual review of all 27 pages, `redact.py --verify` (0 hits in 1082 files), and a staged-content grep (0 hits).

## 4. Recommendation

**None yet. No engine-role recommendation can be made on this evidence.** D11 requires gate metrics against OWNER_VERIFIED ground truth, and
D16 forbids resting on NOT_DISTINGUISHABLE differences. Every accuracy table is pending. What the verified-independent evidence already constrains:

1. **Engine A (Unlimited-OCR) stays demoted** (D2). Even in budget, 7 of 25 raw answer pages degenerate (F2). Whether it adds value *as a cross-check* is exactly T6's gate recall for the pairs containing `a_base_int8`.
2. **Detection, not recognition, is the CPU cost and RAM driver** (F1). If T5/T8 show `PP-OCRv6_medium_det` is not worse on line recall, it is the operational default: 2.4× faster and about 5× less RAM.
3. **Preprocessing will be conditional on a binarisation feature** (F3). The proxy split is stark, but a selection rule validated on 2 sheets is WEAK.

### What additional data would change or settle this

| Data | What it would settle |
|---|---|
| **The 7 transcription pages OWNER_VERIFIED** | Every accuracy table (T5–T11). That allows a first recommendation, with intervals. |
| **More answer sheets: ≥ 5 more writers, both scan apps** | Turns NOT_DISTINGUISHABLE comparisons into decisions, and moves the preprocessing rule from WEAK to a real validation. With 7 GT pages, page-level intervals will be wide. |
| **More transcribed pages per sheet** (≥ 15 GT pages total) | Narrows the CER bootstrap intervals and gives the gate metrics enough "any engine wrong" lines for a meaningful gate recall. |
| **A Maths/Physics sheet** | Numeral/sign/unit accuracy is **UNTESTED** (D5). It also invalidates the T3 proxy. |
| **sheet_002 gate-ablation results** (T9, out-of-sample) | Whether the 0.04 ink-fraction gate generalises or must be re-derived. |
| **Owner decision on the institution watermark** | Whether the committed redacted images need further masking. |

## 5. Generated tables

<!-- BEGIN GENERATED -->
_Generated by `spike/report_0b.py` from run outputs; do not edit by hand._  
Ground truth status: sheet_001=DRAFT_UNVERIFIED, sheet_002=DRAFT_UNVERIFIED  
Numeral / sign / unit accuracy: **UNTESTED** (owner decision D5).  
Cells: `value [95% interval] (n=...)`; Wilson intervals for rates, page-level bootstrap (2000 resamples, fixed seed) for CER/WER. Comparisons whose intervals overlap are **NOT_DISTINGUISHABLE** (D16).

### T0. Engine configurations (true, not requested; rule 12)

| label | configuration |
|---|---|
| b_v5m | PP-OCRv5_server_det + en_PP-OCRv5_mobile_rec |
| b_v6det_v5rec | PP-OCRv6_medium_det (IMPLICIT, confounded run) + PP-OCRv5_server_rec |
| b_v5s | PP-OCRv5_server_det + PP-OCRv5_server_rec |
| b_v6 | PP-OCRv6_medium_det + PP-OCRv6_medium_rec |
| c1_v6det | TrOCR v1 runner (axis-aligned crops, no gate) on PP-OCRv6_medium_det boxes |
| c1_v5det | TrOCR v1 runner (axis-aligned crops, no gate) on PP-OCRv5_server_det boxes |
| c2_v5det | TrOCR v2 runner (perspective warp + empty-crop gate) on PP-OCRv5_server_det boxes |
| c2_v5det_nogate | TrOCR v2 runner, gate disabled (ablation) |
| a_gundam_int8_ref | Unlimited-OCR gundam int8 (Phase 0; OVER the 6 GB budget) |
| a_base_int8 | Unlimited-OCR base int8 (D10) |

### T1. Operations (answer pages; cover pages excluded)

| engine | variant | sheet | pages | median s/page | C crop s | C rec s | peak RAM MiB | peak VRAM MiB | pages > 6 GB (OVER_BUDGET) | NO_TEXT lines | TRUNCATED lines | A degenerate pages | resolved config recorded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| a_base_int8 | raw | sheet_001 | 11 | 7.64 |  |  |  | 4361 |  |  |  | 4 | yes |
| a_base_int8 | raw | sheet_002 | 14 | 8.33 |  |  |  | 4361 |  |  |  | 3 | yes |
| a_base_int8 | st011b | sheet_001 | 11 | 9.57 |  |  |  | 4361 |  |  |  | 1 | yes |
| a_base_int8 | st011b | sheet_002 | 14 | 8.86 |  |  |  | 4361 |  |  |  | 4 | yes |
| a_base_int8 | st011g | sheet_001 | 11 | 8.64 |  |  |  | 4361 |  |  |  | 1 | yes |
| a_base_int8 | st011g | sheet_002 | 14 | 9.08 |  |  |  | 4361 |  |  |  | 2 | yes |
| a_base_int8 | st011g_rl | sheet_001 | 11 | 9.39 |  |  |  | 4361 |  |  |  | 1 | yes |
| a_base_int8 | st011g_rl | sheet_002 | 14 | 8.8 |  |  |  | 4361 |  |  |  | 3 | yes |
| a_gundam_int8_ref | raw | sheet_001 | 11 | 8.29 |  |  |  | 6153 | 9 |  |  | 1 | no (pre-rule-12 run) |
| a_gundam_int8_ref | raw | sheet_002 | 14 | 9.36 |  |  |  | 6648 | 2 |  |  | 0 | no (pre-rule-12 run) |
| b_v5m | raw | sheet_001 | 11 | 21.02 |  |  | 15748 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5m | raw | sheet_002 | 14 | 23.13 |  |  | 16963 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5m | st010 | sheet_001 | 11 | 20.18 |  |  | 15759 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5m | st010 | sheet_002 | 14 | 24.29 |  |  | 16965 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5s | raw | sheet_001 | 11 | 20.19 |  |  | 15875 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5s | raw | sheet_002 | 14 | 22.83 |  |  | 17156 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5s | st010 | sheet_001 | 11 | 20.13 |  |  | 15906 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5s | st010 | sheet_002 | 14 | 23.85 |  |  | 17125 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v5s | st011b | sheet_001 | 11 | 19.94 |  |  | 15906 |  |  |  |  |  | yes |
| b_v5s | st011b | sheet_002 | 14 | 23.67 |  |  | 17111 |  |  |  |  |  | yes |
| b_v5s | st011g | sheet_001 | 11 | 19.79 |  |  | 15909 |  |  |  |  |  | yes |
| b_v5s | st011g | sheet_002 | 14 | 22.93 |  |  | 17131 |  |  |  |  |  | yes |
| b_v5s | st011g_rl | sheet_001 | 11 | 19.79 |  |  | 15904 |  |  |  |  |  | yes |
| b_v5s | st011g_rl | sheet_002 | 14 | 22.45 |  |  | 17112 |  |  |  |  |  | yes |
| b_v6 | raw | sheet_001 | 11 | 10.98 |  |  | 3213 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6 | raw | sheet_002 | 14 | 11.79 |  |  | 3296 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6 | st010 | sheet_001 | 11 | 9.65 |  |  | 3167 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6 | st010 | sheet_002 | 14 | 11.87 |  |  | 3336 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6 | st011b | sheet_001 | 11 | 9.95 |  |  | 3191 |  |  |  |  |  | yes |
| b_v6 | st011b | sheet_002 | 14 | 12.71 |  |  | 3390 |  |  |  |  |  | yes |
| b_v6 | st011g | sheet_001 | 11 | 10.15 |  |  | 3163 |  |  |  |  |  | yes |
| b_v6 | st011g | sheet_002 | 14 | 11.85 |  |  | 3318 |  |  |  |  |  | yes |
| b_v6 | st011g_rl | sheet_001 | 11 | 10.32 |  |  | 3167 |  |  |  |  |  | yes |
| b_v6 | st011g_rl | sheet_002 | 14 | 11.38 |  |  | 3295 |  |  |  |  |  | yes |
| b_v6det_v5rec | raw | sheet_001 | 11 | 9.43 |  |  | 3259 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6det_v5rec | raw | sheet_002 | 14 | 10.43 |  |  | 3297 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6det_v5rec | st010 | sheet_001 | 11 | 8.5 |  |  | 3200 |  |  |  |  |  | no (pre-rule-12 run) |
| b_v6det_v5rec | st010 | sheet_002 | 14 | 10.42 |  |  | 3328 |  |  |  |  |  | no (pre-rule-12 run) |
| c1_v5det | raw | sheet_001 | 11 | 2.19 |  |  | 2965 | 2713 |  |  |  |  | no (pre-rule-12 run) |
| c1_v5det | raw | sheet_002 | 14 | 1.62 |  |  | 2966 | 2761 |  |  |  |  | no (pre-rule-12 run) |
| c1_v5det | st010 | sheet_001 | 11 | 1.82 |  |  | 2965 | 2745 |  |  |  |  | no (pre-rule-12 run) |
| c1_v5det | st010 | sheet_002 | 14 | 2.55 |  |  | 2965 | 2721 |  |  |  |  | no (pre-rule-12 run) |
| c1_v6det | raw | sheet_001 | 11 | 1.76 |  |  | 2966 | 2709 |  |  |  |  | no (pre-rule-12 run) |
| c1_v6det | raw | sheet_002 | 14 | 1.16 |  |  | 2962 | 2720 |  |  |  |  | no (pre-rule-12 run) |
| c1_v6det | st010 | sheet_001 | 11 | 1.1 |  |  | 2965 | 2691 |  |  |  |  | no (pre-rule-12 run) |
| c1_v6det | st010 | sheet_002 | 14 | 1.2 |  |  | 2964 | 2705 |  |  |  |  | no (pre-rule-12 run) |
| c2_v5det | raw | sheet_001 | 11 | 1.61 | 0.14 | 1.45 | 2968 | 2716 |  | 122 |  |  | yes |
| c2_v5det | raw | sheet_002 | 14 | 1.66 | 0.12 | 1.55 | 2966 | 2740 |  | 21 |  |  | yes |
| c2_v5det | st010 | sheet_001 | 11 | 1.88 | 0.1 | 1.76 | 2968 | 2751 |  | 3 |  |  | yes |
| c2_v5det | st010 | sheet_002 | 14 | 2.52 | 0.11 | 2.4 | 2968 | 2738 |  | 39 |  |  | yes |
| c2_v5det | st011b | sheet_001 | 11 | 2.2 | 0.1 | 2.08 | 2968 | 2725 |  | 1 |  |  | yes |
| c2_v5det | st011b | sheet_002 | 14 | 2.77 | 0.11 | 2.65 | 2969 | 2715 |  | 37 |  |  | yes |
| c2_v5det | st011g | sheet_001 | 11 | 2.07 | 0.11 | 1.95 | 2968 | 2705 |  | 2 |  |  | yes |
| c2_v5det | st011g | sheet_002 | 14 | 1.96 | 0.13 | 1.85 | 2966 | 2743 |  | 20 |  |  | yes |
| c2_v5det | st011g_rl | sheet_001 | 11 | 2.12 | 0.11 | 2.0 | 2968 | 2705 |  | 2 |  |  | yes |
| c2_v5det | st011g_rl | sheet_002 | 14 | 2.28 | 0.12 | 2.13 | 2968 | 2719 |  | 5 |  |  | yes |
| c2_v5det_nogate | raw | sheet_001 | 11 | 2.43 | 0.14 | 2.27 | 2966 | 2716 |  |  |  |  | yes |
| c2_v5det_nogate | raw | sheet_002 | 14 | 1.76 | 0.12 | 1.64 | 2966 | 2746 |  |  |  |  | yes |

### T2. Detection-only and Engine C end-to-end timing (raw, median s/page)

| sheet | detector | detection s | C crop+recognition s | C end-to-end s |
|---|---|---|---|---|
| sheet_001 | PP-OCRv5_server_det | 18.34 | 1.61 | 19.95 |
| sheet_001 | PP-OCRv6_medium_det | 7.45 |  |  |
| sheet_002 | PP-OCRv5_server_det | 19.94 | 1.66 | 21.6 |
| sheet_002 | PP-OCRv6_medium_det | 8.23 |  |  |

### T3. GT-free proxies: **PROXY_NON_NUMERICAL_SHEETS_ONLY** (invalid once a numerical-subject sheet is added)

| engine | variant | sheet | non-empty lines | lines score<0.6 | non-label digit tokens (PROXY_NON_NUMERICAL_SHEETS_ONLY) |
|---|---|---|---|---|---|
| b_v5m | raw | sheet_001 | 442 | 119 | 48 |
| b_v5m | raw | sheet_002 | 341 | 50 | 25 |
| b_v5m | st010 | sheet_001 | 403 | 29 | 18 |
| b_v5m | st010 | sheet_002 | 578 | 262 | 58 |
| b_v5s | raw | sheet_001 | 436 | 124 | 104 |
| b_v5s | raw | sheet_002 | 332 | 65 | 8 |
| b_v5s | st010 | sheet_001 | 404 | 76 | 24 |
| b_v5s | st010 | sheet_002 | 552 | 317 | 29 |
| b_v5s | st011b | sheet_001 | 438 | 84 | 36 |
| b_v5s | st011b | sheet_002 | 617 | 425 | 52 |
| b_v5s | st011g | sheet_001 | 417 | 51 | 23 |
| b_v5s | st011g | sheet_002 | 419 | 172 | 25 |
| b_v5s | st011g_rl | sheet_001 | 425 | 59 | 25 |
| b_v5s | st011g_rl | sheet_002 | 481 | 162 | 33 |
| b_v6 | raw | sheet_001 | 346 | 11 | 31 |
| b_v6 | raw | sheet_002 | 248 | 7 | 11 |
| b_v6 | st010 | sheet_001 | 225 | 5 | 12 |
| b_v6 | st010 | sheet_002 | 263 | 29 | 12 |
| b_v6 | st011b | sheet_001 | 234 | 3 | 10 |
| b_v6 | st011b | sheet_002 | 265 | 33 | 23 |
| b_v6 | st011g | sheet_001 | 273 | 3 | 17 |
| b_v6 | st011g | sheet_002 | 253 | 13 | 10 |
| b_v6 | st011g_rl | sheet_001 | 280 | 3 | 12 |
| b_v6 | st011g_rl | sheet_002 | 271 | 12 | 11 |
| b_v6det_v5rec | raw | sheet_001 | 346 | 60 | 66 |
| b_v6det_v5rec | raw | sheet_002 | 248 | 21 | 6 |
| b_v6det_v5rec | st010 | sheet_001 | 224 | 23 | 11 |
| b_v6det_v5rec | st010 | sheet_002 | 255 | 71 | 5 |
| c1_v5det | raw | sheet_001 | 447 | 114 | 26 |
| c1_v5det | raw | sheet_002 | 368 | 87 | 21 |
| c1_v5det | st010 | sheet_001 | 407 | 25 | 13 |
| c1_v5det | st010 | sheet_002 | 730 | 227 | 126 |
| c1_v6det | raw | sheet_001 | 346 | 64 | 13 |
| c1_v6det | raw | sheet_002 | 249 | 26 | 6 |
| c1_v6det | st010 | sheet_001 | 225 | 9 | 7 |
| c1_v6det | st010 | sheet_002 | 275 | 42 | 11 |
| c2_v5det | raw | sheet_001 | 325 | 55 | 10 |
| c2_v5det | raw | sheet_002 | 347 | 59 | 10 |
| c2_v5det | st010 | sheet_001 | 404 | 29 | 13 |
| c2_v5det | st010 | sheet_002 | 691 | 206 | 109 |
| c2_v5det | st011b | sheet_001 | 440 | 31 | 14 |
| c2_v5det | st011b | sheet_002 | 767 | 271 | 112 |
| c2_v5det | st011g | sheet_001 | 416 | 7 | 14 |
| c2_v5det | st011g | sheet_002 | 516 | 161 | 62 |
| c2_v5det | st011g_rl | sheet_001 | 426 | 15 | 15 |
| c2_v5det | st011g_rl | sheet_002 | 517 | 114 | 43 |
| c2_v5det_nogate | raw | sheet_001 | 447 | 122 | 24 |
| c2_v5det_nogate | raw | sheet_002 | 368 | 76 | 13 |

### T4. Page characterisation features (D8)

| sheet | page | extreme-pixel share | grey levels | show-through est. | ruled lines | ruled spacing px | JPEG blockiness | MP |
|---|---|---|---|---|---|---|---|---|
| sheet_001 | page_01 | 0.0001 | 94 | 0.0473 | 31 | 85.0 | 1.801 | 9.42 |
| sheet_001 | page_02 | 0.0 | 102 | 0.49 | 25 | 125.25 | 2.145 | 8.97 |
| sheet_001 | page_03 | 0.0 | 102 | 0.4889 | 25 | 128.75 | 2.095 | 9.42 |
| sheet_001 | page_04 | 0.0004 | 95 | 0.5213 | 25 | 122.5 | 1.877 | 8.52 |
| sheet_001 | page_05 | 0.0001 | 101 | 0.39 | 26 | 115.5 | 1.807 | 8.26 |
| sheet_001 | page_06 | 0.0004 | 112 | 0.2423 | 25 | 126.75 | 1.812 | 8.98 |
| sheet_001 | page_07 | 0.0017 | 102 | 0.4095 | 25 | 128.25 | 1.922 | 8.83 |
| sheet_001 | page_08 | 0.0005 | 112 | 0.2106 | 25 | 128.25 | 1.923 | 9.42 |
| sheet_001 | page_09 | 0.0001 | 107 | 0.3628 | 25 | 129.25 | 1.905 | 8.83 |
| sheet_001 | page_10 | 0.0006 | 112 | 0.4652 | 25 | 129.75 | 1.933 | 8.83 |
| sheet_001 | page_11 | 0.0002 | 113 | 0.4269 | 25 | 119.25 | 2.008 | 8.56 |
| sheet_001 | page_12 | 0.0 | 108 | 0.8102 | 25 | 110.0 | 2.423 | 7.74 |
| sheet_002 | page_01 | 0.8795 | 62 | 0.2545 | 30 | 66.0 | 1.039 | 5.98 |
| sheet_002 | page_02 | 0.9238 | 27 | 0.2606 | 21 | 104.5 | 1.104 | 6.72 |
| sheet_002 | page_03 | 0.9155 | 43 | 0.1688 | 21 | 137.5 | 1.129 | 10.1 |
| sheet_002 | page_04 | 0.8908 | 60 | 0.3417 | 26 | 136.5 | 1.056 | 10.28 |
| sheet_002 | page_05 | 0.9114 | 30 | 0.1392 | 18 | 103.5 | 1.06 | 7.15 |
| sheet_002 | page_06 | 0.9212 | 35 | 0.0963 | 26 | 114.0 | 1.061 | 8.17 |
| sheet_002 | page_07 | 0.9241 | 22 | 0.1708 | 25 | 118.75 | 1.122 | 9.69 |
| sheet_002 | page_08 | 0.9177 | 21 | 0.1475 | 25 | 121.5 | 1.082 | 9.52 |
| sheet_002 | page_09 | 0.9178 | 30 | 0.2333 | 13 | 110.5 | 1.069 | 7.89 |
| sheet_002 | page_10 | 0.9048 | 21 | 0.1707 | 25 | 116.0 | 1.084 | 8.42 |
| sheet_002 | page_11 | 0.9117 | 22 | 0.1986 | 25 | 142.0 | 1.088 | 9.58 |
| sheet_002 | page_12 | 0.9101 | 46 | 0.3469 | 21 | 123.25 | 1.091 | 9.74 |
| sheet_002 | page_13 | 0.9143 | 21 | 0.1839 | 25 | 119.5 | 1.067 | 9.41 |
| sheet_002 | page_14 | 0.9128 | 20 | 0.0957 | 26 | 137.5 | 1.069 | 11.36 |
| sheet_002 | page_15 | 0.9168 | 21 | 0.172 | 19 | 148.0 | 1.106 | 8.53 |

**PAGE_DETECTION_ANOMALY** (detected text rows > 1.5 × ruled lines):

| sheet | page | variant | engine | detected rows | ruled lines |
|---|---|---|---|---|---|
| sheet_002 | page_02 | raw | b_v5m | 35 | 21 |
| sheet_002 | page_02 | st010 | b_v5m | 47 | 21 |
| sheet_002 | page_02 | raw | b_v5s | 35 | 21 |
| sheet_002 | page_02 | st010 | b_v5s | 47 | 21 |
| sheet_002 | page_02 | st011b | b_v5s | 46 | 21 |
| sheet_002 | page_02 | st011g | b_v5s | 39 | 21 |
| sheet_002 | page_04 | st010 | b_v5m | 61 | 26 |
| sheet_002 | page_04 | st010 | b_v5s | 61 | 26 |
| sheet_002 | page_04 | st011b | b_v5s | 77 | 26 |
| sheet_002 | page_04 | st011g | b_v5s | 48 | 26 |
| sheet_002 | page_09 | st010 | b_v5m | 28 | 13 |
| sheet_002 | page_09 | st010 | b_v6det_v5rec | 20 | 13 |
| sheet_002 | page_09 | st010 | b_v5s | 28 | 13 |
| sheet_002 | page_09 | st011b | b_v5s | 28 | 13 |
| sheet_002 | page_09 | st011g | b_v5s | 26 | 13 |
| sheet_002 | page_09 | st011g_rl | b_v5s | 23 | 13 |
| sheet_002 | page_09 | st010 | b_v6 | 20 | 13 |
| sheet_002 | page_09 | st011b | b_v6 | 23 | 13 |
| sheet_002 | page_09 | st011g | b_v6 | 20 | 13 |
| sheet_002 | page_12 | st010 | b_v5m | 35 | 21 |
| sheet_002 | page_12 | st010 | b_v5s | 35 | 21 |
| sheet_002 | page_12 | st011b | b_v5s | 33 | 21 |
| sheet_002 | page_14 | st011b | b_v5s | 40 | 26 |
| sheet_002 | page_15 | st011b | b_v5s | 33 | 19 |

### T12. Error rate of the agent's draft transcriptions (owner edits in the verifier)

| sheet | status | draft lines | changed | deleted | missing (inserted by owner) | draft lines changed or deleted |
|---|---|---|---|---|---|---|
| sheet_001 | DRAFT_UNVERIFIED | 79 | 1 | 0 | 0 | 0.013 [0.002, 0.068] (n=79) |

### T5. Accuracy per engine / variant / sheet

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

### T6. Gate metrics (D11)

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

### T7. Oracle vs best single engine; shared autocorrections

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

### T8. Line-detection quality (D9)

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

### T9. Empty-crop gate ablation (D9)

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

### T10. Best variant per page and selection rule (D8)

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

### T11. Pairwise comparisons (D16)

**PENDING_VERIFICATION**: transcriptions are not OWNER_VERIFIED; these tables are generated only from verified ground truth.

<!-- END GENERATED -->
