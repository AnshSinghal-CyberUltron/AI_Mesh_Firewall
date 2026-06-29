"""Shared stdio MCP security constants and child-env construction.

Used by gateway ``mcp_stdio_adapter`` and sandbox-agent ``stdio_manager`` so
denylist and OAuth heuristics stay identical across in-process and per-org
sandbox paths.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

LOG = logging.getLogger("ai_mesh_shared.mcp_stdio_common")

# Gateway-internal secrets that must NEVER be inherited by a spawned MCP
# subprocess. A poisoned npm package would otherwise read the gateway↔control
# shared key and impersonate the backend for OTHER orgs.
_SECRET_ENV_DENYLIST = {
    "GATEWAY_INTERNAL_API_KEY",
    "AGENT_API_KEY",
    "BACKEND_URL",
    "AIGUARDX_BACKEND_URL",
    "AI_MESH_CONTROL_URL",
    "MCP_FIREWALL_URL",
    "SECURE_MCP_GATEWAY_URL",
    "SECRET_KEY",
    "DJANGO_SECRET_KEY",
    "FIELD_ENCRYPTION_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "POSTGRES_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "PYTHONPATH",
}

# Substrings that, when seen on a child's stdout/stderr during startup, signal
# the server is trying to run an INTERACTIVE OAuth/login flow that cannot
# complete headless.
_OAUTH_HINT_SUBSTRINGS = (
    "please visit",
    "open the following url",
    "open this url",
    "authorize this app",
    "authorization required",
    "to authenticate",
    "log in to your",
    "visit the following",
    "press any key to open",
    "waiting for authentication",
    "sign in to continue",
    "please authorize",
    "by visiting",
    "authentication required",
    "waiting for authorization",
    "browser opened automatically",
    "oauth callback server running",
)

# mcp-remote stderr when a Bearer token is already injected — informational only.
_MCP_REMOTE_HEADLESS_OAUTH_INFO = (
    "discovering oauth server configuration",
    "discovered authorization server",
    "using custom headers",
    "connecting to remote server",
    "connected to remote server",
    "proxy established successfully",
    "local stdio server running",
    "using transport strategy",
    "using automatically selected callback port",
    "press ctrl+c to exit",
)

# Host env vars that are safe (and sometimes necessary) to pass through so
# npx/node/python/uvx can resolve binaries, TLS certs, locale and the shared
# on-demand package caches (mounted as docker volumes for warm reuse).
_SAFE_ENV_PASSTHROUGH = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "NODE_PATH",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NPM_CONFIG_CACHE",
    "NPM_CONFIG_PREFIX",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "XDG_CACHE_HOME",
}


def _args_have_oauth_header(args: list[str]) -> bool:
    for idx, arg in enumerate(args):
        if arg == "--header" and idx + 1 < len(args):
            if args[idx + 1].lower().startswith("authorization:"):
                return True
    return False


def _looks_like_oauth_prompt(text: str, *, oauth_header_injected: bool = False) -> bool:
    """Heuristic: does this child output indicate an interactive login flow?"""
    t = text.lower()
    if oauth_header_injected and any(info in t for info in _MCP_REMOTE_HEADLESS_OAUTH_INFO):
        return False
    if not any(h in t for h in _OAUTH_HINT_SUBSTRINGS):
        return False
    return "http://" in t or "https://" in t or "authenticat" in t or "authoriz" in t


def _build_child_env(
    env: dict[str, str] | None,
    org_slug: str,
    *,
    host_environ: Mapping[str, str] | None = None,
    remote_config_dir: str | None = None,
    log: logging.Logger | None = None,
) -> dict[str, str]:
    """Construct a sandboxed environment for a spawned MCP subprocess.

    Allowlist host vars (never gateway secrets) + per-org BYOK env, then pin a
    per-org ``MCP_REMOTE_CONFIG_DIR`` so OAuth tokens cannot leak across orgs.
    """
    host = host_environ if host_environ is not None else os.environ
    logger = log or LOG
    child: dict[str, str] = {k: host[k] for k in _SAFE_ENV_PASSTHROUGH if k in host}
    for k, v in (env or {}).items():
        if k in _SECRET_ENV_DENYLIST:
            logger.warning("Refusing to pass denylisted env var %s to stdio child", k)
            continue
        child[k] = v
    for dangerous in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
        child.pop(dangerous, None)
    for secret in _SECRET_ENV_DENYLIST:
        child.pop(secret, None)
    child["MCP_REMOTE_CONFIG_DIR"] = (
        remote_config_dir or f"/tmp/mcp-orgs/{org_slug}/mcp-auth"
    )
    return child
