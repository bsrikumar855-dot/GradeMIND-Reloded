"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { describe, nudge, START_BOX, type Box } from "@/lib/kbbox";
import type { LineHighlight, PageInfo, Region } from "@/lib/types";

/**
 * One booklet page: zoom, pan (drag when not drawing), rotate, fit width/page, thumbnails, and an optional
 * rectangle-drawing mode that reports a box in page-relative coordinates (0..1), which is what the API stores.
 */
export function PageViewer({
  pages,
  pageIdx,
  onPage,
  regions,
  labels,
  activeRegionId,
  highlight,
  canDraw,
  drawHint,
  onDraw,
  onSelectRegion,
}: {
  pages: PageInfo[];
  pageIdx: number;
  onPage: (i: number) => void;
  regions: Region[];
  labels: Map<string, string>;
  activeRegionId: string | null;
  highlight?: LineHighlight | null;
  canDraw: boolean;
  drawHint: string;
  onDraw: (pageId: string, bbox: Box) => void;
  onSelectRegion: (id: string) => void;
}) {
  const current: PageInfo | undefined = pages[pageIdx];
  const scroller = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [draw, setDraw] = useState(false);
  type Drag = { x0: number; y0: number; x1: number; y1: number };
  const [drag, setDrag] = useState<Drag | null>(null);
  // The drag lives in a ref as well as in state: a final pointermove and the pointerup that follows it can arrive before React has re-rendered,
  // and the lift must use the finger's LAST position, not the one from the last render (a box drawn quickly ended short of the finger).
  const dragRef = useRef<Drag | null>(null);
  const [kb, setKb] = useState<Box | null>(null); // keyboard-drawn box (3.0a): arrows move, Shift resizes, Enter confirms
  const canvas = useRef<HTMLDivElement>(null);
  const pan = useRef<{ x: number; y: number; sl: number; st: number; touch: boolean } | null>(null);
  const drawingNow = draw && canDraw && rotation === 0;

  // turning drawing on moves focus to the canvas, so a keyboard user can start the box straight away
  useEffect(() => {
    if (drawingNow) canvas.current?.focus();
  }, [drawingNow]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(200, el.clientWidth - 16)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!current) return <p className="text-muted-foreground">This booklet has no pages.</p>;
  const page: PageInfo = current;

  const W = width * zoom;
  const H = (W * page.height) / page.width;
  const swap = rotation % 180 !== 0;
  const wrapW = swap ? H : W;
  const wrapH = swap ? W : H;
  const drawing = drawingNow;

  function rel(e: React.PointerEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  }

  function down(e: React.PointerEvent<HTMLDivElement>) {
    // Not drawing: a mouse or pen drags the page around in both directions. A finger scrolls up and down natively (touch-action
    // pan-y) but its SIDEWAYS drag is handled here, never by the browser: a sideways swipe the browser handles can become its
    // "back" gesture and throw the examiner out of the booklet. While drawing, any pointer, touch included, draws.
    if (drawing) {
      e.currentTarget.setPointerCapture(e.pointerId);
      const p = rel(e);
      dragRef.current = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
      setDrag(dragRef.current);
    } else if (scroller.current) {
      const touch = e.pointerType === "touch";
      if (!touch) e.currentTarget.setPointerCapture(e.pointerId);
      pan.current = { x: e.clientX, y: e.clientY, sl: scroller.current.scrollLeft, st: scroller.current.scrollTop, touch };
    }
  }
  function move(e: React.PointerEvent<HTMLDivElement>) {
    if (dragRef.current) {
      const p = rel(e);
      dragRef.current = { ...dragRef.current, x1: p.x, y1: p.y };
      setDrag(dragRef.current);
    } else if (pan.current && scroller.current) {
      scroller.current.scrollLeft = pan.current.sl - (e.clientX - pan.current.x);
      if (!pan.current.touch) scroller.current.scrollTop = pan.current.st - (e.clientY - pan.current.y); // a finger scrolls vertically itself
    }
  }
  function up() {
    pan.current = null;
    const d = dragRef.current;
    if (!d) return;
    const box: Box = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
    dragRef.current = null;
    setDrag(null);
    if (box[2] - box[0] >= 0.005 && box[3] - box[1] >= 0.005) onDraw(page.id, box);
  }

  function canvasKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (!drawing) return;
    if (kb === null) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setKb(START_BOX);
      }
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      onDraw(page.id, kb);
      setKb(null);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setKb(null);
    } else {
      const next = nudge(kb, e.key, e.shiftKey, e.altKey);
      if (next) {
        e.preventDefault(); // arrow keys must not scroll the page while a box is being placed
        setKb(next);
      }
    }
  }

  function fitPage() {
    const el = scroller.current;
    if (!el) return;
    const byHeight = ((el.clientHeight - 16) * page.width) / page.height;
    setZoom(Math.min(1, byHeight / width));
  }

  const pageRegions = regions.filter((r) => r.page_id === page.id);

  return (
    <div className="flex min-w-0 gap-2">
      <ol aria-label="Pages" className="flex max-h-[75vh] w-16 shrink-0 flex-col gap-2 overflow-y-auto">
        {pages.map((p, i) => (
          <li key={p.id}>
            <button
              type="button"
              onClick={() => onPage(i)}
              aria-label={`Page ${p.page_no}`}
              aria-current={i === pageIdx ? "page" : undefined}
              className={cn("block w-full overflow-hidden rounded border-2 text-xs", i === pageIdx ? "border-primary" : "border-transparent")}
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- signed storage URL, not optimisable by next/image */}
              <img src={p.thumb_url ?? p.image_url} alt="" loading="lazy" className="w-full" />
              <span className="block bg-muted py-0.5">{p.page_no}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Page tools">
          <Button size="sm" variant="outline" onClick={() => onPage(Math.max(0, pageIdx - 1))} disabled={pageIdx === 0}>
            Prev page
          </Button>
          <span className="text-sm tabular-nums">
            Page {page.page_no} of {pages.length}
          </span>
          <Button size="sm" variant="outline" onClick={() => onPage(Math.min(pages.length - 1, pageIdx + 1))} disabled={pageIdx >= pages.length - 1}>
            Next page
          </Button>
          <span className="mx-1 h-5 w-px bg-border" />
          <Button size="sm" variant="outline" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}>
            −
          </Button>
          <span className="w-12 text-center text-sm tabular-nums">{Math.round(zoom * 100)}%</span>
          <Button size="sm" variant="outline" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(6, z * 1.25))}>
            +
          </Button>
          <Button size="sm" variant="outline" onClick={() => setZoom(1)}>
            Fit width
          </Button>
          <Button size="sm" variant="outline" onClick={fitPage}>
            Fit page
          </Button>
          <Button size="sm" variant="outline" onClick={() => setRotation((r) => (r + 90) % 360)}>
            Rotate
          </Button>
          {canDraw ? (
            <Button
              size="sm"
              variant={draw ? "default" : "outline"}
              aria-pressed={draw}
              onClick={() => {
                setDraw((d) => !d);
                setKb(null);
              }}
            >
              {draw ? "Drawing: on" : "Draw answer box"}
            </Button>
          ) : null}
        </div>
        {draw ? (
          <p className="text-sm text-muted-foreground" role="status" id="draw-status" data-testid="draw-status">
            {rotation !== 0
              ? "Rotate back to 0° to draw."
              : kb
                ? describe(kb)
                : `${drawHint} With the keyboard: focus the page, press Enter to place a box, then arrow keys to move it and Shift plus arrow keys to resize it.`}
          </p>
        ) : null}
        <div ref={scroller} className="max-h-[75vh] overflow-auto overscroll-contain rounded-md border bg-muted/40 p-2" data-testid="page-scroller">
          <div style={{ width: wrapW, height: wrapH }} className="relative mx-auto">
            <div
              style={{ width: W, height: H, left: (wrapW - W) / 2, top: (wrapH - H) / 2, transform: `rotate(${rotation}deg)` }}
              className={cn("absolute select-none", drawing ? "touch-none cursor-crosshair" : "touch-pan-y touch-pinch-zoom cursor-grab")}
              onPointerDown={down}
              onPointerMove={move}
              onPointerUp={up}
              onPointerCancel={up}
              data-testid="page-canvas"
              ref={canvas}
              tabIndex={drawing ? 0 : -1}
              role="group"
              aria-label={`Booklet page ${page.page_no}${drawing ? ": answer box drawing is on" : ""}`}
              aria-describedby={drawing ? "draw-status" : undefined}
              onKeyDown={canvasKey}
              onBlur={() => setKb(null)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- signed storage URL */}
              <img src={page.image_url} alt={`Booklet page ${page.page_no}`} draggable={false} className="size-full" />
              {pageRegions.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  aria-label={`Answer to ${labels.get(r.qid) ?? r.qid}, attempt ${r.attempt_no}`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => onSelectRegion(r.id)}
                  style={{ left: `${r.bbox[0] * 100}%`, top: `${r.bbox[1] * 100}%`, width: `${(r.bbox[2] - r.bbox[0]) * 100}%`, height: `${(r.bbox[3] - r.bbox[1]) * 100}%` }}
                  className={cn(
                    "absolute border-2 text-left text-xs font-medium",
                    r.id === activeRegionId ? "border-primary bg-primary/15" : "border-amber-500 bg-amber-400/10",
                    r.crossed_out && "border-dashed opacity-60",
                  )}
                >
                  <span className="bg-background/90 px-1">
                    {labels.get(r.qid) ?? r.qid}
                    {r.attempt_no > 1 ? ` #${r.attempt_no}` : ""}
                  </span>
                </button>
              ))}
              {highlight && highlight.page_id === page.id ? (
                <div
                  data-testid="line-highlight"
                  className="pointer-events-none absolute border-2 border-sky-600 bg-sky-400/20"
                  style={{
                    left: `${highlight.bbox[0] * 100}%`,
                    top: `${highlight.bbox[1] * 100}%`,
                    width: `${(highlight.bbox[2] - highlight.bbox[0]) * 100}%`,
                    height: `${(highlight.bbox[3] - highlight.bbox[1]) * 100}%`,
                  }}
                />
              ) : null}
              {kb ? (
                <div
                  data-testid="kb-box"
                  className="pointer-events-none absolute border-2 border-dashed border-primary bg-primary/10"
                  style={{ left: `${kb[0] * 100}%`, top: `${kb[1] * 100}%`, width: `${(kb[2] - kb[0]) * 100}%`, height: `${(kb[3] - kb[1]) * 100}%` }}
                />
              ) : null}
              {drag ? (
                <div
                  className="pointer-events-none absolute border-2 border-primary bg-primary/10"
                  style={{ left: `${Math.min(drag.x0, drag.x1) * 100}%`, top: `${Math.min(drag.y0, drag.y1) * 100}%`, width: `${Math.abs(drag.x1 - drag.x0) * 100}%`, height: `${Math.abs(drag.y1 - drag.y0) * 100}%` }}
                />
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
