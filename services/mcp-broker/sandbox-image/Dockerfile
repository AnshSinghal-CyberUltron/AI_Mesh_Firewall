# MCP per-tenant sandbox image — Node 20 + Python 3.12 + uv/npx for stdio MCP servers.
# Build from repo root:
#   docker build -t ai-mesh/mcp-sandbox:latest -f services/mcp-broker/sandbox-image/Dockerfile .
# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS runtime

# uv/uvx for Python MCP servers (parity with gateway/Dockerfile).
COPY --from=ghcr.io/astral-sh/uv:python3.12-bookworm-slim /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/agent/.venv/bin:$PATH" \
    ORG_SLUG=default \
    MCP_REMOTE_CONFIG_DIR=/data/mcp-auth

WORKDIR /opt/agent

# Node.js 20 for stdio MCP servers via npx (bookworm glibc matches Python wheels).
COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && mkdir -p /data/mcp-auth /tmp/mcp-orgs

# Sandbox-agent (in-container stdio MCP process manager).
COPY services/mcp-broker/sandbox-image/pyproject.toml ./pyproject.toml
COPY services/mcp-broker/sandbox-image/agent/ ./agent/
RUN uv sync --no-dev \
    && useradd --create-home --shell /usr/sbin/nologin sandbox \
    && chown -R sandbox:sandbox /opt/agent /data/mcp-auth /tmp/mcp-orgs

USER sandbox

EXPOSE 9320

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9320/health')" || exit 1

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "9320"]
