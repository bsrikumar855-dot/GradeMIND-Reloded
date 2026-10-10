"use client";

import { Eye, EyeOff } from "lucide-react";
import { useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, ClientError } from "@/lib/client";
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
  submissionId,
  qid,
  attempt,
  pageNo,
  onHighlight,
  onChanged,
}: {
  data: MachineReadingData | null;
  failed: boolean;
  submissionId: string;
  qid: string;
  attempt: number;
  pageNo: (pageId: string) => number | undefined;
  onHighlight: (h: LineHighlight | null) => void;
  onChanged: () => void;
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
                      <Line
                        key={ln.id}
                        line={ln}
                        index={i}
                        total={r.lines.length}
                        pageId={r.page_id}
                        pageNo={pageNo(r.page_id)}
                        submissionId={submissionId}
                        onHighlight={onHighlight}
                        onChanged={onChanged}
                      />
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
  submissionId,
  onHighlight,
  onChanged,
}: {
  line: MachineLine;
  index: number;
  total: number;
  pageId: string;
  pageNo: number | undefined;
  submissionId: string;
  onHighlight: (h: LineHighlight | null) => void;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const item = useRef<HTMLLIElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const on = () => onHighlight({ page_id: pageId, bbox: line.bbox });
  const shown = safeText(line.text);
  const n = index + 1;

  function start() {
    setDraft(shown);
    setError(null);
    setEditing(true);
    setTimeout(() => input.current?.focus(), 0);
  }
  function stop() {
    setEditing(false);
    setTimeout(() => item.current?.focus(), 0);
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`submissions/${submissionId}/ocr-lines/${line.id}/correction`, {
        method: "PUT",
        json: { text: draft, expected_correction_id: line.correction_id },
      });
      onChanged();
      stop();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not save the correction. Please try again.");
      if (err instanceof ClientError && err.code === "line_changed") onChanged(); // show what is there now
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      ref={item}
      tabIndex={0}
      data-testid="machine-line"
      data-low-confidence={line.low_confidence ? "true" : "false"}
      data-corrected={line.corrected ? "true" : "false"}
      aria-label={`Line ${n} of ${total}${line.corrected ? ", corrected by an examiner" : ""}${line.low_confidence ? ", low confidence, check this line" : ""}${pageNo ? `, page ${pageNo}` : ""}`}
      onFocus={on}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onHighlight(null);
      }}
      onMouseEnter={on}
      onMouseLeave={() => onHighlight(null)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget && !editing) {
          e.preventDefault();
          start();
        }
      }}
      className={
        "rounded-md border-l-4 px-2 py-1 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 " +
        (line.low_confidence ? "border-amber-500 border-dashed bg-amber-50" : line.corrected ? "border-emerald-600 bg-emerald-50" : "border-transparent bg-muted/50")
      }
    >
      {editing ? (
        <form onSubmit={save} className="flex flex-col gap-2">
          {/* what the line reads RIGHT NOW: after a conflict this shows the other examiner's version while the draft stays editable */}
          <p className="text-xs text-muted-foreground" data-testid="machine-line-current">
            Currently reads: <q dir="auto">{shown || "(blank line)"}</q>
          </p>
          {/* the examiner's own reading of the line, literally: misspellings are kept, [?] marks an illegible part */}
          <Input
            ref={input}
            type="text"
            dir="auto"
            value={draft}
            maxLength={2000}
            aria-label={`Corrected text for line ${n}`}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                stop();
              }
            }}
          />
          <p className="text-xs text-muted-foreground">Type the line exactly as the student wrote it. Keep spelling mistakes; use [?] for a part you cannot read.</p>
          {error ? (
            <Alert variant="destructive" role="alert">
              {error}
            </Alert>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy || draft === shown}>
              {busy ? "Saving…" : "Save correction"}
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={stop}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          {/* a plain text node: React escapes it; safeText also strips look-alike tricks (bidi overrides, invisible characters) */}
          <span dir="auto" className="whitespace-pre-wrap break-words" data-testid="machine-line-text">
            {shown || "(blank line)"}
          </span>
          {line.corrected ? (
            <Badge tone="success" className="ml-2 align-middle">
              corrected
            </Badge>
          ) : null}
          {line.low_confidence ? (
            <Badge tone="warning" className="ml-2 align-middle">
              check this line
            </Badge>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <Button type="button" size="sm" variant="ghost" className="h-7 px-2" aria-label={`Correct line ${n}`} onClick={start}>
              Correct this line
            </Button>
            {line.corrected ? (
              <Button type="button" size="sm" variant="ghost" className="h-7 px-2" aria-expanded={showOriginal} onClick={() => setShowOriginal((v) => !v)}>
                {showOriginal ? "Hide original" : "Show original"}
              </Button>
            ) : null}
          </div>
          {line.corrected && showOriginal ? (
            <p className="mt-1 text-xs text-muted-foreground" data-testid="machine-line-original">
              The machine read: <q dir="auto">{safeText(line.original_text) || "(blank line)"}</q>
            </p>
          ) : null}
        </>
      )}
    </li>
  );
}
