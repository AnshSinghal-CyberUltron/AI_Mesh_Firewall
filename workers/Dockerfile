# Build from AI_Mesh_Firewall repo root
# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

WORKDIR /app/control
COPY control/pyproject.toml control/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/control/.venv/bin:$PATH" \
    PYTHONPATH=/app/shared:/app/control/ai_mesh_control:/app/workers \
    DJANGO_SETTINGS_MODULE=main_app.settings

WORKDIR /app
COPY shared /app/shared
COPY control/manage.py /app/control/manage.py
COPY workers /app/workers
COPY control/ai_mesh_control /app/control/ai_mesh_control
COPY --from=builder /app/control/.venv /app/control/.venv

WORKDIR /app/control
CMD ["celery", "-A", "ai_mesh_workers.celery_app", "worker", "-Q", "policy.compile,platform.batch,compute.heavy,scan.tier2,vector.index,mcp.audit", "--loglevel=info", "--concurrency=2"]
