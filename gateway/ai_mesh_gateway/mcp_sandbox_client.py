"""HTTP client for MCP sandbox broker delegation.

Wraps broker ensure + stdio/rpc endpoints when ``MCP_STDIO_IN_PROCESS=false``.
Policy, scan, and audit remain in ``mcp_proxy.py`` — this module only talks
to the internal mcp-broker Sandbox Controller.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import random
from typing import Any
from urllib.parse import urlparse

import httpx

LOG = logging.getLogger("gateway.mcp_sandbox_client")

BROKER_KEY_HEADER = "X-MCP-Broker-Key"
_RPC_ID_SEQ = itertools.count(1)

_BROKER_URL = os.environ.get("MCP_BROKER_URL", "http://mcp-broker:8311").rstrip("/")
_INIT_TIMEOUT = float(os.environ.get("MCP_STDIO_INIT_TIMEOUT", "120"))
_METHOD_TIMEOUT = float(os.environ.get("MCP_STDIO_METHOD_TIMEOUT", "60"))
_RETRY_MAX = int(os.environ.get("MCP_SANDBOX_RETRY_MAX", "5"))
_RETRY_BASE = float(os.environ.get("MCP_SANDBOX_RETRY_BASE_DELAY", "0.5"))
_RETRY_MAX_DELAY = float(os.environ.get("MCP_SANDBOX_RETRY_MAX_DELAY", "8.0"))
# B3 #20: how many times to re-poll ensure while the broker still reports the
# sandbox as `provisioning` (agent not yet ready), before giving up the warm.
_ENSURE_READY_MAX = int(os.environ.get("MCP_SANDBOX_ENSURE_READY_MAX", "6"))


def _broker_key() -> str:
    key = os.environ.get("MCP_BROKER_INTERNAL_KEY", "").strip()
    if not key:
        raise RuntimeError("MCP broker is not configured (missing MCP_BROKER_INTERNAL_KEY)")
    return key


def _auth_headers() -> dict[str, str]:
    return {BROKER_KEY_HEADER: _broker_key()}


def _timeouts_payload(timeout: float | None) -> dict[str, float]:
    method_seconds = _METHOD_TIMEOUT if timeout is None else timeout
    return {
        "init_seconds": _INIT_TIMEOUT,
        "method_seconds": method_seconds,
    }


def _http_timeout(timeout: float | None) -> float:
    method_seconds = _METHOD_TIMEOUT if timeout is None else timeout
    return max(_INIT_TIMEOUT, method_seconds) + 10.0


def _safe_broker_error(status_code: int) -> str:
    if status_code == 502:
        return "MCP sandbox is temporarily unavailable"
    if status_code == 503:
        return "MCP sandbox is starting; please retry shortly"
    if status_code == 429:
        return "MCP sandbox capacity exceeded for this organization"
    if status_code == 401:
        return "MCP broker authentication failed"
    return "MCP sandbox request failed"


def _raise_for_broker_error(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    LOG.warning(
        "Broker request failed status=%s url=%s detail=%s",
        response.status_code,
        str(response.request.url),
        response.text[:500],
    )
    raise RuntimeError(_safe_broker_error(response.status_code))


async def _sleep_backoff(attempt: int) -> None:
    delay = min(_RETRY_BASE * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
    jitter = random.uniform(0, delay * 0.25)
    await asyncio.sleep(delay + jitter)


async def _request_with_503_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    last_response: httpx.Response | None = None
    # CHG-0051: broker auth headers + optional out-of-band tracing headers (e.g. the
    # X-Request-ID correlation id), built once and reused across 503 retries.
    _hdrs = _auth_headers()
    if extra_headers:
        _hdrs.update({k: str(v) for k, v in extra_headers.items() if v})
    for attempt in range(1, _RETRY_MAX + 1):
        try:
            response = await client.request(method, url, json=json, headers=_hdrs)
        except httpx.HTTPError as exc:
            LOG.warning("Broker unreachable (attempt %d/%d): %s", attempt, _RETRY_MAX, exc)
            if attempt >= _RETRY_MAX:
                raise RuntimeError("MCP sandbox is temporarily unavailable") from exc
            await _sleep_backoff(attempt)
            continue

        if response.status_code != 503:
            return response

        last_response = response
        LOG.info(
            "Broker returned 503 (attempt %d/%d) for %s — sandbox may be starting",
            attempt,
            _RETRY_MAX,
            url,
        )
        if attempt >= _RETRY_MAX:
            break
        await _sleep_backoff(attempt)

    assert last_response is not None
    return last_response


async def ensure_sandbox(org_slug: str) -> dict[str, Any]:
    """Ensure the broker has a running, READY sandbox container for *org_slug*.

    The broker's warm ensure blocks (bounded) for the in-container agent to bind
    and returns a ``provisioning`` flag (True = still starting). For a slow cold
    start (e.g. first-time ``npx`` fetch) one warm window may not be enough, so we
    re-poll ensure with bounded jittered backoff until the agent is ready or the
    attempt budget is spent — a client-side readiness poll (B3 #19/#20). This is
    best-effort warming; the actual RPC path still retries independently.
    """
    url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/ensure"
    result: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(1, _ENSURE_READY_MAX + 1):
            response = await _request_with_503_retry(client, "POST", url, json={"warm": True})
            _raise_for_broker_error(response)
            result = response.json()
            if not result.get("provisioning"):
                return result  # agent ready (or broker doesn't report provisioning)
            if attempt >= _ENSURE_READY_MAX:
                break
            await _sleep_backoff(attempt)
    return result


async def broker_send_jsonrpc(
    org_slug: str,
    server_config: dict[str, Any],
    method: str,
    params: dict | list | None,
    timeout: float | None = None,
    *,
    msg_id: int | str | None = None,
) -> dict[str, Any]:
    """Forward one JSON-RPC exchange to a stdio MCP server inside the org sandbox."""
    server_slug = server_config.get("server_slug")
    if not server_slug:
        raise ValueError("server_config must include server_slug")

    payload: dict[str, Any] = {
        "server_slug": server_slug,
        "command": server_config["command"],
        "args": list(server_config.get("args") or []),
        "env": dict(server_config.get("env_vars") or server_config.get("env") or {}),
        "method": method,
        "params": params,
        "jsonrpc_id": next(_RPC_ID_SEQ) if msg_id is None else msg_id,
        "timeouts": _timeouts_payload(timeout),
    }

    url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/stdio/rpc"
    async with httpx.AsyncClient(timeout=_http_timeout(timeout)) as client:
        response = await _request_with_503_retry(client, "POST", url, json=payload)

    _raise_for_broker_error(response)
    return response.json()


def _upstream_allowed_hosts(url: str, server_config: dict[str, Any]) -> list[str]:
    """Egress allowlist for a remote-transport server: the upstream host plus any
    explicitly-declared extra hosts (SANDBOX_TRANSPORT_CONTRACT §4). Exact hosts
    only — never a wildcard; the sandbox may connect ONLY to these."""
    hosts: list[str] = []
    parsed = urlparse(url)
    if parsed.hostname:
        hosts.append(parsed.hostname)
    for extra in server_config.get("allowed_hosts") or []:
        h = str(extra).strip()
        if h and h not in hosts:
            hosts.append(h)
    return hosts


async def broker_send_rpc(
    org_slug: str,
    server_config: dict[str, Any],
    method: str,
    params: dict | list | None,
    timeout: float | None = None,
    *,
    msg_id: int | str | None = None,
    oauth_token: str | None = None,
    correlation_id: str = "",
) -> dict[str, Any]:
    """Forward one JSON-RPC exchange to an MCP server inside the org sandbox — for
    ANY transport (stdio, streamable-http, sse, websocket).

    P4.13/P6.18 unified path: the gateway NEVER dials the upstream MCP URL directly.
    For remote transports the gateway builds the ``upstream`` block (url +
    egress-allowlist + injected Bearer) and the per-org sandbox agent does the dial,
    so all egress is org-scoped and allowlisted. ``oauth_token`` (if the gateway
    holds one for this server) is injected as the upstream ``Authorization`` bearer —
    the sandbox never runs the OAuth client itself (``oauth_client_role`` forbidden).
    """
    server_slug = server_config.get("server_slug")
    if not server_slug:
        raise ValueError("server_config must include server_slug")
    transport = (server_config.get("transport") or "stdio").strip().lower()

    payload: dict[str, Any] = {
        "server_slug": server_slug,
        "transport": transport,
        "method": method,
        "params": params,
        "jsonrpc_id": next(_RPC_ID_SEQ) if msg_id is None else msg_id,
        "timeouts": _timeouts_payload(timeout),
    }

    if transport == "stdio":
        payload["command"] = server_config.get("command")
        payload["args"] = list(server_config.get("args") or [])
        payload["env"] = dict(server_config.get("env_vars") or server_config.get("env") or {})
    else:
        upstream_url = server_config.get("url") or server_config.get("upstream_url")
        if not upstream_url:
            raise ValueError(f"{transport} server_config must include a url for the sandbox upstream")
        headers = dict(server_config.get("headers") or {})
        if oauth_token:
            headers["Authorization"] = f"Bearer {oauth_token}"
        payload["upstream"] = {
            "url": upstream_url,
            "allowed_hosts": _upstream_allowed_hosts(upstream_url, server_config),
            "headers": headers,
            "oauth_client_role": "forbidden_in_sandbox",
        }

    url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/rpc"
    # CHG-0051: propagate the per-request correlation id to the broker (and thus the
    # sandbox) as X-Request-ID, so their logs correlate with the gateway MCPEvent
    # audit for the same tool call (end-to-end tracing without OTEL).
    _trace_hdrs = {"X-Request-ID": correlation_id} if correlation_id else None
    async with httpx.AsyncClient(timeout=_http_timeout(timeout)) as client:
        response = await _request_with_503_retry(
            client, "POST", url, json=payload, extra_headers=_trace_hdrs
        )

    _raise_for_broker_error(response)
    return response.json()
