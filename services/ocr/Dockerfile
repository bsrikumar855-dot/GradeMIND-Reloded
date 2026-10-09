# GradeMIND OCR service: PP-OCRv6 on CPU (D18; the GPU is shared and reserved). Build context: services/ocr.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock.txt

ENV PYTHONPATH=/app/src \
    PADDLE_PDX_CACHE_HOME=/models \
    PADDLE_PDX_MODEL_SOURCE=huggingface \
    PYTHONUNBUFFERED=1
COPY expected_models.json .
COPY src ./src

# Download the weights at build time and run the rule-12 assertion here too: an image whose resolved models or
# weight hashes differ from expected_models.json fails to build.
RUN python -c "from grademind_ocr.main import _paddle_engine; from grademind_ocr.rule12 import assert_resolved, load_expected; assert_resolved(load_expected(), _paddle_engine().resolved()); print('rule 12 OK at build')" \
 && useradd --uid 10001 --no-create-home ocr && chown -R ocr /models
USER 10001
# the runtime never downloads: if the baked weights are missing the service fails rule 12 instead
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

EXPOSE 8800
HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=3 CMD curl -fsS http://127.0.0.1:8800/ocr/health || exit 1
CMD ["uvicorn", "grademind_ocr.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8800"]
