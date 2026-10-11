# GradeMIND: what it does and does not do

*One page for examiners, students' representatives and anyone who asks. Version for the pilot.*

**What it is.** A tool that helps an examiner grade handwritten answer booklets. The examiner opens a scanned booklet, marks which part of the page
answers which question, and for each question picks a level from a rubric the examiner approved. The system adds up the marks by fixed rules.

**The examiner decides every mark.** The system never proposes a mark, never grades by itself and never changes one. A mark exists only because an
examiner chose a level. If an examiner reopens a finalized result, who did it, when and why are recorded, and the earlier result stays on record.

**What the computer does**
- Shows the scanned pages, lets the examiner draw answer boxes and grade against the rubric, with the keyboard, mouse, finger or stylus.
- Adds up the marks exactly as the exam's rules say (choices, rounding) and shows the total; a finalized result is stored unchanged and can be re-checked.
- Prints a **result sheet** for each student and a **summary** for each exam (PDF, and a spreadsheet file), from the finalized result only.
- Shows a **machine reading** of the handwriting next to the page, so a long answer is easier to skim. It is labelled "can be wrong", it is only a
  convenience, and examiners can hide it. **It is never used to work out a mark.** When an examiner corrects a line, the correction is kept so that
  the reading can be measured (and, later and only with consent, improved).

**What it does not do**
- It does **not** decide, suggest or adjust marks. It does not detect cheating, predict anything or compare students' answers with each other.
- It does **not** read handwriting reliably. How well the machine reads *this college's* handwriting has not been measured yet; the pilot measures it.
  Tamil and Devanagari **handwriting** has not been tested at all. (Result sheets can print Tamil and Devanagari **text**, such as exam names.)
- It does **not** send booklets, marks or corrections to the internet, to any outside company or to any outside AI service. They stay on the college's machine, which is reachable only on the college network or VPN.
- It does **not** replace the college's rules. Appeals, moderation and second marking happen as they do now; the system only records what examiners did.
- The pages are English only. They have been checked automatically for keyboard use, labels and contrast; a person using a screen reader or a real
  tablet and phone has done the walk-through only if the pilot report says so.

**Who can see what.** Examiners see the booklets of the exams they are assigned. Teachers and administrators see their college's exams, results and
reports. Every sign-in, grade, finalize, reopen and report download is written to an audit trail the administrator can read. Passwords are kept only as
one-way hashes, so nobody can read one back; a forgotten password is reset by the administrator, who sees the temporary one once, and it must be changed at the next sign-in.

**Student data.** Booklets are pseudonymous (a reference, not a name). Each booklet has a **data use** choice: *local only* (never leaves the machine) or
*public release* (only with the student's written consent). Corrections of machine reading can be exported only by an administrator on the machine itself,
only according to that choice (`docs/DATASET_EXPORT.md`).

**If something looks wrong** (a mark, a total, a page that is not the student's): stop, do not finalize, and tell the administrator. Nothing is lost by stopping.
