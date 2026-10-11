#!/usr/bin/env bash
# Fetch the pinned `age` release (D30.4) into tools/age/ and verify its SHA-256 BEFORE anything is unpacked or run.
#   scripts/fetch_age.sh            -> tools/age/age and tools/age/age-keygen (git-ignored)
# age v1.3.2, published 2026-08-29 by FiloSottile (BSD-3-Clause), linux/amd64. The checksum below is the one GitHub publishes for the release asset
# and was re-computed from the downloaded file when it was pinned.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="v1.3.2"
ASSET="age-${VERSION}-linux-amd64.tar.gz"
SHA256="cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10"
URL="https://github.com/FiloSottile/age/releases/download/${VERSION}/${ASSET}"
DEST="tools/age"

if [ -x "$DEST/age" ] && [ -x "$DEST/age-keygen" ] && [ "$("$DEST/age" --version 2>&1 | head -1)" = "${VERSION}" ]; then
  echo "age ${VERSION} already present in $DEST"; exit 0
fi
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL --retry 3 -o "$TMP/$ASSET" "$URL"
echo "$SHA256  $TMP/$ASSET" | sha256sum -c --quiet || { echo "fetch_age: checksum mismatch for $ASSET; nothing was installed" >&2; exit 1; }
tar -xzf "$TMP/$ASSET" -C "$TMP"
install -m 0755 "$TMP/age/age" "$DEST/age"
install -m 0755 "$TMP/age/age-keygen" "$DEST/age-keygen"
"$DEST/age" --version
