#!/usr/bin/env bash
# Phase 5 release-candidate evidence from the REAL database after the browser E2E over HTTPS: password changes and resets, the rubric guard,
# the frozen snapshots, the Tamil/Devanagari reports, and the database's own refusals. No student text is printed (there is none in these tables'
# output); exam names (the Tamil one) and counts only.
# usage: scripts/phase5_evidence.sh "<psql command prefix>"     e.g. "docker compose exec -T postgres psql -U grademind -d grademind"
set -euo pipefail
PSQL="$1 -v ON_ERROR_STOP=1 -At -F  |  "

echo "--- passwords: the people the E2E created (role | sessions ended so far | still forced to change | has chosen their own):"
$PSQL -c "select role, token_version, must_change_password, password_changed_at is not null from users where email like 'pw.%@example.edu' or email like 'rc.examiner.%@example.edu' order by email"
chosen=$($PSQL -c "select count(*) from users where email like 'rc.examiner.%@example.edu' and not must_change_password and password_changed_at is not null and token_version >= 2")
test "$chosen" -ge 1   # reset (1) + their own change (2)
stuck=$($PSQL -c "select count(*) from users where must_change_password")
test "$stuck" -eq 0    # nobody was left holding a temporary password

echo "--- password events in the audit trail (action, count):"
$PSQL -c "select action, count(*) from audit_logs where action in ('auth.password_changed','user.password_reset','auth.password_change_throttled','auth.login_throttled') group by 1 order by 1"
for need in "auth.password_changed:3" "user.password_reset:2"; do
  act="${need%%:*}"; min="${need##*:}"
  c=$($PSQL -c "select count(*) from audit_logs where action = '$act'")
  test "$c" -ge "$min" || { echo "audit action $act: $c, expected at least $min"; exit 1; }
done
leak=$($PSQL -c "select count(*) from audit_logs where details::text ~* '(password|temporary)[a-z_]*\"?: *\"[^\"]{8,}'")
test "$leak" -eq 0     # no audit entry carries a password value

echo "--- rubric guard: approvals made after grading started (role, new version, grades already given, length of the written reason):"
$PSQL -c "select u.role, a.details->>'version_no', a.details->>'grades_given', length(a.details->>'reason') from audit_logs a join users u on u.id = a.actor_id where a.action = 'rubric.approve' and a.details->>'after_grading' = 'true'"
g=$($PSQL -c "select count(*) from audit_logs a join users u on u.id = a.actor_id where a.action = 'rubric.approve' and a.details->>'after_grading' = 'true' and u.role = 'admin' and length(a.details->>'reason') >= 10")
test "$g" -ge 1
echo "--- finalized results keep the rubric version they were finalized under (exam has a newer version):"
$PSQL -c "select s.student_ref, n.snapshot_no, 'finalized under v' || n.rubric_version_no, 'newest approved v' || (select max(version_no) from rubric_versions r where r.exam_id = s.exam_id and r.status = 'APPROVED') from result_snapshots n join submissions s on s.id = n.submission_id where n.rubric_version_no < (select max(version_no) from rubric_versions r where r.exam_id = s.exam_id and r.status = 'APPROVED') limit 5"
kept=$($PSQL -c "select count(*) from result_snapshots n join submissions s on s.id = n.submission_id where n.rubric_version_no < (select max(version_no) from rubric_versions r where r.exam_id = s.exam_id and r.status = 'APPROVED')")
test "$kept" -ge 1

echo "--- Tamil / Devanagari exam: its reports (action, snapshot, bytes, sha256 prefix, characters that could not be printed):"
$PSQL -c "select a.action, coalesce(a.details->>'snapshot_no','-'), a.details->>'bytes', left(a.details->>'sha256', 12), a.details->>'unprintable_characters' from audit_logs a where a.action in ('report.student_pdf','report.summary_pdf') and (a.details->>'exam_id' in (select id::text from exams where name ~ '[^\\x01-\\x7f]') or a.entity_id in (select id::text from exams where name ~ '[^\\x01-\\x7f]')) order by a.at"
r=$($PSQL -c "select count(*) from audit_logs a where a.action in ('report.student_pdf','report.summary_pdf') and (a.details->>'exam_id' in (select id::text from exams where name ~ '[^\\x01-\\x7f]') or a.entity_id in (select id::text from exams where name ~ '[^\\x01-\\x7f]'))")
test "$r" -ge 2
bad=$($PSQL -c "select count(*) from audit_logs where action in ('report.student_pdf','report.summary_pdf') and details->>'unprintable_characters' = 'true'")
test "$bad" -eq 0

echo "--- the database refuses edits to a finalized result and to its history:"
for stmt in "update result_snapshots set total = 99" "delete from result_snapshots" "update finalization_events set reason = 'x'" "delete from finalization_events"; do
  out=$($PSQL -c "$stmt" 2>&1 || true)
  echo "$out" | grep -qE "append-only|finalized" || { echo "NOT refused: $stmt  -> $out"; exit 1; }
  echo "refused: $stmt"
done
echo "PHASE 5 EVIDENCE OK"
