"""The MCP-only boot dependencies (GW17). ``edge/routes.py`` imports this without a cycle."""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.dispatch.mcp_broker import BrokerClient
from gateway_v2.dispatch.url_guard import Resolver
from gateway_v2.edge.mcp_registry import McpRegistry, OrgMap

__all__ = ("McpDeps",)


@dataclass(frozen=True, slots=True)
class McpDeps:
    """Built once at boot. ``McpRoute`` adds the booted chat route."""

    registry: McpRegistry
    broker: BrokerClient
    org_map: OrgMap
    dns: Resolver
    allow_hosts: frozenset[str]
    stdio_allowed: frozenset[str]
