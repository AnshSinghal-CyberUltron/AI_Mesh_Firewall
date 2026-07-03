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

# BACKSTOP CHG-0142 (item 8, supply-chain defense-in-depth): bake a GLOBAL npmrc that
# force-disables npm/npx install lifecycle scripts for EVERY invocation in the sandbox —
# not only the one child that inherits ``npm_config_ignore_scripts=true`` from
# ``_build_child_env`` (CHG-0044). npm reads ``$PREFIX/etc/npmrc`` (= /usr/local/etc/npmrc
# for the /usr/local prefix) as the global config for ANY user, so a manual/debug ``npx``
# inside the container, or any future spawn path that forgets to set the env, STILL runs
# with install scripts OFF (the supply-chain RCE vector stays closed). Owned by root
# (created pre-USER), so the unprivileged ``sandbox`` user cannot rewrite it to re-enable
# scripts. audit/fund/update-notifier off = no needless egress from the egress-locked
# sandbox. The runtime env (``npm_config_ignore_scripts=true``) still takes precedence and
# AGREES — this is belt-and-suspenders, not a behavior change.
RUN mkdir -p /usr/local/etc \
    && printf '%s\n' 'ignore-scripts=true' 'audit=false' 'fund=false' 'update-notifier=false' \
        > /usr/local/etc/npmrc

# Shared stdio denylist/OAuth heuristics (gateway parity).
COPY shared/ai_mesh_shared/mcp_stdio_common.py /opt/shared/ai_mesh_shared/mcp_stdio_common.py
RUN mkdir -p /opt/shared/ai_mesh_shared \
    && printf '%s\n' '"""Sandbox subset of ai_mesh_shared (stdio common only)."""' \
        > /opt/shared/ai_mesh_shared/__init__.py
ENV PYTHONPATH="/opt/shared:${PYTHONPATH}"

# Sandbox-agent (in-container stdio MCP process manager).
COPY services/mcp-broker/sandbox-image/pyproject.toml ./pyproject.toml
COPY services/mcp-broker/sandbox-image/agent/ ./agent/
RUN uv sync --no-dev \
    && useradd --create-home --shell /usr/sbin/nologin sandbox \
    && chown -R sandbox:sandbox /opt/agent /opt/shared /data/mcp-auth /tmp/mcp-orgs

USER sandbox

EXPOSE 9320

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9320/health')" || exit 1

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "9320"]
