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

# websocket: @modelcontextprotocol/server-everything has no ws mode — use Cursor ws stub.
upsert_ws() {
  local name="$1" port="$2" alias="$3"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --network "$NET" \
    --network-alias "$alias" \
    -e "PORT=${port}" \
    -v "${REPO_ROOT}/scripts/mcp_ws_everything_stub.mjs:/stub.mjs:ro" \
    "$IMAGE" \
    sh -c "cd /tmp && npm_config_cache=/tmp/npm npm init -y >/dev/null 2>&1 && npm install ws@8 >/dev/null 2>&1 && cp /stub.mjs /tmp/stub.mjs && NODE_PATH=/tmp/node_modules node /tmp/stub.mjs" >/dev/null
  echo "started ${name} (websocket stub) alias=${alias} on ${NET}:${port}"
}

upsert_ws ws-everything 3003 ws-everything.stub

sleep 6
docker logs --tail 2 http-everything 2>/dev/null || true
docker logs --tail 2 sse-everything 2>/dev/null || true
docker logs --tail 2 ws-everything 2>/dev/null || true
