#!/usr/bin/env bash
# Ensure mcp_sandbox_net_<org> exists before `docker compose --profile transport-stubs up`.
# The broker creates per-org sandbox networks at runtime; this script pre-creates the
# zeroshield network so compose-managed transport stubs can attach with DNS aliases.
set -euo pipefail
NET="${MCP_STUB_NETWORK:-mcp_sandbox_net_zeroshield}"
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  echo "Creating $NET network..."
  docker network create "$NET"
else
  echo "$NET network already exists"
fi
