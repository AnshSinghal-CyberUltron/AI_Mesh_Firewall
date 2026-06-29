#!/usr/bin/env bash
# Ensure mcp_sandbox_bridge exists before `docker compose --profile services up`.
# Compose declares this network as external to avoid label conflicts when tests
# or manual broker runs created it first.
set -euo pipefail
if ! docker network inspect mcp_sandbox_bridge >/dev/null 2>&1; then
  echo "Creating mcp_sandbox_bridge network..."
  docker network create mcp_sandbox_bridge
else
  echo "mcp_sandbox_bridge network already exists"
fi
