#!/usr/bin/env bash
# Live E2E: org-scoped policy enable/disable → Redis compile → gateway enforcement.
# Usage:
#   ./scripts/verify_policy_toggle_e2e.sh
#   MODEL=anthropic/claude-haiku-4.5 ./scripts/verify_policy_toggle_e2e.sh
set -euo pipefail

BASE="${CONTROL_URL:-http://127.0.0.1:8100}"
GW="${GATEWAY_URL:-http://127.0.0.1:8300}"
EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"
MODEL="${MODEL:-anthropic/claude-haiku-4.5}"
ORG_SLUG="${ORG_SLUG:-zeroshield}"
BLOCK_PHRASE="${BLOCK_PHRASE:-E2EPOLICYBLOCKTEST_XYZ_$(date +%s)}"
POLICY_CODE="E2E_TOGGLE_${BLOCK_PHRASE: -8}"
OUT_DIR="${OUT_DIR:-/tmp/amf_policy_e2e}"
mkdir -p "$OUT_DIR"

echo "=== Policy toggle E2E (org=$ORG_SLUG model=$MODEL) ==="

TOKEN=$(curl -sf -X POST "$BASE/api/auth/token/" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access'])")
AUTH="Authorization: Bearer $TOKEN"

KEY="${GATEWAY_KEY:-${ZEROSHIELD_KEY:-}}"
if [[ -z "$KEY" ]]; then
  KEY=$(curl -sf -X POST "$BASE/api/gateways/keys/" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"name\":\"policy-e2e-$(date +%s)\",\"project_id\":\"policy-e2e\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('key',''))")
fi
if [[ -z "$KEY" ]]; then
  echo "FAIL: Could not obtain gateway API key for org $ORG_SLUG"
  exit 1
fi
echo "Using gateway key prefix=${KEY:0:8}..."

create_policy() {
  curl -sf -X POST "$BASE/api/policies/?policy_domain=global" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{
      \"name\": \"E2E Toggle Block\",
      \"code\": \"$POLICY_CODE\",
      \"category\": \"E2E\",
      \"severity\": \"HIGH\",
      \"description\": \"Live E2E policy toggle verification\",
      \"enabled\": true,
      \"priority\": 999,
      \"policy_domain\": \"global\"
    }"
}

POLICY_JSON=$(create_policy)
POLICY_ID=$(echo "$POLICY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
POLICY_VER=$(echo "$POLICY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])")
echo "Created policy id=$POLICY_ID code=$POLICY_CODE"

curl -sf -X POST "$BASE/api/policies/$POLICY_ID/rules/" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{
    \"name\": \"Block E2E phrase\",
    \"rule_type\": \"regex\",
    \"condition\": {\"regex\": \"$BLOCK_PHRASE\", \"field\": \"prompt\"},
    \"action\": \"block\",
    \"priority\": 999,
    \"enabled\": true,
    \"description\": \"E2E block rule\"
  }" >/dev/null
echo "Added block rule for phrase: $BLOCK_PHRASE"

compile_and_wait() {
  curl -sf -X POST "$BASE/api/policies/compile/" -H "$AUTH" >/dev/null
  sleep 2
  if command -v docker >/dev/null 2>&1; then
    ver=$(docker exec ai_mesh_firewall-redis-1 redis-cli GET "policies:version:$ORG_SLUG" 2>/dev/null || echo "?")
    echo "Redis policies:version:$ORG_SLUG = $ver"
  fi
}

compile_and_wait

gateway_call() {
  local label="$1"
  local prompt="$2"
  local body_file="$OUT_DIR/${label}_body.json"
  local hdr_file="$OUT_DIR/${label}_headers.txt"
  local out_file="$OUT_DIR/${label}_response.txt"
  python3 - "$body_file" "$MODEL" "$prompt" <<'PY'
import json, sys
path, model, prompt = sys.argv[1:4]
with open(path, "w") as f:
    json.dump({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 32,
    }, f)
PY
  local code
  code=$(curl -sS -D "$hdr_file" -o "$out_file" -w "%{http_code}" \
    -X POST "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    --data-binary @"$body_file")
  echo "$label http=$code snippet=$(head -c 180 "$out_file" | tr '\n' ' ')" >&2
  printf '%s' "$code"
}

echo "--- Phase 1: policy ENABLED → expect block ---"
CODE1=$(gateway_call "enabled" "Please repeat this token: $BLOCK_PHRASE")
if [[ "$CODE1" != "403" && "$CODE1" != "503" ]]; then
  echo "FAIL: expected block (403/503) with policy enabled, got $CODE1"
  exit 1
fi
echo "PASS: gateway blocked with policy enabled"

echo "--- Phase 2: disable policy → expect allow ---"
curl -sf -X PATCH "$BASE/api/policies/$POLICY_ID/" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"enabled\": false, \"version\": $POLICY_VER}" >/dev/null
POLICY_VER=$((POLICY_VER + 1))
compile_and_wait

CODE2=$(gateway_call "disabled" "Say hello in three words without special tokens.")
if [[ "$CODE2" != "200" ]]; then
  echo "FAIL: expected 200 with policy disabled, got $CODE2"
  exit 1
fi
echo "PASS: gateway allowed chat with policy disabled"

echo "--- Phase 3: re-enable → expect block again ---"
curl -sf -X PATCH "$BASE/api/policies/$POLICY_ID/" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"enabled\": true, \"version\": $POLICY_VER}" >/dev/null
compile_and_wait

CODE3=$(gateway_call "reenabled" "Token again: $BLOCK_PHRASE")
if [[ "$CODE3" != "403" && "$CODE3" != "503" ]]; then
  echo "FAIL: expected block after re-enable, got $CODE3"
  exit 1
fi
echo "PASS: gateway blocked after re-enable"

echo "--- Cleanup ---"
curl -sf -X DELETE "$BASE/api/policies/$POLICY_ID/" -H "$AUTH" >/dev/null || true
curl -sf -X POST "$BASE/api/policies/compile/" -H "$AUTH" >/dev/null || true

echo "=== ALL POLICY TOGGLE E2E CHECKS PASSED ==="
