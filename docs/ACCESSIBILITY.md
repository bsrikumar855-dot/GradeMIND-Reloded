# Accessibility (Phase 4, step 4.5)

What is checked **automatically** on every CI run (`apps/web/e2e/a11y.spec.ts`, `keyboard-flow.spec.ts`, `touch.spec.ts`):

- **axe-core, WCAG 2.0 / 2.1 / 2.2 level A and AA rules**, on the sign-in page, every page around an exam (paper, rubric, booklets, totals, analytics,
  audit, examiners), the users page, and the grading workspace in its different states (page open, drawing on, the machine-line correction editor open,
  the shortcuts help open, finalized, the reopen form), as an administrator and as an examiner, and at phone width (375 px). Any violation fails the build.
- **Keyboard only, the whole grading flow**: sign in, find the exam, open the booklet, draw an answer box, grade it with digits and Ctrl+Enter, confirm the
  unanswered question, finalize, reach the result-sheet link, reopen with a reason. The page records any real mouse or touch press and the test fails if
  there was one.
- **Touch**: a box drawn with a real finger drag (touch events through the browser's debugging protocol, which Chrome turns into pointer events of type
  "touch"); a finger drag that is not drawing creates nothing; a stray tap while drawing creates no zero-size box; controls are at least 44 px tall on a touch
  screen; a sideways finger drag on the page image no longer triggers the browser's Back gesture.
- **Narrow screen**: no page is wider than a 375 px screen; wide tables scroll sideways inside a labelled, keyboard-focusable region.

What the checks found and what was fixed in this step:

- Tables on the exams, booklets, users and examiners pages overflowed a phone screen (the exams page by 10 px): they now scroll inside their card.
- The analytics, audit and totals tables scroll sideways on a narrow screen but were not reachable by keyboard: each is now a labelled, focusable region.
- A sideways finger drag on the page image was handed to the browser, which treated it as its "Back" gesture and left the booklet. The viewer now pans
  sideways itself while not drawing (the browser still scrolls vertically and pinch-zooms), and `overscroll-behavior` is set.
- On touch screens controls were below a comfortable size: coarse-pointer devices now get a 44 px minimum height.

Everything else (all the desktop pages and states) passed on the first run, so the baseline from Phases 1 to 3 (labels, roles, focus rings, a skip link, a
`lang` attribute, light-theme colours chosen for contrast) was already sound.

## Not covered (a listed gap)

- **Screen readers.** Nothing here proves how the pages sound in NVDA, JAWS, VoiceOver or TalkBack: the order things are announced in, whether the
  live regions ("Grade saved.", "Answer area saved.") are announced at a useful moment, whether the page canvas and the machine-reading lines make sense
  when spoken. A person who uses a screen reader should walk the grading flow once before a pilot.
- **Real touch devices.** The touch checks use Chrome's touch emulation on a desktop. A phone or tablet, and a stylus, are untested. Browser taps could not be driven
  reliably through the emulation, so toggle buttons are clicked in that test.
- **Judgements automated rules cannot make**: whether a label is clear, whether instructions are understandable, reading level, and consistent navigation as a
  whole.
- **Languages and scripts**: the interface is English only; the PDF reports print only characters in Windows-1252 (other scripts print as "?").
- **Zoom and text-size changes** beyond the 375 px width check, and high-contrast / forced-colours modes.
