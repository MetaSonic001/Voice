# syntax=docker/dockerfile:1
#
# The brain: one container with the text agent, the voice engine, call audit, the desk API and the embedded mock bank
# (the modular monolith). The UI is a separate image (ui/Dockerfile).
#
# Build from the repository root (the dummy-data workbook lives outside brain/).
#
# SEARCH_MODELS picks which search models go inside the image (they load from /app/models):
#   embed  the UAT image for EKS (no GPU, ADR 0031): bge-m3 only; the reranker is off on a CPU (RERANK=auto)
#       docker build --platform linux/amd64 --build-arg SEARCH_MODELS=embed -f brain/Dockerfile -t au-voice:brain-<version> .
#   1      both models (default): a GPU machine with SEARCH_PROVIDER=local, or the GPU search server
#          (`python -m app search`, or SageMaker's `serve` on port 8080) for later
#   0      none: Docker Compose on a laptop mounts brain/models instead (compose.yaml)
# Settings are environment variables (brain/.env.example); nothing secret is baked in. See deploy/README.md.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ── 1. Build: Python packages and the search models ──
FROM ${PYTHON_IMAGE} AS build
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# PyTorch first, from the build that fits: TORCH=cpu (default: EKS and the office laptop; several GB smaller) or
# TORCH=cu128 (an NVIDIA GPU; compose.gpu.yaml passes it).
ARG TORCH=cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/${TORCH}

# Then every other package at its exact version (requirements.txt). Reused until that file changes.
COPY brain/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# The models (~4.5 GB) next, so a code change doesn't download them again. The script checks each weights file's SHA-256.
# SEARCH_MODELS: 1 = both, embed = bge-m3 only, 0 = none (see the top of this file).
ARG SEARCH_MODELS=1
COPY brain/scripts ./scripts
RUN case "$SEARCH_MODELS" in \
      1) /app/.venv/bin/python scripts/download_models.py ;; \
      embed) /app/.venv/bin/python scripts/download_models.py --embed-only ;; \
      *) mkdir -p models ;; \
    esac

# Then the code, used in place (PYTHONPATH=/app; app/config.py finds models/, seed/ and evals/ next to app/).
COPY brain/app ./app
COPY brain/content ./content
COPY brain/seed ./seed
COPY brain/evals ./evals
COPY ["Sample Questions/Inbound_Voice_Agent_Dummy_Data.xlsx", "./seed/Inbound_Voice_Agent_Dummy_Data.xlsx"]

# ── 2. Run: no build caches; a non-root user ──
FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app /app
# Code and models stay read-only (owned by root). The app writes only here: local database (when no DATABASE_URL),
# uploaded files, recordings, logs and eval reports.
RUN mkdir -p /app/data /app/logs /app/evals/reports \
    && chown -R app:app /app/data /app/logs /app/evals/reports

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app \
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
# Health checks (EKS probes, load balancer): GET /health (process up) and GET /health/ready (database reachable).
# Models load before the port opens (~1-2 min), so allow up to 5 minutes for the first answer (deploy/eks/brain.yaml).
CMD ["python", "-m", "app", "serve", "--host", "0.0.0.0", "--port", "8000"]
