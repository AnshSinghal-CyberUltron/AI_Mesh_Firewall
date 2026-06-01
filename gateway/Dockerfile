# Build from AI_Mesh_Firewall repo root
FROM python:3.12-slim-bookworm

WORKDIR /app
COPY shared /app/shared
COPY gateway/pyproject.toml /app/gateway/pyproject.toml
COPY gateway/ai_mesh_gateway /app/gateway/ai_mesh_gateway

WORKDIR /app/gateway
RUN pip install --no-cache-dir \
    boto3 cryptography fastapi "uvicorn[standard]" gunicorn httpx jsonschema litellm \
    numpy pinecone psutil "psycopg[binary,pool]" pymilvus python-dotenv pyyaml \
    redis requests motor websockets \
    "chromadb==0.5.20" \
    "prometheus-client>=0.20.0" \
    opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp

ENV PYTHONPATH=/app/shared:/app/gateway:/app/gateway/ai_mesh_gateway

# uv/uvx RUNTIME only (not any server package). uvx is the Python equivalent of
# `npx`: it fetches + caches a published package on first run, so Python-based
# stdio MCP servers (e.g. `uvx semgrep-mcp`) are pulled ON-DEMAND at client
# connect time rather than baked into the image. The shared on-demand cache is
# mounted as a docker volume (UV_CACHE_DIR) for warm reuse across connects.
RUN pip install --no-cache-dir uv && uv --version && uvx --version

# Node.js 20 runtime so stdio MCP servers can be launched via `npx`
# (e.g. `npx mcp-remote ...`). Copied from the official slim image to avoid
# apt/NodeSource network steps; bookworm→bookworm keeps the glibc ABI compatible.
COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version && npx --version

EXPOSE 8300

# Multi-process ASGI server. gunicorn pre-forks WEB_CONCURRENCY uvicorn workers
# (uvloop+httptools), each an independent event loop. This removes the single-
# process bottleneck that collapsed the gateway under load. Worker count and
# socket/timeout tuning are env-overridable for per-instance sizing (one worker
# per vCPU is the default). Shell form is used so ${VAR:-default} expands.
CMD gunicorn ai_mesh_gateway.main:app \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8300 \
    --workers ${WEB_CONCURRENCY:-4} \
    --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-1000} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-60} \
    --keep-alive ${GUNICORN_KEEPALIVE:-65}
