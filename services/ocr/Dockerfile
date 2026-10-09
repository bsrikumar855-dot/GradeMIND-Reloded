# GradeMIND OCR service: PP-OCRv6 on CPU (D18; the GPU is shared and reserved). Build context: services/ocr.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
# D26.2: Paddle and every other wheel come ONLY from the vendored, sha256-pinned wheelhouse
# (run scripts/fetch_ocr_vendor.sh first). The step has no network, so a Paddle CDN outage cannot affect it.
# A bind mount, not COPY, so the 330 MB wheelhouse never becomes an image layer.
RUN --network=none --mount=type=bind,source=wheelhouse,target=/wheelhouse \
    if ! ls /wheelhouse/*.whl >/dev/null 2>&1; then \
      echo "ERROR: services/ocr/wheelhouse is empty. Run scripts/fetch_ocr_vendor.sh first." >&2; exit 1; \
    fi \
 && pip install --no-cache-dir --no-index --find-links /wheelhouse -r requirements.txt -c requirements.lock.txt

ENV PYTHONPATH=/app/src \
    PADDLE_PDX_CACHE_HOME=/models \
    PYTHONUNBUFFERED=1
COPY expected_models.json .
COPY src ./src
# the vendored weights become the paddlex cache (scripts/fetch_ocr_vendor.sh; sha256-pinned)
COPY models/ /models/

# The weights are the vendored copies (no download: this step has no network). The rule-12 assertion runs at build
# time: an image whose resolved models or weight hashes differ from expected_models.json fails to build.
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
RUN --network=none \
    test -d /models/official_models || { echo "ERROR: services/ocr/models is empty. Run scripts/fetch_ocr_vendor.sh first." >&2; exit 1; }; \
    python -c "from grademind_ocr.main import _paddle_engine; from grademind_ocr.rule12 import assert_resolved, load_expected; assert_resolved(load_expected(), _paddle_engine().resolved()); print('rule 12 OK at build')" \
 && useradd --uid 10001 --no-create-home ocr && chown -R ocr /models
USER 10001

EXPOSE 8800
HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=3 CMD curl -fsS http://127.0.0.1:8800/ocr/health || exit 1
CMD ["uvicorn", "grademind_ocr.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8800"]
