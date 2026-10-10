/** Keyboard-only answer-box drawing (3.0a). Pure functions: no DOM, unit-tested with `node --test`.
 *  Boxes are [x0, y0, x1, y1] as fractions of the unrotated page, exactly what the API stores. */

export type Box = [number, number, number, number];

export const START_BOX: Box = [0.1, 0.1, 0.5, 0.3];
export const STEP = 0.02; // 2% of the page per key press
export const FINE_STEP = 0.005; // Alt: 0.5%
export const MIN_SIZE = 0.02;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const round = (v: number) => Math.round(v * 1e5) / 1e5;

/** Arrow keys move the box; Shift+arrows resize it (right/down grow, left/up shrink the bottom-right corner).
 *  Returns null for any other key. The box always stays inside the page and at least MIN_SIZE wide and high. */
export function nudge(box: Box, key: string, shift: boolean, fine = false): Box | null {
  const dx = key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0;
  const dy = key === "ArrowDown" ? 1 : key === "ArrowUp" ? -1 : 0;
  if (!dx && !dy) return null;
  const s = fine ? FINE_STEP : STEP;
  let [x0, y0, x1, y1] = box;
  if (shift) {
    x1 = clamp(x1 + dx * s, x0 + MIN_SIZE, 1);
    y1 = clamp(y1 + dy * s, y0 + MIN_SIZE, 1);
  } else {
    const w = x1 - x0;
    const h = y1 - y0;
    x0 = clamp(x0 + dx * s, 0, 1 - w);
    y0 = clamp(y0 + dy * s, 0, 1 - h);
    x1 = x0 + w;
    y1 = y0 + h;
  }
  return [round(x0), round(y0), round(x1), round(y1)];
}

const pct = (v: number) => Math.round(v * 100);

/** Text for the live region, so a screen-reader user hears where the box is. */
export function describe(box: Box): string {
  return `Box ${pct(box[0])}% from the left, ${pct(box[1])}% from the top, ${pct(box[2] - box[0])}% wide, ${pct(box[3] - box[1])}% high. Arrow keys move it, Shift plus arrow keys resize it, Enter saves it, Escape cancels.`;
}
