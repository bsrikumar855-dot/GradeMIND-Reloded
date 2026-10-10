#!/usr/bin/env bash
# Phase 4 evidence from the REAL database after the browser E2E: finalize / reopen history, frozen snapshots, the audit counts, and the
# database's own refusals (append-only snapshots and events; no edits to a finalized result). Student text is not printed.
# usage: scripts/phase4_evidence.sh "<psql command prefix>"     e.g. "docker compose exec -T postgres psql -U grademind -d grademind"
set -euo pipefail
PSQL="$1 -v ON_ERROR_STOP=1 -At -F  |  "

echo "--- finalize / reopen history per booklet (booklets that were ever reopened):"
$PSQL -c "select s.student_ref, string_agg(e.action || ' (snapshot ' || n.snapshot_no || ')', ' -> ' order by e.seq) from finalization_events e join submissions s on s.id = e.submission_id join result_snapshots n on n.id = e.snapshot_id where e.submission_id in (select submission_id from finalization_events where action = 'REOPENED') group by s.student_ref order by 1"
seq=$($PSQL -c "select count(*) from (select submission_id, string_agg(action, ',' order by seq) a from finalization_events group by 1) t where a = 'FINALIZED,REOPENED,FINALIZED'")
echo "booklets with the sequence FINALIZED, REOPENED, FINALIZED: $seq"
test "$seq" -ge 1

echo "--- frozen snapshots (rubric version, ScoreComputer, marks; two snapshots for a reopened booklet):"
$PSQL -c "select s.student_ref, n.snapshot_no, n.rubric_version_no, n.score_computer_version, n.total || ' / ' || n.max_total, left(n.inputs_sha256, 12) from result_snapshots n join submissions s on s.id = n.submission_id where s.student_ref like 'P4-%' order by s.student_ref, n.snapshot_no"
two=$($PSQL -c "select count(*) from (select submission_id from result_snapshots group by 1 having count(*) = 2) t")
test "$two" -ge 1

echo "--- who did what (actor role, action, count):"
$PSQL -c "select u.role, a.action, count(*) from audit_logs a join users u on u.id = a.actor_id where a.action in ('submission.finalize','submission.reopen','evaluation.override','report.student_pdf','report.summary_csv','report.summary_pdf','user.create','exam.assign') group by 1,2 order by 2,1"
for need in "submission.finalize:3" "submission.reopen:1" "evaluation.override:1" "report.student_pdf:2" "report.summary_csv:1" "user.create:3"; do
  act="${need%%:*}"; min="${need##*:}"
  c=$($PSQL -c "select count(*) from audit_logs where action = '$act'")
  test "$c" -ge "$min" || { echo "audit action $act: $c, expected at least $min"; exit 1; }
done
ft=$($PSQL -c "select count(distinct u.role) from audit_logs a join users u on u.id = a.actor_id where a.action = 'submission.finalize' and u.role = 'examiner'")
test "$ft" -eq 0   # no examiner ever finalized anything

echo "--- every report is tied to a snapshot and to the exact bytes (audit details):"
$PSQL -c "select action, details->>'snapshot_no', left(coalesce(details->>'sha256',''), 12), details->>'bytes' from audit_logs where action in ('report.student_pdf') order by at limit 6"

echo "--- the database refuses edits to history and to a finalized result:"
sub=$($PSQL -c "select submission_id from finalization_events e where action = 'FINALIZED' and seq = (select max(seq) from finalization_events where submission_id = e.submission_id) limit 1")
test -n "$sub"
for stmt in \
  "update result_snapshots set total = 99" "delete from result_snapshots" \
  "update finalization_events set reason = 'x'" "delete from finalization_events" \
  "update answer_regions set crossed_out = true where submission_id = '$sub'" \
  "delete from answer_regions where submission_id = '$sub'" \
  "insert into evaluations (id, submission_id, rubric_version_id, qid, attempt_no, verdicts, notes, marks, examiner_id, is_override, score_computer_version) select gen_random_uuid(), submission_id, rubric_version_id, qid, attempt_no, verdicts, notes, marks, examiner_id, false, score_computer_version from evaluations where submission_id = '$sub' limit 1"; do
  out=$($PSQL -c "$stmt" 2>&1 || true)
  echo "$out" | grep -qE "append-only|finalized" || { echo "NOT refused: $stmt  -> $out"; exit 1; }
  echo "refused: ${stmt:0:90}"
done
echo "PHASE 4 EVIDENCE OK"
