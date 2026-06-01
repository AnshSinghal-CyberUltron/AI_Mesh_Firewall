#!/usr/bin/env bash
# Live org-scoping verification for rate-limit + content-filter fields.
# Reads per-org config via control API and inspects per-org Redis keys.
set -uo pipefail

CTRL=http://localhost:8100
GW=http://127.0.0.1:8180/gateway

echo "== ORG A (zeroshield) token =="
A_TOK=$(curl -s -X POST "$CTRL/api/auth/token/" -H 'Content-Type: application/json' \
  --data '{"email":"admin@zeroshield.io","password":"Adm1n!Pass#2024"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
echo "tokA len: ${#A_TOK}"

echo "== ORG B (acme-test) token =="
B_TOK=$(curl -s -X POST "$CTRL/api/auth/token/" -H 'Content-Type: application/json' \
  --data '{"email":"client2@acme.test","password":"Acme!Pass#2024"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
echo "tokB len: ${#B_TOK}"

echo "== ORG A firewall config (rate-limit + content-filter fields) =="
curl -s "$CTRL/api/firewall/config/" -H "Authorization: Bearer $A_TOK" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["rate_limit_enabled","requests_per_minute","burst_limit","content_filtering_enabled","pii_detection_enabled","toxicity_threshold","blocked_keywords"]})'

echo "== ORG B firewall config =="
curl -s "$CTRL/api/firewall/config/" -H "Authorization: Bearer $B_TOK" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["rate_limit_enabled","requests_per_minute","burst_limit","content_filtering_enabled","pii_detection_enabled","toxicity_threshold","blocked_keywords"]})'

echo "== Redis per-org firewall config keys =="
docker compose exec -T redis redis-cli KEYS 'firewall:config:*'

echo "== Redis per-org gateway payload (zeroshield) rate-limit keys =="
docker compose exec -T redis redis-cli GET 'firewall:config:zeroshield' \
  | python3 -c 'import sys,json;d=json.loads(sys.stdin.read() or "{}");print({k:d.get(k) for k in ["rate_limit_enabled","requests_per_minute","burst_limit","input_scan_enabled","scan_block_on_pii","toxicity_threshold","blocked_keywords"]})'
