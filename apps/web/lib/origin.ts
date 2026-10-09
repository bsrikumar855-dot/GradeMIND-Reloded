/** CSRF guard for form posts: a cross-site post carries a foreign (or opaque "null") Origin; browsers send Origin on POST. */
export function isSameOrigin(origin: string | null, host: string | null): boolean {
  if (origin === null) return true; // non-browser clients (no ambient cookies to abuse)
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
