# Pilot protocol

What the pilot is for, what is measured, and what may be said afterwards. Binding decisions: D30 (owner) and `docs/MASTER_PROMPT.md`.
The system helps an examiner grade handwritten answer booklets. **The examiner decides every mark.** Nothing here changes that.

## 1. Before the first real booklet (the owner ticks each line; the RELEASE CHECKLIST in `PHASE_5_REPORT.md` repeats them)

- [ ] Examiner and administrator accounts exist; each person has chosen their own password (first sign-in forces it).
- [ ] The system is reachable only on the college network or VPN, over HTTPS (runbook, "HTTPS").
- [ ] A backup has run and **you** have done the restore drill once (runbook, "Backups"). The key that opens the weekly encrypted copy is stored where you keep it, not on this machine.
- [ ] The walk-through (`docs/PILOT_WALKTHROUGH.md`) is done and signed, or the pilot report says it was NOT done.
- [ ] **Consent (D20):** the upload form's **Data use** choice is set on purpose for every booklet. "Local only" (the default) means the booklet and its corrections never leave this machine. "Public release" is chosen only when the student's written consent for public release is on file. A booklet whose consent is unclear is uploaded as local only, or not used.
- [ ] Examiners have read `docs/WHAT_THIS_SYSTEM_DOES.md`. The machine reading is **visible** and labelled "can be wrong"; it can be hidden per browser.

## 2. What is recorded during the pilot (nothing extra to do)

- Every mark an examiner gives (the verdict for each criterion), with who and when, and every finalize or reopen (with the reason).
- Every correction an examiner makes to a machine-read line: the machine's text, the examiner's text, and the picture of the line (kept on this machine; see `docs/DATASET_EXPORT.md`).
- Lines the examiner leaves alone are **not** recorded. This matters for what can be claimed (section 4).

## 3. The measurement: error rate of the machine reading on corrected lines

After the examiners have corrected lines in the pilot, the owner (or the person who runs the system) runs, on the machine that hosts it:

```bash
docker compose exec api python -m grademind_core.cli pilot-cer
```

Optional: `--exam <exam id>` (repeat for several exams) to use only some exams; `--limit N` to use the first N corrected lines (the default, 200, is the protocol's).

What it does: for each machine-read line that an examiner corrected, it compares the machine's text with the examiner's *final* text, character by
character (insert, delete, replace each count 1), and adds up. It reports:

- **n**: how many corrected lines the figure rests on, and how many characters of examiner text;
- the **character error rate** = total edits ÷ total characters of the examiner's text, with a **95% interval** (a resampling over lines; fixed seed, so the same data always gives the same interval);
- the average of each line's own rate, and the share of corrected lines that were off by one character or less;
- how many lines the machine **missed** (the examiner had to add them; there is no rate for these) and how many were **left out** because the examiner marked them unreadable ("[?]").

Rules the script keeps:

- **It refuses below 50 corrected lines.** It prints that it refused and why, and no figure. (D30 asks for the first **200**; between 50 and 200 the report says INTERIM.)
- **It never prints student text**: no line, no crop, no student reference, no booklet or examiner id. Only numbers. (A test checks that.)
- It only **reads** the corrections; it writes nothing and cannot change a mark, a result snapshot or a correction.

## 4. What may be claimed, and what may not

The number is the error rate **among the lines examiners chose to correct**. A line the machine read correctly leaves no row, so the machine's error
rate over *all* lines is **lower** than the printed figure, by an amount this method cannot tell. Therefore:

| May say | May NOT say |
|---|---|
| "On the first 200 lines examiners corrected, the machine's text differed from theirs by X% of characters (95% interval A% to B%)." | "The system reads handwriting with X% accuracy." |
| "Examiners corrected N lines in this pilot; the machine missed M lines entirely." | Any accuracy figure before n reaches 200 (an interim figure is labelled interim and not quoted outside the pilot team). |
| "This is measured on these booklets, these examiners, these pens." | That it works on other handwriting, other subjects or other scripts. Tamil and Devanagari handwriting reading has **not** been tested at all (D27; Phase 0c is blocked). |

To measure the machine's error rate over *all* lines you would need examiners to confirm lines they leave unchanged. The product does not do that today;
adding it is a product decision for after the pilot, not part of it.

## 5. During the pilot

- Examiners grade as they always would. If the machine reading gets in the way they hide it; that is allowed and expected.
- Anyone who sees a wrong mark, another person's data, or a result that changed after finalizing **tells the owner at once**. A reopen with a reason is normal and recorded.
- The monthly routines (restore drill, image update; the runbook) are done on their dates and written in the owner's logbook.

## 6. After the pilot

1. Run `verify-snapshots` (runbook): every finalized result must be reproduced exactly. Anything else is reported before any other conclusion is drawn.
2. Run the measurement (section 3); copy the numbers and the caveat **as printed** into the pilot report.
3. Write down: what examiners hid or ignored, what they asked for, what confused them, what failed. Do not summarise these into a score.
4. Decide, with the examiners, whether to continue. No model training, no cloud service and no new grading feature starts from a pilot number alone (D27).
