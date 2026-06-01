#!/usr/bin/env bash
# Runtime proof of per-org PII-detection toggle (scan_block_on_pii).
# ORG A = zeroshield (allows gpt-4). Observes gateway logs.
set -uo pipefail
CTRL=http://localhost:8100
GW=http://localhost:8300
A_KEY="$1"
A_TOK=$(curl -s -X POST "$CTRL/api/auth/token/" -H 'Content-Type: application/json' \
  --data '{"email":"admin@zeroshield.io","password":"Adm1n!Pass#2024"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

set_pii() { # $1 true|false
  printf '{"pii_detection_enabled":%s}' "$1" > /tmp/pii.json
  curl -s -X PUT "$CTRL/api/firewall/config/" -H "Authorization: Bearer $A_TOK" \
    -H 'Content-Type: application/json' --data @/tmp/pii.json \
    | python3 -c 'import sys,json;print("  pii_detection_enabled =",json.load(sys.stdin).get("pii_detection_enabled"))'
  sleep 2
}
send() { # $1 content
  curl -s -o /dev/null -X POST "$GW/v1/chat/completions" -H "Authorization: Bearer $A_KEY" \
    -H 'Content-Type: application/json' \
    --data "$(python3 -c 'import json,sys;print(json.dumps({"model":"gpt-4","messages":[{"role":"user","content":sys.argv[1]}]}))' "$1")"
}
logs() { docker compose logs gateway --since "${1}s" 2>&1 | grep -iE "redacting|PII detection disabled|PII/secret detected" | tail -4; }

echo "=== CASE 1: PII ON + email (expect: redacting type=pii) ==="
set_pii true
send "Please contact me at john.doe@example.com about the order"
sleep 1; logs 6

echo "=== CASE 2: PII OFF + email (expect: PII detection disabled; allowing unredacted) ==="
set_pii false
send "Please contact me at john.doe@example.com about the order"
sleep 1; logs 6

echo "=== CASE 3: PII OFF + AWS secret (expect: redacting type=secret — always) ==="
send "My AWS key is AKIAIOSFODNN7EXAMPLE please rotate it"
sleep 1; logs 6

echo "=== Revert ORG A pii_detection_enabled=true ==="
set_pii true
