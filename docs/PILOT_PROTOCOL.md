# Pilot protocol

What the pilot is for, what is measured, and what may be said afterwards. Binding decisions: D30 (owner) and `docs/MASTER_PROMPT.md`.
The system helps an examiner grade handwritten answer booklets. **The examiner decides every mark.** Nothing here changes that.

## 1. Before the first real booklet (the owner ticks each line; the RELEASE CHECKLIST in `PHASE_5_REPORT.md` repeats them)

- [ ] Examiner and administrator accounts exist; each person has chosen their own password (first sign-in forces it).
- [ ] The system is reachable only on the college network or VPN, over HTTPS (runbook, "HTTPS").
- [ ] A backup has run and **you** have done the restore drill once (runbook, "Backups"). The key that opens the weekly encrypted copy is stored where you keep it, not on this machine.
- [ ] The walk-through (`docs/PILOT_WALKTHROUGH.md`) is done and signed, or the pilot report says it was NOT done.
- [ ] **Consent (D20):** the upload form's **Data use** choice is set on purpose for every booklet. "Local only" (the default) means the booklet and its corrections never leave this machine. "Public release" is chosen only when the student's written consent for public release is on file. A booklet whose consent is unclear is uploaded as local only, or not used.
- [ ] Examiners have read `docs/WHAT_THIS_SYSTEM_DOES.md`. The machine reading is **visible** and labelled "can be wrong"; each person can hide it, and the choice is kept on their account.

## 2. What is recorded during the pilot (nothing extra to do)

- Every mark an examiner gives (the verdict for each criterion), with who and when, and every finalize or reopen (with the reason).
- Every correction an examiner makes to a machine-read line: the machine's text, the examiner's text, and the picture of the line (kept on this machine; see `docs/DATASET_EXPORT.md`).
- Lines the examiner leaves alone are **not** recorded. This matters for what can be claimed (section 4).

## 3. The measurements: two numbers, kept apart

**3a. Error among corrected lines** (from the corrections table, no extra work). After examiners have corrected lines, the owner (or the person who runs the system) runs, on the machine that hosts it:

```bash
docker compose exec api python -m grademind_core.cli pilot-cer
```

Optional: `--exam <exam id>` (repeat for several exams) to use only some exams; `--limit N` to use the first N corrected lines (the default, 200, is the protocol's). The owner reads the first report (D31.6).

What it does: for each machine-read line that an examiner corrected, it compares the machine's text with the examiner's *final* text, character by character (insert, delete, replace each count 1), and adds up. It reports:

- **n**: how many corrected lines the figure rests on, and how many characters of examiner text;
- the **character error rate** = total edits ÷ total characters of the examiner's text, with a **95% interval** (a resampling over lines; fixed seed, so the same data always gives the same interval);
- the average of each line's own rate, and the share of corrected lines that were off by one character or less;
- how many lines the machine **missed** (the examiner had to add them; there is no rate for these) and how many were **left out** because the examiner marked them unreadable ("[?]").

Rules the script keeps: it **refuses below 50 corrected lines** (and says INTERIM below 200); it **never prints student text** (a test checks that); it only **reads** the corrections.

The number is the error rate **among the lines examiners chose to correct**. A line the machine read correctly leaves no row, so the machine's error rate over *all* lines is **lower**, by an amount this method cannot tell. It is always reported as "error among corrected lines" and never as accuracy. There is **no "mark correct" button** in the examiner flow, by decision (D31.2).

**3b. The real accuracy: the owner's random sample** (per pilot batch, at least 50 lines; this sample is also the D23 benchmark data).

1. `pilot-sample` (runbook, "Measure the machine's real accuracy") draws a random sample, without repeats, of **all the machine lines examiners were shown** in the batch (lines inside an answer box, from the latest reading of each page), whether or not anyone corrected them. The seed is recorded; the same seed on the same data gives the same sample. It needs an administrator, is audited, and writes a folder of cropped lines named `s001.jpg` ... with no booklet, student or line id.
2. **You transcribe each crop** with `scripts/sample_transcriber.py`. It never shows what the machine read. Type exactly what is written, spelling mistakes included; do not correct, do not guess; tick "I cannot read this line" if you cannot.
3. `pilot-sample-cer <folder>` compares the machine's text with yours: the same character error rate, interval and n, **refusing below 50 transcribed lines**, numbers only. Unreadable lines are left out and counted (leaving them out flatters the machine a little: the report says so).
4. Keep the folder on this machine (it is student handwriting). Whether any of it may ever leave follows the booklets' "Data use" choice (D20).

## 4. What may be claimed, and what may not

| May say | May NOT say |
|---|---|
| "On a random sample of N lines of batch B that the machine showed examiners, transcribed by the owner, the machine's text differed from the transcription by X% of characters (95% interval A% to B%)." (3b) | "The system reads handwriting with X% accuracy" in general: the figure holds for these booklets, pens and subjects. |
| "On the first 200 lines examiners corrected, the machine's text differed from theirs by X% (95% interval A% to B%). This is the error among corrected lines." (3a) | The 3a figure as an accuracy, or as the machine's error over all lines. |
| "Examiners corrected N lines in this pilot; the machine missed M lines entirely." | Any figure before n reaches the minimum (50), or 200 for the protocol's 3a figure (interim figures are not quoted outside the pilot team). |
| | That it works on other handwriting, other subjects or other scripts. Tamil and Devanagari handwriting reading has **not** been tested at all (D27; Phase 0c is blocked). |

## 5. During the pilot

- Examiners grade as they always would. If the machine reading gets in the way they hide it; that is allowed and expected.
- Anyone who sees a wrong mark, another person's data, or a result that changed after finalizing **tells the owner at once**. A reopen with a reason is normal and recorded.
- The monthly routines (restore drill, image update; the runbook) are done on their dates and written in the owner's logbook.

## 6. After the pilot

1. Run `verify-snapshots` (runbook): every finalized result must be reproduced exactly. Anything else is reported before any other conclusion is drawn.
2. Run the measurement (section 3); copy the numbers and the caveat **as printed** into the pilot report.
3. Write down: what examiners hid or ignored, what they asked for, what confused them, what failed. Do not summarise these into a score.
4. Decide, with the examiners, whether to continue. No model training, no cloud service and no new grading feature starts from a pilot number alone (D27).
