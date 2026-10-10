/** Display helpers for the analytics page. A share arrives as "0.4000" (or null when n is too small); it is shown as a percentage. */
export function percent(share: string | null): string {
  return share === null ? "n/a" : `${(Number(share) * 100).toFixed(0)}%`;
}

export function duration(seconds: number | null): string {
  if (seconds === null) return "n/a";
  if (seconds < 90) return `${seconds} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/** Width of a bar, 0 to 100, relative to the largest count in its group. */
export function barWidth(count: number, max: number): number {
  return max <= 0 ? 0 : Math.round((count / max) * 100);
}
