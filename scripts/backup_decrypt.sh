#!/usr/bin/env bash
# Decrypt a backup made by backup_encrypt.sh:  scripts/backup_decrypt.sh FILE.tar.age IDENTITY_FILE OUT_PARENT_DIR
# Prints the path of the unpacked backup folder. The identity (private key) is passed in by the owner for this one command; nothing keeps it.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: backup_decrypt.sh FILE.tar.age IDENTITY_FILE OUT_PARENT_DIR}"
ID="${2:?identity file}"
OUT="${3:?output parent folder}"
AGE="${AGE_BIN:-tools/age/age}"
[ -x "$AGE" ] || { echo "backup_decrypt: $AGE not found; run scripts/fetch_age.sh" >&2; exit 2; }
[ -f "$FILE" ] && [ -f "$ID" ] || { echo "backup_decrypt: file or identity not found" >&2; exit 2; }
mkdir -p "$OUT"; chmod 700 "$OUT"
umask 077
"$AGE" -d -i "$ID" "$FILE" | tar -x -C "$OUT" --no-same-owner --no-same-permissions
inner="$(find "$OUT" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -n "$inner" ] || { echo "backup_decrypt: the archive held no backup folder" >&2; exit 1; }
echo "$inner"
