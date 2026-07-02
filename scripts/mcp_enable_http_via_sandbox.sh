#!/usr/bin/env bash
# Enable MCP_HTTP_VIA_SANDBOX on the live gateway WITHOUT editing gateway source.
# Writes local .env keys (gitignored) and recreates gateway + control so SSRF allowlist applies.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
ENV_FILE="${ENV_FILE:-.env}"
INTERNAL_HOSTS="${MCP_ALLOW_INTERNAL_HOSTS:-http-everything.stub:3001,sse-everything.stub:3002,http-everything.stub,sse-everything.stub,mcp-stub:9999,mcp-stub}"

set_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

touch "$ENV_FILE"
set_kv MCP_HTTP_VIA_SANDBOX true
set_kv MCP_ALLOW_INTERNAL_HOSTS "$INTERNAL_HOSTS"

echo "Recreating control + gateway with MCP_HTTP_VIA_SANDBOX=true ..."
docker compose up -d control gateway

for _ in $(seq 1 30); do
  if docker exec ai_mesh_firewall-gateway-1 printenv MCP_HTTP_VIA_SANDBOX 2>/dev/null | grep -qiE '^(1|true|yes|on)$'; then
    echo "gateway MCP_HTTP_VIA_SANDBOX=$(docker exec ai_mesh_firewall-gateway-1 printenv MCP_HTTP_VIA_SANDBOX)"
    echo "control MCP_ALLOW_INTERNAL_HOSTS=$(docker exec ai_mesh_firewall-control-1 printenv MCP_ALLOW_INTERNAL_HOSTS 2>/dev/null || echo unset)"
    exit 0
  fi
  sleep 2
done
echo "WARN: gateway MCP_HTTP_VIA_SANDBOX not visible after recreate — check compose env_file wiring." >&2
exit 1
