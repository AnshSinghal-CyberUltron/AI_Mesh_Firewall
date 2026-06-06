# Build from AI_Mesh_Firewall repo root
# syntax=docker/dockerfile:1

# uv installs wheels into .venv; CPython in the runtime stage executes them.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

WORKDIR /app/control
COPY control/pyproject.toml control/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Runtime: Debian slim only — no uv binary (~30MB saved vs single-stage uv image).
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/control/.venv/bin:$PATH" \
    PYTHONPATH=/app/shared:/app/control/ai_mesh_control

WORKDIR /app
COPY shared /app/shared
COPY control/manage.py /app/control/manage.py
COPY control/ai_mesh_control /app/control/ai_mesh_control
COPY --from=builder /app/control/.venv /app/control/.venv

WORKDIR /app/control
EXPOSE 8000
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "--proxy-headers", "main_app.asgi:application"]
