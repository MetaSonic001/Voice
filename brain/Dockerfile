# syntax=docker/dockerfile:1
#
# The brain: one container with the text agent, the voice engine, call audit, the desk API and the embedded mock bank
# (the modular monolith). The UI is a separate image (ui/Dockerfile).
#
# Build from the repository root (the dummy-data workbook lives outside brain/).
#
# One Dockerfile, two images (deploy/README.md, deploy/aws-pilot-guide.md):
#   au-brain (on AWS: ECS on EC2 / Managed Instances, no GPU; ADR 0028):
#       docker build --platform linux/amd64 --build-arg SEARCH_MODELS=0 -f brain/Dockerfile -t au-brain .
#       run with SEARCH_PROVIDER=sagemaker (or http, for a dedicated GPU search server)
#   au-search (the search models, on a GPU):
#       docker build --platform linux/amd64 -f brain/Dockerfile -t au-search .        (default build, models inside)
#       on SageMaker: SageMaker runs `serve` (port 8080)
#       on a GPU server: python -m app search --host 0.0.0.0 --port 8200            (needs SEARCH_TOKEN)
#   A brain with the models inside (SEARCH_PROVIDER=local) is the default build too; that is for a single GPU machine
#   only (on CPU a rerank takes 20-37 s), not for AWS.
# Settings are environment variables (brain/.env.example); nothing secret is baked in. See deploy/README.md.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ── 1. Build: Python packages and the two search models ──
FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Packages first (this layer is reused until uv.lock changes). Exact versions from uv.lock; torch is the CUDA 12.8 build.
COPY brain/pyproject.toml brain/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# The models (~4.5 GB) next, so a code change doesn't download them again. The script checks each weights file's SHA-256.
# SEARCH_MODELS=0 leaves them out: for a brain that uses the GPU search server (SEARCH_PROVIDER=http).
ARG SEARCH_MODELS=1
COPY brain/scripts ./scripts
RUN if [ "$SEARCH_MODELS" = "1" ]; then /app/.venv/bin/python scripts/download_models.py; else mkdir -p models; fi

# Then the code, installed in place (app/config.py finds models/, seed/ and evals/ next to app/).
COPY brain/app ./app
COPY brain/seed ./seed
COPY brain/evals ./evals
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
COPY ["Sample Questions/Inbound_Voice_Agent_Dummy_Data.xlsx", "./seed/Inbound_Voice_Agent_Dummy_Data.xlsx"]

# ── 2. Run: no uv, no build caches; a non-root user ──
FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app /app
# Code and models stay read-only (owned by root). The app writes only here: local database (when no DATABASE_URL),
# uploaded files, recordings, logs and eval reports.
RUN mkdir -p /app/data /app/logs /app/evals/reports \
    && chown -R app:app /app/data /app/logs /app/evals/reports

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    BANK_DATA_FILE=/app/seed/Inbound_Voice_Agent_Dummy_Data.xlsx \
    LOG_CONSOLE=json \
    FORWARDED_ALLOW_IPS=*

# SageMaker starts an endpoint's container as `docker run <image> serve`: this command runs the search server in its
# SageMaker mode (/ping, /invocations on port 8080). Written here, so no Windows line endings can break it.
RUN printf '#!/bin/sh\nexec python -m app search --sagemaker "$@"\n' > /usr/local/bin/serve && chmod 755 /usr/local/bin/serve

USER app
EXPOSE 8000 8200 8080
# ECS/ALB checks: GET /health (process up) and GET /health/ready (database reachable). Models load before the port
# opens (~1-2 min), so give the container a start period of 300 s.
CMD ["python", "-m", "app", "serve", "--host", "0.0.0.0", "--port", "8000"]
