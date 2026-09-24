"""Shared MCP sandbox host-CLI tool contract (parse / validate / merge).

Operators declare extra CLI binaries an MCP stdio server shells out to via the
``MCP_HOST_TOOLS`` env var on the server registration. The sandbox agent installs
each declared tool into writable tmpfs before spawning the server.

Format (whitespace/comma separated entries):

    semgrep              → pip manager (``uv tool install semgrep``)
    pip:semgrep          → explicit pip/uv manager
    npm:some-cli         → ``npm install -g some-cli``
"""

from __future__ import annotations

import os
import re
from typing import Iterable

MCP_HOST_TOOLS_ENV_KEY = "MCP_HOST_TOOLS"
MCP_HOST_TOOLS_ALLOWLIST_ENV = "MCP_HOST_TOOLS_ALLOWLIST"

HOST_TOOL_MANAGERS = frozenset({"pip", "uv", "npm"})
DEFAULT_HOST_TOOL_MANAGER = "pip"

HOST_TOOL_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@/-]{0,127}$")

# Stable prefixes for classifier matching (gateway + control).
HOST_TOOL_INSTALL_FAILED_PREFIX = "host tool install failed:"
HOST_TOOL_MANAGER_DENIED_PREFIX = "host tool manager"
HOST_TOOL_ALLOWLIST_DENIED_PREFIX = "host tool not in allowlist:"
# Stable raw-text markers for classifiers (gateway + control + sandbox agent).
MCP_PROTOCOL_HANDSHAKE_FAILED_MARKER = "mcp_protocol_handshake_failed"
HOST_CLI_TOOLS_INSTALLED_MARKER = "host_cli_tools_installed"


class HostToolValidationError(ValueError):
    """Invalid MCP_HOST_TOOLS entry."""


def _normalize_entry(entry: str | dict) -> tuple[str, str]:
    """Normalize a catalog entry or spec token to (manager, package)."""
    if isinstance(entry, dict):
        manager = str(entry.get("manager") or DEFAULT_HOST_TOOL_MANAGER).strip().lower()
        package = str(entry.get("package") or entry.get("name") or "").strip()
    else:
        raw = str(entry).strip()
        if not raw:
            raise HostToolValidationError("empty host tool entry")
        manager, sep, package = raw.partition(":")
        if not sep:
            manager, package = DEFAULT_HOST_TOOL_MANAGER, raw
        else:
            manager = manager.strip().lower()
            package = package.strip()
    if manager not in HOST_TOOL_MANAGERS:
        raise HostToolValidationError(
            f"host tool manager '{manager}' not allowed (use pip, uv, or npm)"
        )
    if not package or not HOST_TOOL_PKG_RE.match(package):
        raise HostToolValidationError(f"invalid host tool package name: {package!r}")
    return manager, package


def parse_host_tools_spec(spec: str) -> list[tuple[str, str]]:
    """Parse ``MCP_HOST_TOOLS`` string into [(manager, package), ...]."""
    text = (spec or "").strip()
    if not text:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for token in re.split(r"[,\s]+", text):
        token = token.strip()
        if not token:
            continue
        pair = _normalize_entry(token)
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def format_host_tools_spec(entries: Iterable[str | dict]) -> str:
    """Format catalog entries into an ``MCP_HOST_TOOLS`` string."""
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not entry:
            continue
        manager, package = _normalize_entry(entry)
        pair = (manager, package)
        if pair in seen:
            continue
        seen.add(pair)
        if manager == DEFAULT_HOST_TOOL_MANAGER:
            parts.append(package)
        else:
            parts.append(f"{manager}:{package}")
    return " ".join(parts)


def validate_host_tools_spec(spec: str) -> None:
    """Raise ``HostToolValidationError`` if *spec* is invalid."""
    parse_host_tools_spec(spec)


def parse_host_tools_allowlist(raw: str | None = None) -> frozenset[str]:
    """Package names permitted when ``MCP_HOST_TOOLS_ALLOWLIST`` is set."""
    text = (raw if raw is not None else os.environ.get(MCP_HOST_TOOLS_ALLOWLIST_ENV, "")).strip()
    if not text:
        return frozenset()
    names: set[str] = set()
    for token in re.split(r"[,;\s]+", text):
        token = token.strip()
        if not token:
            continue
        if not HOST_TOOL_PKG_RE.match(token):
            raise HostToolValidationError(f"invalid host tool allowlist entry: {token!r}")
        names.add(token)
    return frozenset(names)


def assert_host_tool_allowed(package: str, *, allowlist: frozenset[str] | None = None) -> None:
    """Fail closed when broker allowlist is configured and *package* is not listed."""
    allowed = parse_host_tools_allowlist() if allowlist is None else allowlist
    if allowed and package not in allowed:
        raise HostToolValidationError(
            f"{HOST_TOOL_ALLOWLIST_DENIED_PREFIX} '{package}'"
        )


def merge_host_tools_into_env(
    env_vars: dict[str, str] | None,
    host_tools: Iterable[str | dict] | None,
    *,
    manual_spec: str | None = None,
) -> dict[str, str]:
    """Merge catalog ``host_tools`` into env_vars as ``MCP_HOST_TOOLS``.

  Manual ``env_vars[MCP_HOST_TOOLS]`` wins over catalog entries when both are set.
    """
    merged = dict(env_vars or {})
    catalog_spec = format_host_tools_spec(host_tools or [])
    manual = (manual_spec if manual_spec is not None else merged.get(MCP_HOST_TOOLS_ENV_KEY) or "").strip()

    if catalog_spec and manual:
        # Manual wins: keep manual spec, but validate both.
        validate_host_tools_spec(manual)
        validate_host_tools_spec(catalog_spec)
        merged[MCP_HOST_TOOLS_ENV_KEY] = manual
    elif manual:
        validate_host_tools_spec(manual)
        merged[MCP_HOST_TOOLS_ENV_KEY] = manual
    elif catalog_spec:
        merged[MCP_HOST_TOOLS_ENV_KEY] = catalog_spec
    return merged


def host_tool_install_cmd(manager: str, package: str) -> list[str]:
    """Return argv for installing *package* with *manager* (no shell)."""
    if manager in ("pip", "uv"):
        return ["uv", "tool", "install", "--quiet", package]
    if manager == "npm":
        return ["npm", "install", "-g", "--no-audit", "--no-fund", package]
    raise HostToolValidationError(f"host tool manager '{manager}' not allowed")
