"""Shared stdio MCP security constants and child-env construction.

Used by gateway ``mcp_stdio_adapter`` and sandbox-agent ``stdio_manager`` so
denylist and OAuth heuristics stay identical across in-process and per-org
sandbox paths.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

LOG = logging.getLogger("ai_mesh_shared.mcp_stdio_common")

# Gateway-internal secrets that must NEVER be inherited by a spawned MCP
# subprocess. A poisoned npm package would otherwise read the gateway↔control
# shared key and impersonate the backend for OTHER orgs.
_SECRET_ENV_DENYLIST = {
    "GATEWAY_INTERNAL_API_KEY",
    "MCP_BROKER_INTERNAL_KEY",
    "MCP_BROKER_URL",
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
    # uvx installs tools to UV_TOOL_DIR + links bins into UV_TOOL_BIN_DIR; both
    # default to the read-only rootfs (~/.local/...), so uvx/Python MCP servers
    # (Fetch, semgrep-mcp) failed to start until the container points them at a
    # writable tmpfs AND they are passed through to the spawned child. (CP36)
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "XDG_CACHE_HOME",
    # The container sets NODE_OPTIONS=--max-old-space-size (the per-org graceful-OOM
    # heap cap, CP20). Since this child env is rebuilt FRESH (not inherited), it must
    # be passed through or the actual Node MCP server runs WITHOUT the heap cap. (CP36)
    "NODE_OPTIONS",
}


def _args_have_oauth_header(args: list[str]) -> bool:
    for idx, arg in enumerate(args):
        if arg == "--header" and idx + 1 < len(args):
            if args[idx + 1].lower().startswith("authorization:"):
                return True
    return False


# CHG-0053 / CHG-0107: stdio server args are logged for debuggability, but a
# configured credential must NOT land in operator logs in plaintext. Two vectors:
#   (1) a secret-looking FLAG value  (``--token XYZ`` / ``--api-key=XYZ``)
#   (2) a credential embedded in a URL passed as a STANDALONE arg
#       (``postgres://u:pw@h/db`` · ``https://x:ghp_..@github`` · ``?api_key=..``)
# CHG-0053 (sandbox agent only) masked (1); (2) still egressed raw, and the gateway
# adapter masked NEITHER. This shared helper covers both, for both consumers.
# (The normal secret location is ``env``, which is never logged; tool-call
# results/params are never logged either — this hardens the arg edge case.)
_SECRET_ARG_HINTS = (
    "token", "key", "secret", "password", "passwd", "auth", "credential", "apikey",
)


def _redact_url_creds(s: str) -> str:
    """Mask credentials embedded in a URL arg. Non-URL args return unchanged.

    Masks (a) the WHOLE userinfo (``scheme://user:pass@host`` → ``scheme://***@host``
    — the whole userinfo because a token can sit in the user OR password position,
    e.g. ``https://ghp_x@h`` or ``https://x-access-token:ghp_x@h``) and (b) the
    values of any query param whose name matches a secret hint
    (``?api_key=v&token=v&page=2`` → ``?api_key=***&token=***&page=2``). Never
    raises — on any parse hiccup it returns the string unchanged (fail-safe;
    logging must not crash the spawn path).
    """
    if "://" not in s:
        return s
    try:
        parts = urlsplit(s)
    except Exception:
        return s
    if not parts.scheme or not parts.netloc:
        return s
    changed = False
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
        changed = True
    query = parts.query
    if query:
        pairs = []
        for kv in query.split("&"):
            k, sep, v = kv.partition("=")
            if sep and v and any(h in k.lower() for h in _SECRET_ARG_HINTS):
                pairs.append(k + "=***")
                changed = True
            else:
                pairs.append(kv)
        query = "&".join(pairs)
    if not changed:
        return s
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _safe_args_for_log(args: list[str]) -> list[str]:
    """Redact secrets from stdio spawn args before logging (see notes above).

    Masks secret-looking flag values AND URL-embedded credentials in every arg
    (standalone URLs and non-secret ``--flag=URL`` inline values). Benign
    package specs / flags / plain URLs pass through unchanged.
    """
    out: list[str] = []
    mask_next = False
    for a in args:
        s = str(a)
        if mask_next:
            out.append("***")
            mask_next = False
            continue
        low = s.lower()
        if s.startswith("-") and any(h in low for h in _SECRET_ARG_HINTS):
            if "=" in s:
                out.append(s.split("=", 1)[0] + "=***")
            else:
                out.append(s)       # keep the flag name itself
                mask_next = True     # ...but mask the following value
        elif s.startswith("-") and "=" in s:
            # A non-secret flag with an inline value (``--dsn=postgres://u:pw@h``):
            # the value may still be a URL carrying credentials — redact just it.
            k, _, v = s.partition("=")
            out.append(k + "=" + _redact_url_creds(v))
        else:
            out.append(_redact_url_creds(s))
    return out


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
    # BACKSTOP CHG-0044 (item 8, supply-chain RCE): FORCE npm/npx install lifecycle
    # scripts OFF for every spawned stdio child — unconditionally and LAST, so a
    # malicious server-spec `env` cannot re-enable them. The container sets
    # ``npm_config_ignore_scripts=true`` (docker_manager), but this child env is
    # rebuilt FRESH from ``_SAFE_ENV_PASSTHROUGH`` (which omits it) and REPLACES the
    # process environment (``create_subprocess_exec(env=...)`` does not inherit the
    # parent) — so without this the ``npx`` child ran with ignore-scripts defaulting
    # to FALSE and an untrusted package's preinstall/install/postinstall executed on
    # fetch (the exact supply-chain vector the container-level flag claims to kill).
    # Pinned here == pinned for the child that actually fetches untrusted packages.
    child["npm_config_ignore_scripts"] = "true"
    child["MCP_REMOTE_CONFIG_DIR"] = (
        remote_config_dir or f"/tmp/mcp-orgs/{org_slug}/mcp-auth"
    )
    return child
