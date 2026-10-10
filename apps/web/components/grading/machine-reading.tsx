"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { safeText } from "@/lib/safe-text";
import type { LineHighlight, MachineLine, MachineReadingData, RegionReading } from "@/lib/types";

const PREF = "gm_show_machine_reading";

function loadPref(): boolean {
  try {
    return window.localStorage.getItem(PREF) !== "off"; // shown by default
  } catch {
    return true; // storage can be blocked: the panel still works
  }
}

/**
 * "Machine reading" (OCR assist, D28): what the OCR engine read inside the examiner's answer boxes, so the page can be read
 * faster. DISPLAY-ONLY: it never decides marks and never suggests a verdict. The text is the student's, so it is rendered as an
 * escaped plain text node (never markup), and the panel always says that it can be wrong.
 */
export function MachineReading({
  data,
  failed,
  qid,
  attempt,
  pageNo,
  onHighlight,
}: {
  data: MachineReadingData | null;
  failed: boolean;
  qid: string;
  attempt: number;
  pageNo: (pageId: string) => number | undefined;
  onHighlight: (h: LineHighlight | null) => void;
}) {
  const [show, setShow] = useState<boolean>(loadPref);
  const regions: RegionReading[] = (data?.regions ?? []).filter((r) => r.qid === qid && r.attempt_no === attempt);

  function toggle() {
    setShow((s) => {
      try {
        window.localStorage.setItem(PREF, s ? "off" : "on");
      } catch {
        /* a blocked store only means the choice is not remembered */
      }
      if (s) onHighlight(null);
      return !s;
    });
  }

  return (
    <Card className="gap-3 py-4" data-testid="machine-reading-panel">
      <CardHeader className="flex-row items-center justify-between gap-2 px-4">
        <CardTitle className="text-base">Machine reading</CardTitle>
        <Button type="button" size="sm" variant="outline" aria-pressed={show} onClick={toggle}>
          {show ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
          {show ? "Hide machine reading" : "Show machine reading"}
        </Button>
      </CardHeader>
      {show ? (
        <CardContent className="flex flex-col gap-3 px-4">
          <p className="text-sm font-medium" data-testid="machine-reading-notice">
            {data?.notice ?? "Machine reading: it can be wrong, especially for handwriting. Always check it against the page."}
          </p>
          {failed ? (
            <Alert>Machine reading is not available right now. You can still grade from the page.</Alert>
          ) : !data ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : regions.length === 0 ? (
            <p className="text-sm text-muted-foreground">Draw an answer box on the page to see what the machine read inside it.</p>
          ) : (
            regions.map((r) => (
              <section key={r.region_id} aria-label={`Machine reading, page ${r.page_no}`} className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Page {r.page_no}
                  {r.crossed_out ? " · crossed out" : ""}
                </h3>
                {r.page_status !== "read" ? (
                  <p className="text-sm text-muted-foreground">
                    {r.page_status === "failed"
                      ? "This page could not be machine-read. Read it from the page."
                      : "This page has not been machine-read yet."}
                  </p>
                ) : r.lines.length === 0 ? (
                  <p className="text-sm text-muted-foreground">The machine found no text inside this box.</p>
                ) : (
                  <ol className="flex flex-col gap-1" aria-label="Lines, top to bottom">
                    {r.lines.map((ln, i) => (
                      <Line key={ln.id} line={ln} index={i} total={r.lines.length} pageId={r.page_id} pageNo={pageNo(r.page_id)} onHighlight={onHighlight} />
                    ))}
                  </ol>
                )}
              </section>
            ))
          )}
        </CardContent>
      ) : null}
    </Card>
  );
}

function Line({
  line,
  index,
  total,
  pageId,
  pageNo,
  onHighlight,
}: {
  line: MachineLine;
  index: number;
  total: number;
  pageId: string;
  pageNo: number | undefined;
  onHighlight: (h: LineHighlight | null) => void;
}) {
  const on = () => onHighlight({ page_id: pageId, bbox: line.bbox });
  return (
    <li
      tabIndex={0}
      data-testid="machine-line"
      data-low-confidence={line.low_confidence ? "true" : "false"}
      aria-label={`Line ${index + 1} of ${total}${line.low_confidence ? ", low confidence, check this line" : ""}${pageNo ? `, page ${pageNo}` : ""}`}
      onFocus={on}
      onBlur={() => onHighlight(null)}
      onMouseEnter={on}
      onMouseLeave={() => onHighlight(null)}
      className={
        "rounded-md border-l-4 px-2 py-1 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 " +
        (line.low_confidence ? "border-amber-500 border-dashed bg-amber-50" : "border-transparent bg-muted/50")
      }
    >
      {/* a plain text node: React escapes it; safeText also strips look-alike tricks (bidi overrides, invisible characters) */}
      <span dir="auto" className="whitespace-pre-wrap break-words">
        {safeText(line.text) || "(blank line)"}
      </span>
      {line.low_confidence ? (
        <Badge tone="warning" className="ml-2 align-middle">
          check this line
        </Badge>
      ) : null}
    </li>
  );
}
