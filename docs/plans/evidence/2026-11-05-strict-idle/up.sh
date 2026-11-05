#!/usr/bin/env bash
# Bring up the amff1 live-test stack for the strict-withhold-idle-timeout fix (task 5).
# Project amff1, host ports base +25000, bound to 127.0.0.1. Run from the worktree root.
# NEVER touches amfint or ai_mesh_firewall.
#
# Needs ./.env at the worktree root (gitignored; copied from the integration worktree which holds
# the shared dev AMF_STATE_HMAC_KEY / POLICY_SIGNING_KEY so the state services + signed state boot).
set -euo pipefail
cd "$(dirname "$0")/../../../.."
D=docs/plans/evidence/2026-11-05-strict-idle
export COMPOSE_PROJECT_NAME=amff1
export GATEWAY_V2_WEB_CONCURRENCY=1
export INT_PROVIDER_URL="${INT_PROVIDER_URL-http://fake-openai:9600/v1}"
export INT_PROVIDER_KEY="${INT_PROVIDER_KEY-sk-fake-upstream}"
F=(-f docker-compose.yml -f docker-compose.override.yml -f "$D/docker-compose.amff1.yml")
docker compose "${F[@]}" --profile services up -d --build \
  postgres pgbouncer redis rabbitmq control gateway gateway-v2 mcp-broker mcp-stub \
  state-rehydrator-a state-projector-a
# Test double (not product code): the fake OpenAI upstream with SLOWSTREAM/SLOWLEAK/SLOWSTALL.
docker rm -f amff1-fake-openai >/dev/null 2>&1 || true
docker run -d --name amff1-fake-openai --network amff1_default --network-alias fake-openai \
  -v "$PWD/$D/fake_openai_upstream.py:/s.py:ro" python:3.12-slim \
  sh -c "pip install -q aiohttp==3.10.10 && python /s.py"
# Publish the INCREMENTAL control plan for the peer org (org-b) so the dev plan watch applies it.
sleep 2
docker exec -i amff1-redis-1 redis-cli -x SET '{rv2}:devplan:org-b' \
  < "$PWD/$D/plan.incremental.json" >/dev/null
echo "amff1 up. gateway-v2 http://127.0.0.1:33301 (strict dev-org + incremental org-b)."
# Teardown (ONLY amff1 resources):
#   COMPOSE_PROJECT_NAME=amff1 docker compose -f docker-compose.yml -f docker-compose.override.yml \
#     -f docs/plans/evidence/2026-11-05-strict-idle/docker-compose.amff1.yml --profile services down -v
#   docker rm -f amff1-fake-openai
