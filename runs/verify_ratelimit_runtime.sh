#!/usr/bin/env bash
# Runtime proof of per-org rate-limit scoping.
set -uo pipefail
CTRL=http://localhost:8100
GW=http://localhost:8300
A_KEY="$1"   # zeroshield
B_KEY="$2"   # acme-test

A_TOK=$(curl -s -X POST "$CTRL/api/auth/token/" -H 'Content-Type: application/json' \
  --data '{"email":"admin@zeroshield.io","password":"Adm1n!Pass#2024"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

echo "== Set ORG A (zeroshield) requests_per_minute=3, burst_limit=3 =="
printf '{"requests_per_minute":3,"burst_limit":3,"rate_limit_enabled":true}' > /tmp/rlA.json
curl -s -X PUT "$CTRL/api/firewall/config/" -H "Authorization: Bearer $A_TOK" \
  -H 'Content-Type: application/json' --data @/tmp/rlA.json \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["requests_per_minute","burst_limit","rate_limit_enabled"]})'
sleep 2  # allow pubsub propagation to gateway

echo "== Clear ratelimit Redis keys =="
docker compose exec -T redis redis-cli --scan --pattern 'ratelimit:*' | xargs -r docker compose exec -T redis redis-cli DEL >/dev/null 2>&1
echo "cleared"

echo "== Fire 7 requests as ORG A (zeroshield) =="
for i in $(seq 1 7); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $A_KEY" -H 'Content-Type: application/json' \
    --data '{"model":"gpt-4","messages":[{"role":"user","content":"hi"}]}')
  echo "A req $i -> $code"
done

echo "== Fire 7 requests as ORG B (acme-test, RPM=1000) =="
for i in $(seq 1 7); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $B_KEY" -H 'Content-Type: application/json' \
    --data '{"model":"gpt-4","messages":[{"role":"user","content":"hi"}]}')
  echo "B req $i -> $code"
done

echo "== Redis ratelimit keys (should be per-org, NOT ratelimit:global) =="
docker compose exec -T redis redis-cli --scan --pattern 'ratelimit:*'

echo "== Revert ORG A config to defaults =="
printf '{"requests_per_minute":1000,"burst_limit":150,"rate_limit_enabled":true}' > /tmp/rlA2.json
curl -s -X PUT "$CTRL/api/firewall/config/" -H "Authorization: Bearer $A_TOK" \
  -H 'Content-Type: application/json' --data @/tmp/rlA2.json \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("reverted:",{k:d.get(k) for k in ["requests_per_minute","burst_limit"]})'
