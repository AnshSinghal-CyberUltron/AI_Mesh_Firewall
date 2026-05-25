# AIGuardX Gateway Proxy - OpenAI-compatible policy enforcement proxy
# Build with context from repo root: docker build -f gateway/Dockerfile .
FROM python:3.12-slim

# Install Node.js (required for stdio-transport MCP servers like Semgrep & Playwright)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Pre-install global npm MCP server packages (survive container restarts)
RUN npm install -g @modelcontextprotocol/sdk mcp-server-semgrep @playwright/mcp @perplexity-ai/mcp-server @modelcontextprotocol/server-github mcp-remote 2>/dev/null || true

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
ENV PYTHONPATH=/app

# Copy dependency manifests (full workspace: root + both members)
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY gateway/pyproject.toml ./gateway/pyproject.toml

# Install gateway deps from lock file
RUN UV_HTTP_TIMEOUT=300 uv sync --frozen --package gateway --no-dev --no-install-project

# Copy gateway source and shared package (used by gateway for request normalization)
COPY gateway/ ./
COPY shared/ ./shared/

# Ensure Docker-built venv is intact (local .venv from COPY may overwrite it)
RUN rm -rf .venv && uv sync --frozen --package gateway --no-dev --no-install-project

EXPOSE 8300
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8300", "--workers", "9"]
