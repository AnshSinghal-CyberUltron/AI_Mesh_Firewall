#!/usr/bin/env bash
# Start in-cluster MCP transport stubs on the zeroshield sandbox network (Cursor-owned).
# Used by P4.13/P6.18 four-transport verification — NOT committed secrets.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NET="${MCP_STUB_NETWORK:-mcp_sandbox_net_zeroshield}"
IMAGE="${MCP_STUB_NODE_IMAGE:-node:20-slim}"

upsert() {
  local name="$1" port="$2" transport="$3" alias="$4"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --network "$NET" \
    --network-alias "$alias" \
    -e "PORT=${port}" \
    "$IMAGE" \
    sh -c "npm_config_cache=/tmp/npm npx -y @modelcontextprotocol/server-everything ${transport}" >/dev/null
  echo "started ${name} (${transport}) alias=${alias} on ${NET}:${port}"
}

upsert http-everything 3001 streamableHttp http-everything.stub
upsert sse-everything 3002 sse sse-everything.stub

# websocket: Everything server has no ws mode — ws-everything remains Claude-owned (broker path).
if docker ps -a --format '{{.Names}}' | grep -qx ws-everything; then
  echo "ws-everything already present (skip)"
else
  echo "NOTE: no ws-everything stub — websocket e2e blocked until Claude routes ws via broker_send_rpc + ws stub."
fi

sleep 6
docker logs --tail 2 http-everything 2>/dev/null || true
docker logs --tail 2 sse-everything 2>/dev/null || true
