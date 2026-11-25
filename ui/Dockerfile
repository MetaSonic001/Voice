# syntax=docker/dockerfile:1
#
# The test console (static pages: /, /kb, /calls, /desk, /fixes) served by nginx. No Node server at run time.
#
# Build from the repository root:
#   docker build --platform linux/amd64 -f ui/Dockerfile -t au-ui .
#
# NEXT_PUBLIC_API_URL is fixed at build time (it is compiled into the pages):
#   "/" (default)            the API is on the same address as the UI: on AWS the load balancer sends /api/* to the
#                            brain and everything else here. Works for any domain, no rebuild.
#   http://localhost:8000    running both images on a laptop (pass --build-arg NEXT_PUBLIC_API_URL=...).

# ── 1. Build the static pages (Node runs Next.js; Bun installs the exact versions in bun.lock) ──
FROM node:22-bookworm-slim AS build
COPY --from=oven/bun:1.3.14 /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /ui
COPY ui/package.json ui/bun.lock ./
RUN bun install --frozen-lockfile
COPY ui/ ./
ARG NEXT_PUBLIC_API_URL=/
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1
RUN bun run build

# ── 2. Serve them: nginx as a non-root user on port 8080 ──
FROM nginxinc/nginx-unprivileged:1.28-alpine AS runtime
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/out /usr/share/nginx/html
EXPOSE 8080
# Health check: GET /ui-health
