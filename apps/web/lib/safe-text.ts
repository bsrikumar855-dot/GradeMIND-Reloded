/**
 * Student text is DATA, never markup or instructions (I5). React already escapes text nodes, and nothing here uses
 * dangerouslySetInnerHTML; this additionally removes characters that can make text LOOK different from what it is in a review
 * tool: bidirectional overrides/isolates (visual reordering), zero-width and other invisible format characters, and C0/C1
 * control characters. Line breaks and tabs become spaces. Ordinary text in any script is untouched.
 *
 * The pattern is written with visible escapes on purpose: a source file must never contain the invisible characters itself.
 */
const INVISIBLE =
  /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u00AD\u061C\u180E\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF\uFFF9-\uFFFB]/g;

export function safeText(s: string): string {
  return s.replace(/[\t\n\r]+/g, " ").replace(INVISIBLE, "");
}
