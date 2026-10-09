#!/usr/bin/env bash
# D26.2: the OCR image installs Paddle and the PP-OCRv6 weights from vendored copies, so a Paddle CDN or Hugging Face
# outage cannot break a build. The copies are GitHub release assets of this repo (Apache-2.0 software, unmodified),
# pinned by sha256 in services/ocr/vendor.sha256. Usage: scripts/fetch_ocr_vendor.sh   (idempotent; verifies hashes)
set -euo pipefail
cd "$(dirname "$0")/../services/ocr"
TAG="${OCR_VENDOR_TAG:-ocr-vendor-v1}"
BASE="https://github.com/bsrikumar855-dot/GradeMIND-Reloded/releases/download/$TAG"
mkdir -p vendor-cache
while read -r sum name; do
  f="vendor-cache/$name"
  if [ ! -f "$f" ] || ! echo "$sum  $f" | sha256sum -c --status; then
    echo "fetching $name"
    curl -fsSL --retry 3 -o "$f.part" "$BASE/$name"
    mv "$f.part" "$f"
  fi
  echo "$sum  $f" | sha256sum -c
done < vendor.sha256
rm -rf wheelhouse/*.whl models/official_models
mkdir -p wheelhouse models
tar -xf vendor-cache/ocr-wheelhouse.tar
tar -xf vendor-cache/ocr-models.tar -C models
echo "vendored: $(ls wheelhouse/*.whl | wc -l) wheels, models: $(ls models/official_models)"
