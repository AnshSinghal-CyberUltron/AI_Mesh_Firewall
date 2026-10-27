"""DEV PROFILE wiring for the GW17 MCP surface (task 7.2).

MCP is wired only when all five inputs are set:

* ``AMF_DEV_MCP_BROKER_URL`` and ``AMF_DEV_MCP_BROKER_KEY``: the per-org sandbox broker.
* ``AMF_DEV_CONTROL_URL`` and ``AMF_DEV_GATEWAY_INTERNAL_KEY``: control's server registry.
* ``AMF_DEV_MCP_ORG_MAP``: ``v2org=controlslug,...``.

None set means MCP stays the 501 placeholder. Some but not all set refuses to boot, so a
typo cannot leave an operator believing MCP is configured. Secrets never reach the log.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from gateway_v2.dispatch.mcp_broker import BrokerClient, httpx_transport
from gateway_v2.dispatch.url_guard import default_resolver, parse_allow_hosts
from gateway_v2.edge.dev_components import DevProfileError, env_text
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.mcp_registry import ControlTransport, McpRegistry, Mget, OrgMap
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("build_dev_mcp",)

_REQUIRED = (
    "AMF_DEV_MCP_BROKER_URL",
    "AMF_DEV_MCP_BROKER_KEY",
    "AMF_DEV_CONTROL_URL",
    "AMF_DEV_GATEWAY_INTERNAL_KEY",
    "AMF_DEV_MCP_ORG_MAP",
)
_DEFAULT_STDIO = "npx,node,python,python3,uvx,uv"


def build_dev_mcp(
    env: Mapping[str, str],
    contract: ResourceContract,
    *,
    redis_client: Any | None,
    clock: Any,
) -> tuple[McpDeps | None, str]:
    """``(deps, boot log line)``. ``deps`` is ``None`` when MCP is not configured."""
    values = {name: env_text(env, name) for name in _REQUIRED}
    present = [name for name, value in values.items() if value is not None]
    if not present:
        return None, "boot: mcp -> 501 placeholder (AMF_DEV_MCP_* not set)"
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise DevProfileError(f"MCP is partly configured; also set {', '.join(missing)}")
    for name in ("AMF_DEV_MCP_BROKER_URL", "AMF_DEV_CONTROL_URL"):
        if urlsplit(values[name] or "").scheme not in ("http", "https"):
            raise DevProfileError(f"{name} must be an http(s) URL")
    try:
        org_map = OrgMap.parse(values["AMF_DEV_MCP_ORG_MAP"] or "")
    except ValueError as exc:
        raise DevProfileError("AMF_DEV_MCP_ORG_MAP is not a valid v2org=slug list") from exc
    bounds = contract.mcp_bounds()
    deps = McpDeps(
        registry=McpRegistry(
            base_url=values["AMF_DEV_CONTROL_URL"] or "",
            internal_key=values["AMF_DEV_GATEWAY_INTERNAL_KEY"] or "",
            transport=_control_transport(),
            ttl_s=bounds.registry_ttl_s,
            timeout_s=bounds.registry_timeout_s,
            clock=clock,
            mget=_mget(redis_client) if redis_client is not None else None,
            poll_s=bounds.registry_poll_s,
        ),
        broker=BrokerClient(
            base_url=values["AMF_DEV_MCP_BROKER_URL"] or "",
            key=values["AMF_DEV_MCP_BROKER_KEY"] or "",
            bounds=bounds,
            transport=httpx_transport(),
            rng=random.Random(),
            sleep=_sleep,
        ),
        org_map=org_map,
        dns=default_resolver,
        allow_hosts=parse_allow_hosts(env_text(env, "AMF_DEV_MCP_ALLOW_INTERNAL_HOSTS") or ""),
        stdio_allowed=parse_allow_hosts(
            env_text(env, "AMF_DEV_MCP_STDIO_ALLOWED_COMMANDS") or _DEFAULT_STDIO
        ),
    )
    poll = "polled off the request path" if redis_client is not None else "TTL only, no redis"
    log = (
        f"boot: mcp -> POST /v1/mcp/{{server}} via broker "
        f"{_redact(values['AMF_DEV_MCP_BROKER_URL'] or '')}, registry "
        f"{_redact(values['AMF_DEV_CONTROL_URL'] or '')} (scan_ver {poll}), "
        f"{len(present)} inputs set; keys redacted"
    )
    return deps, log


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _control_transport() -> ControlTransport:
    async def get(
        url: str, headers: Mapping[str, str], query: Mapping[str, str], timeout_s: float
    ) -> tuple[int, bytes]:
        import httpx

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            response = await client.get(url, headers=dict(headers), params=dict(query))
            return response.status_code, response.content

    return get


def _mget(client: Any) -> Mget:
    async def mget(keys: Sequence[str]) -> Sequence[object]:
        values = await client.mget(list(keys))
        return list(values)

    return mget


def _redact(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
