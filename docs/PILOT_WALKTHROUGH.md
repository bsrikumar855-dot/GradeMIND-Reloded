# Pilot walk-through: screen reader and real devices

**Who this is for:** a person who uses the system as an examiner would, and who is not a developer. Ideally someone who relies on a screen reader
every day, plus one person with a real tablet and a real phone. You do not need to run any command. About 90 minutes in total, in two or three sittings.

**Why it exists.** The automatic checks (`docs/ACCESSIBILITY.md`) prove that the pages have labels, that the keyboard reaches everything, and that a
finger can draw a box in a browser *simulating* a touch screen. They cannot tell whether the pages are *understandable* when read out loud, and no real
phone, tablet or stylus has been tried. This walk-through is that missing check. **Until it has been done and signed at the bottom, the report must say
"screen reader: NOT done; real devices: NOT done".** A person who fails a step has found something real: write it down, do not work around it.

## Before you start (ask whoever runs the system)

- [ ] The address of the system, for example `https://grademind.example:8443`, and that you are on the college network or VPN.
- [ ] The college's root certificate is installed on each device you will use (the runbook, section "HTTPS"). If the browser shows a warning page, stop: that is a setup problem, not a result.
- [ ] A **practice exam** exists with an approved paper and rubric, and one **practice booklet** (a PDF of an invented answer, never a real student's) is uploaded, mapped to nothing yet. Ask for the exam's name.
- [ ] A **practice examiner account** for you (an email and a temporary password). You will be asked to choose your own password at the first sign-in.
- [ ] A second practice exam whose **name is in Tamil or Devanagari** (for part D), for example "தமிழ் தேர்வு" or "हिन्दी परीक्षा", with the same paper and rubric.

How to mark: tick **Pass** if the step worked and made sense *to you*; tick **Problem** if anything was missing, confusing, wrong, or you could not do it
without help. Write what happened in your own words in the notes line: which page, what you did, what you heard or saw. Do not guess at causes.

## A. Screen reader on a computer (about 40 minutes)

Use the reader you know best. Free choices: **NVDA** on Windows (with Chrome or Firefox), **VoiceOver** on a Mac (turn on with Command+F5, Safari).
Write the reader and browser names at the bottom.

| # | What to do | What should happen | Pass | Problem |
|---|---|---|---|---|
| A1 | Open the address. Listen to the page before touching anything. | You hear that this is a sign-in page, and you can find the Email and Password boxes by name. | ☐ | ☐ |
| A2 | Sign in with the temporary password you were given. | You are taken to a page that makes you choose a new password (every new account does). You are told why. | ☐ | ☐ |
| A3 | Choose a new password (at least 12 characters) and save. | You hear that it worked (or the page changes clearly) and you reach the home page. If you made a mistake, the message names the problem. | ☐ | ☐ |
| A4 | Press the first Tab after the page loads. | You are offered "Skip to main content". Press Enter: reading continues in the main part of the page. | ☐ | ☐ |
| A5 | Go to **Exams** and open the practice exam. Find the **Booklets** section. | You can tell the exam sections apart (Paper, Rubric, Booklets and so on) and which one you are on. | ☐ | ☐ |
| A6 | In Booklets, find the practice booklet and its status. Open **Grade**. | The status is spoken in words ("Ready to grade"), not only shown as a colour. The grading page opens. | ☐ | ☐ |
| A7 | On the grading page, listen to the list of pages, the questions, and the running total. | You can tell which page you are on, which question is selected, and the total ("0 / 5" or similar). | ☐ | ☐ |
| A8 | Press **?** (a question mark). | A list of keyboard shortcuts appears and is read. Press **?** again to close it. | ☐ | ☐ |
| A9 | Find the button **Draw answer box** and press Space. Then press Enter on the page, use the arrow keys to move the box (Shift plus arrow changes its size), and press Enter to save. | You hear "Answer area saved." (or equivalent) *without having to go looking for it*. | ☐ | ☐ |
| A10 | Grade the answer: press a number key for a level, then **Ctrl+Enter**. | You hear "Grade saved." and the total changes and is spoken. | ☐ | ☐ |
| A11 | Find the **Machine reading** part. Read what it says at the top. | It says plainly that the machine's reading **can be wrong** and must be checked against the page. | ☐ | ☐ |
| A12 | Find **Hide machine reading** and press it; then **Show machine reading**. Leave it hidden, reload the page. | The button's name changes. After the reload it is still hidden (the choice is kept on your account: sign in on another device and it is still hidden). Show it again before going on. | ☐ | ☐ |
| A13 | If the machine reading shows lines: open **Correct this line** on one, change the text, **Save correction**. | The editor opens with the line's text read out; after saving you hear that it saved. (If there are no lines, write "no lines to try" and tick neither box.) | ☐ | ☐ |
| A14 | Look for the part that says what is still needed before finalizing. | You hear what is missing, in words. A question with no answer box asks for a tick box ("No answer box for ..."). Tick it. | ☐ | ☐ |
| A15 | Press **Finalize result**. | You hear that the result is final, with the total. | ☐ | ☐ |
| A16 | Find **Download result sheet**. Activate it. | A PDF is downloaded or opened (see part D for reading it). | ☐ | ☐ |
| A17 | Find **Reopen** and reopen with a reason. | A box asks "Why is it being reopened"; after saving, the page says the result is open again. | ☐ | ☐ |
| A18 | Open **Account** (top of the page), change your password again, then **Sign out** and sign in with the new one. | Each step is understandable. Signing in with the old password is refused. | ☐ | ☐ |

Notes for part A (what was confusing, what you had to guess, anything not announced):

`_______________________________________________________________________________________________`

## B. A real tablet (about 25 minutes)

Use the tablet examiners would use, with the browser they would use. Do it once with a **finger** and, if you have one, once with a **stylus**.

| # | What to do | What should happen | Pass | Problem |
|---|---|---|---|---|
| B1 | Open the address, sign in, open the practice booklet's grading page. | The page fits the screen; nothing is cut off; buttons are easy to hit without zooming. | ☐ | ☐ |
| B2 | Pinch to zoom the **page image**, then drag it around with one finger. | The image zooms and pans. The browser does **not** go "Back" when you swipe sideways. | ☐ | ☐ |
| B3 | Switch on **Draw answer box**, draw a box around an answer with your finger (or stylus). | The box appears where you drew it and can be saved. A box drawn with a quick flick ends where your finger lifted. | ☐ | ☐ |
| B4 | Touch the page without drawing (a tap, a scroll). | Nothing unwanted is created (no tiny stray box). | ☐ | ☐ |
| B5 | Grade the answer by tapping the level buttons, then save. | The buttons are big enough; the total updates. | ☐ | ☐ |
| B6 | Turn the tablet from portrait to landscape. | The page reorganises and nothing is lost. | ☐ | ☐ |
| B7 | In bright light (a window, or outdoors), read the page and the grade buttons. | Text and buttons are readable. | ☐ | ☐ |

## C. A real phone (about 15 minutes)

| # | What to do | What should happen | Pass | Problem |
|---|---|---|---|---|
| C1 | Open the address on the phone, sign in. | The sign-in page and the exam list fit the width of the screen. | ☐ | ☐ |
| C2 | Open the exam list and the booklets list. | A wide table scrolls sideways *inside its box*; the whole page does not wobble sideways. | ☐ | ☐ |
| C3 | Open the grading page; zoom the page image; draw a box; grade. | It is possible, even if cramped. (Say whether you would do real grading on this phone: yes / no / only for checking.) | ☐ | ☐ |
| C4 | If the phone has **VoiceOver** (iPhone: Settings, Accessibility) or **TalkBack** (Android): repeat A6 to A10 with it. | Swipe navigation reaches every control; the same announcements are heard. | ☐ | ☐ |

## D. The result sheet, in Tamil and Devanagari (about 10 minutes)

| # | What to do | What should happen | Pass | Problem |
|---|---|---|---|---|
| D1 | Finalize a booklet in the **second practice exam** (the one with the Tamil or Devanagari name) and download its **result sheet**. | The exam name and subject on the sheet appear as **real letters**, not as question marks or empty boxes. | ☐ | ☐ |
| D2 | Ask someone who reads Tamil / Hindi to look at the sheet. | They confirm the letters are the right ones and the words are readable (joined letters look right). | ☐ | ☐ |
| D3 | Open the sheet with your screen reader. | The text is read. (It may be read in the wrong language or letter by letter: write down what you hear.) | ☐ | ☐ |
| D4 | Download the **exam summary** PDF for that exam. | The same: real letters, and the file saves with a sensible name. | ☐ | ☐ |

## If something is wrong

- **Stop and tell the owner** if you cannot sign in, if marks look wrong, if anything shows another person's data, or if a warning page appears in the browser. Do not carry on "to see".
- Everything else: finish the walk-through and hand in the page with your notes. Small annoyances are as useful as big failures.

## Sign-off (the owner keeps this page; the pilot report quotes it)

| | |
|---|---|
| Walk-through done by (name) | |
| Date(s) | |
| Computer, screen reader and browser (part A) | |
| Tablet and browser; finger / stylus (part B) | |
| Phone and browser; screen reader on the phone (part C) | |
| Number of steps ticked **Problem** | |
| Problems fixed before the pilot (write them) | |
| Problems accepted for the pilot, and why | |
| **Screen reader testing** | ☐ done by a screen reader user ☐ done by someone else ☐ NOT done |
| **Real tablet / phone testing** | ☐ done ☐ NOT done |
| Owner's signature | |
