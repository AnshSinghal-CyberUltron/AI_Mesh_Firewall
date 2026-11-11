#!/usr/bin/env bash
# Bring up the amff2 live-test stack for the request-id-uniqueness fix (task 5).
# Project amff2, ports +60000, bound to 127.0.0.1. Run from the worktree root.
# Never touches amfint or ai_mesh_firewall.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
D=docs/plans/evidence/2026-11-05-request-id-uniqueness
export COMPOSE_PROJECT_NAME=amff2
export GATEWAY_V2_WEB_CONCURRENCY=1
export INT_PROVIDER_URL="${INT_PROVIDER_URL-http://fake-openai:9600/v1}"
export INT_PROVIDER_KEY="${INT_PROVIDER_KEY-sk-fake-upstream}"
F=(-f docker-compose.yml -f docker-compose.override.yml -f "$D/docker-compose.amff2.yml")
docker compose "${F[@]}" --profile services up -d --build \
  postgres pgbouncer redis rabbitmq control gateway gateway-v2 mcp-broker mcp-stub \
  state-rehydrator-a state-projector-a
docker rm -f amff2-fake-mcp amff2-fake-openai >/dev/null 2>&1 || true
docker run -d --name amff2-fake-mcp --network amff2_default --network-alias fake-mcp \
  -v "$PWD/$D/fake_mcp_server.py:/s.py:ro" python:3.12-slim python /s.py
docker run -d --name amff2-fake-openai --network amff2_default --network-alias fake-openai \
  -v "$PWD/$D/fake_openai_upstream.py:/s.py:ro" python:3.12-slim sh -c "pip install -q aiohttp==3.10.10 && python /s.py"
