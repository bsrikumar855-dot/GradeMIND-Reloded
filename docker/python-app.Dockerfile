# API and worker image (same code, different command). Build context: repo root (allow-listed by .dockerignore).
FROM ghcr.io/astral-sh/uv@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

COPY --from=uv /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app

# dependency layer (cached until uv.lock or a pyproject changes)
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
RUN uv sync --frozen --no-dev --no-install-workspace

COPY packages/core packages/core
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --frozen --no-dev && useradd --uid 10001 --no-create-home app
USER 10001
