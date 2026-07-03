"""Gateway-side MCP failure classifier — clean, non-revealing client errors.

The gateway proxies MCP tool discovery/calls to per-org sandboxes and (for the
external passthrough) to tenant-configured upstream MCP hosts. When any of those
fail, the RAW cause — an exit code, an upstream HTML error page, a connection
exception carrying an internal hostname, a stderr tail, "sandbox-agent logs" —
must NEVER reach the client. This module maps every failure to:

    (stable client error CODE, clean branded MESSAGE, correlation REF)

The client sees only {error: message, code, ref}. The REAL cause is kept
server-side: a structured WARNING log keyed by ``ref`` AND a best-effort write to
the shared Redis diagnostic channel ``mcp:diag:<ref>`` (same key convention as the
control plane's ``_store_sync_diagnostic``/``MCPDiagnosticDetailView``), so a
developer/staff user can retrieve the full cause by ref without it ever egressing.

Codes mirror ``control/ai_mesh_control/mcp_connector/views.py`` so a client sees a
consistent code regardless of which service produced the error, plus three the
gateway needs at the transport boundary (DNS, connection-refused, upstream-HTTP).
"""
from __future__ import annotations

import logging
import os
import re
import uuid

import httpx

LOG = logging.getLogger("gateway.mcp_error")

# ── client-facing error codes (mirror control D-codes + transport additions) ──
MCP_AUTH_FAILED = "MCP_AUTH_FAILED"
MCP_EGRESS_DENIED = "MCP_EGRESS_DENIED"
MCP_OUT_OF_MEMORY = "MCP_OUT_OF_MEMORY"
MCP_INSUFFICIENT_STORAGE = "MCP_INSUFFICIENT_STORAGE"
MCP_SERVER_CRASHED = "MCP_SERVER_CRASHED"
MCP_IMAGE_UNAVAILABLE = "MCP_IMAGE_UNAVAILABLE"
MCP_TIMEOUT = "MCP_TIMEOUT"
MCP_START_FAILED = "MCP_START_FAILED"
MCP_DNS_FAILURE = "MCP_DNS_FAILURE"                 # gateway transport addition
MCP_CONNECTION_REFUSED = "MCP_CONNECTION_REFUSED"   # gateway transport addition
MCP_UPSTREAM_HTTP_ERROR = "MCP_UPSTREAM_HTTP_ERROR" # gateway transport addition
MCP_UNAVAILABLE = "MCP_UNAVAILABLE"

_DIAG_PREFIX = "mcp:diag:"
_DIAG_TTL_SECONDS = 7 * 24 * 3600
_DIAG_RAW_MAX = 4000
_REDIS_URL = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")


def _classify_exit_code(exit_code: int) -> tuple[str, str] | None:
    """Process exit code / signal → (code, message). None if not a recognised exit.

    Convention: a negative value is ``-signal`` (POSIX ``returncode``); a container
    exit is ``128 + signal``. OOM = SIGKILL (9), crash = SIGABRT (6) / SIGSEGV (11).
    """
    if exit_code is None:
        return None
    sig = -exit_code if exit_code < 0 else (exit_code - 128 if exit_code > 128 else None)
    if exit_code in (-9, 137) or sig == 9:
        return MCP_OUT_OF_MEMORY, ("The MCP server exceeded its memory limit and was "
                                   "stopped. Try a lighter configuration or contact support.")
    if exit_code in (-6, 134, -11, 139) or sig in (6, 11):
        return MCP_SERVER_CRASHED, ("The MCP server crashed while starting. Verify the "
                                    "command and package are compatible, then retry.")
    if exit_code != 0:
        return MCP_START_FAILED, ("The MCP server could not be started. Verify the "
                                  "command and package name, then retry.")
    return None


def _classify_http_status(status: int) -> tuple[str, str]:
    """Upstream HTTP status → (code, message). Echoes the numeric status but NEVER
    the response body/HTML."""
    if status in (401, 403):
        return MCP_AUTH_FAILED, ("The MCP server needs re-authentication. Re-authorize "
                                 "the connection, then retry.")
    if status == 405:
        return MCP_UPSTREAM_HTTP_ERROR, ("The server returned an error page (HTTP 405) — "
                                         "check the endpoint URL.")
    if 400 <= status < 500:
        return MCP_UPSTREAM_HTTP_ERROR, (f"The server returned an error page (HTTP {status}) — "
                                         "check the endpoint URL.")
    if 500 <= status < 600:
        return MCP_UPSTREAM_HTTP_ERROR, (f"The MCP server returned a server error (HTTP {status}). "
                                         "Please retry shortly.")
    return MCP_UNAVAILABLE, ("The MCP server returned an unexpected response. Verify the "
                             "configuration and retry.")


