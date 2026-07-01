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
) -> httpx.Response:
    last_response: httpx.Response | None = None
    for attempt in range(1, _RETRY_MAX + 1):
        try:
            response = await client.request(method, url, json=json, headers=_auth_headers())
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
    """Ensure the broker has a running sandbox container for *org_slug*."""
    url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/ensure"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _request_with_503_retry(
            client,
            "POST",
            url,
            json={"warm": True},
        )
    _raise_for_broker_error(response)
    return response.json()


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
