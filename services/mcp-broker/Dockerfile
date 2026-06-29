# MCP broker (Sandbox Controller) — Docker SDK + FastAPI on :8311.
# Build from AI_Mesh_Firewall repo root:
#   docker build -t ai-mesh/mcp-broker:latest -f services/mcp-broker/Dockerfile .
# Per-org sandbox image (separate build):
#   docker build -t ai-mesh/mcp-sandbox:latest -f services/mcp-broker/sandbox-image/Dockerfile .
# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

WORKDIR /app/mcp-broker
COPY services/mcp-broker/pyproject.toml ./
RUN uv sync --no-dev --no-install-project

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/mcp-broker/.venv/bin:$PATH" \
    PYTHONPATH=/app/mcp-broker/src \
    MCP_SANDBOX_IMAGE=ai-mesh/mcp-sandbox:latest \
    MCP_SANDBOX_NETWORK=mcp_sandbox_bridge

WORKDIR /app/mcp-broker
COPY services/mcp-broker/src ./src
COPY --from=builder /app/mcp-broker/.venv /app/mcp-broker/.venv

LABEL ai_mesh.role=mcp-broker \
    ai_mesh.sandbox_image_dockerfile=services/mcp-broker/sandbox-image/Dockerfile

EXPOSE 8311

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8311/health', timeout=5)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8311"]
