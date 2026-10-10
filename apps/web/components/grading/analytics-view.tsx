import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { barWidth, duration, percent } from "@/lib/analytics-format";
import type { AnalyticsData } from "@/lib/types";

/** Read-only statistics from verdicts. A statistic under the minimum n is shown as "n < 5", never as a number. */
export function AnalyticsView({ examId, data }: { examId: string; data: AnalyticsData }) {
  const small = `n < ${data.min_n}`;
  return (
    <div className="flex flex-col gap-6" data-testid="analytics">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <nav aria-label="Population" className="flex gap-1">
          <Link
            href={`/exams/${examId}/analytics`}
            aria-current={data.scope === "all" ? "page" : undefined}
            className={data.scope === "all" ? "rounded-md bg-accent px-2 py-1 font-medium" : "px-2 py-1 text-muted-foreground underline-offset-4 hover:underline"}
          >
            All booklets
          </Link>
          <Link
            href={`/exams/${examId}/analytics?scope=finalized`}
            aria-current={data.scope === "finalized" ? "page" : undefined}
            className={data.scope === "finalized" ? "rounded-md bg-accent px-2 py-1 font-medium" : "px-2 py-1 text-muted-foreground underline-offset-4 hover:underline"}
          >
            Finalized only
          </Link>
        </nav>
        <span className="text-muted-foreground">
          Rubric version {data.rubric_version_no} · {data.score_computer_version}
        </span>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Booklets</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
          {(
            [
              ["In this view", data.booklets.total],
              ["Finalized", data.booklets.finalized],
              ["Not started", data.booklets.not_started],
              ["In progress", data.booklets.in_progress],
              ["Complete", data.booklets.complete],
            ] as const
          ).map(([label, n]) => (
            <div key={label}>
              <div className="text-2xl font-semibold tabular-nums">{n}</div>
              <div className="text-muted-foreground">{label}</div>
            </div>
          ))}
          <p className="col-span-full text-muted-foreground" data-testid="ungraded">
            Mapped but not fully graded: {data.ungraded.mapped_but_ungraded_answers} answer(s) in {data.ungraded.booklets_with_ungraded} booklet(s).
          </p>
        </CardContent>
      </Card>

      <Card className="py-0">
        <CardContent className="overflow-x-auto px-0 focus-visible:ring-2 focus-visible:ring-ring" role="region" aria-label="Marks per question (scrolls sideways on a narrow screen)" tabIndex={0}>
          <table className="w-full text-sm">
            <caption className="sr-only">Marks per question</caption>
            <thead className="border-b bg-muted text-left">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">Question</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Graded (n)</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Mean</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Median</th>
                <th scope="col" className="px-4 py-3 font-medium">Marks awarded (count)</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Not attempted</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Not fully graded</th>
              </tr>
            </thead>
            <tbody>
              {data.questions.map((q) => {
                const max = Math.max(0, ...q.distribution.map((d) => d.count));
                return (
                  <tr key={q.qid} className="border-b align-top last:border-0" data-testid="analytics-question">
                    <th scope="row" className="px-4 py-3 text-left font-medium">
                      {q.label} <span className="font-normal text-muted-foreground">/ {q.max_marks}</span>
                    </th>
                    <td className="px-4 py-3 text-right tabular-nums">{q.n}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{q.mean ?? <span className="text-muted-foreground">{small}</span>}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{q.median ?? <span className="text-muted-foreground">{small}</span>}</td>
                    <td className="px-4 py-3">
                      {q.distribution.length === 0 ? (
                        <span className="text-muted-foreground">no graded answers yet</span>
                      ) : (
                        <ul className="flex flex-col gap-1">
                          {q.distribution.map((d) => (
                            <li key={d.marks} className="flex items-center gap-2">
                              <span className="w-10 text-right tabular-nums">{d.marks}</span>
                              <span className="h-3 rounded bg-primary/70" style={{ width: `${Math.max(4, barWidth(d.count, max))}px`, maxWidth: "10rem" }} aria-hidden="true" />
                              <span className="tabular-nums">
                                {d.count}
                                {d.share !== null ? <span className="text-muted-foreground"> ({percent(d.share)})</span> : null}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{q.not_attempted}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{q.incomplete}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Verdicts per criterion</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          {data.questions.map((q) => (
            <details key={q.qid}>
              <summary className="cursor-pointer font-medium">Question {q.label}</summary>
              <div className="mt-2 flex flex-col gap-3 pl-4">
                {q.criteria.map((c) => (
                  <div key={c.id}>
                    <div className="mb-1">
                      {c.name} <span className="text-muted-foreground">(n = {c.n}{c.suppressed ? `, ${small}: percentages not shown` : ""})</span>
                    </div>
                    <ul className="flex flex-wrap gap-2">
                      {c.levels.map((l) => (
                        <li key={l.id}>
                          <Badge>
                            {l.name} ({l.marks}): {l.count}
                            {l.share !== null ? ` · ${percent(l.share)}` : ""}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </details>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Overrides and time</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p data-testid="override-rate">
            Grades saved: <strong className="tabular-nums">{data.overrides.grades_saved}</strong> · changed by a second examiner (overrides):{" "}
            <strong className="tabular-nums">{data.overrides.overrides}</strong> · override rate:{" "}
            <strong>{data.overrides.rate !== null ? percent(data.overrides.rate) : <span className="font-normal text-muted-foreground">{small}</span>}</strong>
          </p>
          <p>
            Time from drawing an answer box to its first grade: median{" "}
            <strong>{data.time_to_grade.median_seconds !== null ? duration(data.time_to_grade.median_seconds) : <span className="font-normal text-muted-foreground">{small}</span>}</strong>{" "}
            (n = {data.time_to_grade.n}). <span className="text-muted-foreground">{data.time_to_grade.note}</span>
          </p>
        </CardContent>
      </Card>

      <ul className="list-disc pl-5 text-sm text-muted-foreground">
        {data.definitions.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
    </div>
  );
}