_DNS_HINTS = ("getaddrinfo", "name or service not known", "nodename nor servname",
              "temporary failure in name resolution", "name resolution", "no address associated",
              "[errno -2]", "[errno -3]", "[errno 8]", "could not resolve")
_REFUSED_HINTS = ("connection refused", "econnrefused", "[errno 111]", "[errno 61]",
                  "actively refused")
_TIMEOUT_HINTS = ("timed out", "timeout", "deadline exceeded")


def _classify_exception(exc: BaseException) -> tuple[str, str]:
    """Transport exception → (code, message). Distinguishes DNS vs refused vs timeout
    from the message text so the client gets an actionable hint WITHOUT the raw cause
    or any internal hostname."""
    s = str(exc).lower()
    if isinstance(exc, httpx.TimeoutException) or any(h in s for h in _TIMEOUT_HINTS):
        return MCP_TIMEOUT, "The MCP server did not respond in time. Please retry in a moment."
    if any(h in s for h in _DNS_HINTS):
        return MCP_DNS_FAILURE, "Could not reach the server — check the URL/host."
    if any(h in s for h in _REFUSED_HINTS):
        return MCP_CONNECTION_REFUSED, ("Could not reach the server — the connection was "
                                        "refused. Check the host and port.")
    if isinstance(exc, httpx.ConnectError):
        # a ConnectError without a clearer hint is still an unreachable host
        return MCP_DNS_FAILURE, "Could not reach the server — check the URL/host."
    return MCP_UNAVAILABLE, ("The MCP server could not be reached. Verify the "
                             "configuration and retry.")


# Raw-text fingerprints for causes that arrive as a string (stderr tail, agent
# reason, JSON-RPC error message). Mirrors control's `_classify_sync_error` order.
def _classify_raw_text(low: str) -> tuple[str, str]:
    if any(k in low for k in ("re-authenticate", "re-authentication", "unauthorized",
                              "invalid token", "invalid_token", "forbidden", " 401", " 403")):
        return MCP_AUTH_FAILED, ("The MCP server needs re-authentication. Re-authorize the "
                                 "connection, then retry.")
    if any(k in low for k in ("egress denied", "allowlist", "not permitted by", "egress policy")):
        return MCP_EGRESS_DENIED, ("The MCP server host is not permitted by your "
                                   "organization's egress policy.")
    if any(k in low for k in ("no space left", "enospc", "storage limit", "exceeded the sandbox storage")):
        return MCP_INSUFFICIENT_STORAGE, ("The MCP server is too large to install within the "
                                          "current sandbox storage limit. Use a lighter server "
                                          "or raise the limit, then retry.")
    if any(k in low for k in ("out of memory", "oom", "code -9", "signal 9", "sigkill",
                              "code 137", "exit 137")):
        return MCP_OUT_OF_MEMORY, ("The MCP server exceeded its memory limit. Try a lighter "
                                   "configuration or contact support.")
    if any(k in low for k in ("code -6", "signal 6", "sigabrt", "code -11", "signal 11",
                              "sigsegv", "code 134", "code 139", "core dumped", "aborted")):
        return MCP_SERVER_CRASHED, ("The MCP server crashed while starting. Verify the command "
                                    "and package are compatible, then retry.")
    if any(k in low for k in ("no such image", "image not found", "manifest unknown",
                              "pull access denied", "unable to find image")):
        return MCP_IMAGE_UNAVAILABLE, ("The MCP server's runtime image is not available. "
                                       "Please retry shortly or contact support.")
    if any(h in low for h in _TIMEOUT_HINTS) or any(k in low for k in ("did not respond",
                              "not ready", "provisioning", "starting up")):
        return MCP_TIMEOUT, "The MCP server did not respond in time. Please retry in a moment."
    if any(h in low for h in _DNS_HINTS):
        return MCP_DNS_FAILURE, "Could not reach the server — check the URL/host."
    if any(h in low for h in _REFUSED_HINTS):
        return MCP_CONNECTION_REFUSED, ("Could not reach the server — the connection was "
                                        "refused. Check the host and port.")
    if any(k in low for k in ("exited with code", "failed to start", "process exited",
                              "missing host dependency", "did not start", "stdout stream closed")):
        return MCP_START_FAILED, ("The MCP server could not be started. Verify the command "
                                  "and package name, then retry.")
    return MCP_UNAVAILABLE, ("The MCP server could not be reached or returned an error. "
                             "Verify the configuration and retry.")


