#!/usr/bin/env bash
# Encrypt a backup folder with age (D30.4):  scripts/backup_encrypt.sh BACKUP_DIR RECIPIENTS_FILE OUT.tar.age
#
# Only PUBLIC keys are used: RECIPIENTS_FILE holds one or more `age1...` lines. The private key (identity) is held by the owner, off this
# machine, and is needed only to restore. This script REFUSES a file that contains a private key, so a mistake cannot put one next to the data.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${1:?usage: backup_encrypt.sh BACKUP_DIR RECIPIENTS_FILE OUT.tar.age}"
RECIP="${2:?recipients file}"
OUT="${3:?output file}"
AGE="${AGE_BIN:-tools/age/age}"
[ -x "$AGE" ] || { echo "backup_encrypt: $AGE not found; run scripts/fetch_age.sh" >&2; exit 2; }
[ -d "$DIR" ] || { echo "backup_encrypt: $DIR is not a folder" >&2; exit 2; }
[ -f "$RECIP" ] || { echo "backup_encrypt: recipients file $RECIP not found" >&2; exit 2; }
if grep -q 'AGE-SECRET-KEY-' "$RECIP"; then
  echo "backup_encrypt: $RECIP contains a PRIVATE key. Give this script the public key only (the line starting age1...)." >&2; exit 2
fi
grep -Eq '^age1[a-z0-9]{50,}' "$RECIP" || { echo "backup_encrypt: $RECIP has no age public key (a line starting age1...)" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "backup_encrypt: $OUT already exists" >&2; exit 2; }
umask 077
tmp="$OUT.partial"
trap 'rm -f "$tmp"' EXIT
tar -C "$(dirname "$DIR")" -cf - "$(basename "$DIR")" | "$AGE" -R "$RECIP" -o "$tmp"
mv "$tmp" "$OUT"
trap - EXIT
echo "encrypted $(basename "$DIR") -> $OUT ($(wc -c < "$OUT") bytes)"
