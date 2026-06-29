# Build from AI_Mesh_Firewall repo root
# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

WORKDIR /app/gateway
COPY gateway/pyproject.toml gateway/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Runtime: slim Python + prebuilt .venv + uvx/npx for MCP stdio (no shell-free distroless).
FROM python:3.12-slim-bookworm AS runtime

COPY --from=ghcr.io/astral-sh/uv:python3.12-bookworm-slim /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/gateway/.venv/bin:$PATH" \
    PYTHONPATH=/app/shared:/app/gateway:/app/gateway/ai_mesh_gateway

WORKDIR /app/gateway
COPY shared /app/shared
COPY gateway/ai_mesh_gateway ./ai_mesh_gateway
COPY --from=builder /app/gateway/.venv /app/gateway/.venv

# Node.js 20 for stdio MCP servers via npx (bookworm glibc matches Python wheels).
COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

EXPOSE 8300

CMD gunicorn ai_mesh_gateway.main:app \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8300 \
    --workers ${WEB_CONCURRENCY:-4} \
    --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-1000} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-60} \
    --keep-alive ${GUNICORN_KEEPALIVE:-65}