def classify_mcp_failure(*, exc: BaseException | None = None, status: int | None = None,
                         exit_code: int | None = None, raw: str = "") -> tuple[str, str]:
    """Map a raw MCP failure to a stable (client CODE, clean MESSAGE).

    Priority: process exit code → HTTP status → transport exception → raw-text
    fingerprint. NEVER returns any exit code, HTTP body, hostname, or stderr in the
    message — only the branded summary.
    """
    if exit_code is not None:
        hit = _classify_exit_code(exit_code)
        if hit:
            return hit
    if status is not None and status >= 400:
        return _classify_http_status(status)
    if exc is not None:
        return _classify_exception(exc)
    return _classify_raw_text((raw or "").lower())


def mint_ref() -> str:
    return uuid.uuid4().hex[:12]


def _raw_cause(*, exc, status, exit_code, raw) -> str:
    parts = []
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if status is not None:
        parts.append(f"http={status}")
    if exc is not None:
        parts.append(f"exc={type(exc).__name__}: {exc}")
    if raw:
        parts.append(f"raw={raw}")
    return " | ".join(parts)[:_DIAG_RAW_MAX]


async def _persist_diagnostic(ref: str, code: str, cause: str, *, org_slug: str, server_slug: str) -> None:
    """Best-effort write of the REAL cause to the shared Redis diagnostic channel,
    keyed by ref (mcp:diag:<ref>, 7d TTL). A Redis outage must NEVER break the error
    path — the structured log already carries the cause for developers."""
    try:
        import json

        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await client.set(
            f"{_DIAG_PREFIX}{ref}",
            json.dumps({"ref": ref, "code": code, "kind": "mcp_gateway_error",
                        "org_slug": org_slug, "server_slug": server_slug, "raw_cause": cause}),
            ex=_DIAG_TTL_SECONDS,
        )
        await client.aclose()
    except Exception as e:  # noqa: BLE001
        LOG.debug("mcp diag persist failed [ref=%s]: %s", ref, e)


async def sanitize_mcp_error(*, exc: BaseException | None = None, status: int | None = None,
                             exit_code: int | None = None, raw: str = "",
                             org_slug: str = "", server_slug: str = "") -> dict:
    """Classify + mint a ref + record the raw cause dev-only, and return the
    client-safe body ``{"error", "code", "ref"}``. The raw cause goes to a
    structured WARNING log AND the Redis diagnostic channel — never to the client.
    """
    code, message = classify_mcp_failure(exc=exc, status=status, exit_code=exit_code, raw=raw)
    ref = mint_ref()
    cause = _raw_cause(exc=exc, status=status, exit_code=exit_code, raw=raw)
    LOG.warning("MCP error sanitized [ref=%s code=%s org=%s server=%s]: %s",
                ref, code, org_slug or "-", server_slug or "-", cause)
    await _persist_diagnostic(ref, code, cause, org_slug=org_slug, server_slug=server_slug)
    return {"error": message, "code": code, "ref": ref}


def sanitize_mcp_error_sync(*, exc: BaseException | None = None, status: int | None = None,
                            exit_code: int | None = None, raw: str = "",
                            org_slug: str = "", server_slug: str = "") -> dict:
    """Sync variant for non-async call sites: classify + ref + structured log
    (no Redis write). Returns the client-safe ``{"error", "code", "ref"}``."""
    code, message = classify_mcp_failure(exc=exc, status=status, exit_code=exit_code, raw=raw)
    ref = mint_ref()
    LOG.warning("MCP error sanitized [ref=%s code=%s org=%s server=%s]: %s", ref, code,
                org_slug or "-", server_slug or "-",
                _raw_cause(exc=exc, status=status, exit_code=exit_code, raw=raw))
    return {"error": message, "code": code, "ref": ref}
