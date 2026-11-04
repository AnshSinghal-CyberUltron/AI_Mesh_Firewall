#!/usr/bin/env bash
# Bring up the integration live-test stack exactly as used on 4 Nov 2026 (project amfint, ports +30000,
# bound to 127.0.0.1). Run from the integration worktree root. Needs ../../.env copied to ./.env
# (gitignored). Never touches the main-tree project `ai_mesh_firewall`.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
D=docs/plans/evidence/2026-11-04-integration
export COMPOSE_PROJECT_NAME=amfint
export GATEWAY_V2_WEB_CONCURRENCY=1          # one worker: dev RAG stores are per process; L27-2 is W=1
export INT_CANARY_ORGS="${INT_CANARY_ORGS-}"  # canary off by default (the echo provider leaks it)
export INT_EXFIL_ORGS="${INT_EXFIL_ORGS-}"
export INT_PROVIDER_URL="${INT_PROVIDER_URL-http://fake-openai:9600/v1}"   # empty = echo provider
export INT_PROVIDER_KEY="${INT_PROVIDER_KEY-sk-fake-upstream}"
F=(-f docker-compose.yml -f docker-compose.override.yml -f "$D/docker-compose.int.yml")
docker compose "${F[@]}" --profile services up -d --build \
  postgres pgbouncer redis rabbitmq control gateway gateway-v2 mcp-broker mcp-stub \
  state-rehydrator-a state-projector-a frontend
# Test doubles (not product code): fake MCP upstream (bearer-authenticated) and fake OpenAI upstream.
docker rm -f amfint-fake-mcp amfint-fake-openai >/dev/null 2>&1 || true
docker run -d --name amfint-fake-mcp --network amfint_default --network-alias fake-mcp \
  -v "$PWD/$D/fake_mcp_server.py:/s.py:ro" python:3.12-slim python /s.py
docker run -d --name amfint-fake-openai --network amfint_default --network-alias fake-openai \
  -v "$PWD/$D/fake_openai_upstream.py:/s.py:ro" python:3.12-slim sh -c "pip install -q aiohttp==3.10.10 && python /s.py"
echo "After the first MCP call creates the org sandbox, run:"
echo "  docker network connect --alias fake-mcp mcp_sandbox_net_zeroshield amfint-fake-mcp"
# Teardown (removes ONLY what this script and the integration broker created):
#   docker compose "${F[@]}" --profile services down
#   docker rm -f amfint-fake-mcp amfint-fake-openai zeroshield-mcp-sandbox
#   docker network rm mcp_sandbox_net_zeroshield
