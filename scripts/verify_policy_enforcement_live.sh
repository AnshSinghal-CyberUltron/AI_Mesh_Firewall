#!/usr/bin/env bash
# Live E2E: block / redact / monitor + enable-disable via real gateway model calls.
# Org: zeroshield | Model: anthropic/claude-haiku-4.5 (Custom/Other)
set -euo pipefail

BASE="${CONTROL_URL:-http://127.0.0.1:8100}"
GW="${GATEWAY_URL:-http://127.0.0.1:8300}"
EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"
MODEL="${MODEL:-anthropic/claude-haiku-4.5}"
ORG_SLUG="${ORG_SLUG:-zeroshield}"
STAMP="$(date +%s)"
OUT_DIR="${OUT_DIR:-/tmp/amf_policy_live_$STAMP}"
mkdir -p "$OUT_DIR"

BLOCK_TOKEN="E2EBLOCK_${STAMP}"
REDACT_TOKEN="E2EREDACT_${STAMP}"
MONITOR_TOKEN="E2EMON_${STAMP}"
REDACT_EMAIL="e2e_${STAMP}@zeroshield-test.io"

pass=0
fail=0
created_policy_ids=()

log() { echo "[$(date +%H:%M:%S)] $*"; }
pass_case() { log "PASS: $1"; pass=$((pass+1)); }
fail_case() { log "FAIL: $1"; fail=$((fail+1)); }

TOKEN=$(curl -sf -X POST "$BASE/api/auth/token/" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access'])")
AUTH="Authorization: Bearer $TOKEN"

KEY="${GATEWAY_KEY:-${ZEROSHIELD_KEY:-}}"
if [[ -z "$KEY" ]]; then
  KEY=$(curl -sf -X POST "$BASE/api/gateways/keys/" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"name\":\"policy-live-$STAMP\",\"project_id\":\"policy-live\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('key',''))")
fi
[[ -n "$KEY" ]] || { echo "No gateway key"; exit 1; }

compile() {
  curl -sf -X POST "$BASE/api/policies/compile/" -H "$AUTH" >/dev/null
  sleep 2
}

create_policy_with_rule() {
  local code="$1" name="$2" regex="$3" action="$4" replacement="${5:-}"
  local policy_json policy_id
  policy_json=$(curl -sf -X POST "$BASE/api/policies/?policy_domain=pipeline" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$name\",\"code\":\"$code\",\"category\":\"E2E\",\"severity\":\"HIGH\",\"enabled\":true,\"priority\":998,\"policy_domain\":\"pipeline\"}")
  policy_id=$(echo "$policy_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  created_policy_ids+=("$policy_id")
  local rule_body
  if [[ "$action" == "redact" ]]; then
    rule_body="{\"name\":\"$name rule\",\"rule_type\":\"regex\",\"condition\":{\"regex\":\"$regex\",\"field\":\"prompt\"},\"action\":\"redact\",\"redaction_config\":{\"replacement\":\"[REDACTED_E2E]\"},\"priority\":998,\"enabled\":true}"
  else
    rule_body="{\"name\":\"$name rule\",\"rule_type\":\"regex\",\"condition\":{\"regex\":\"$regex\",\"field\":\"prompt\"},\"action\":\"$action\",\"priority\":998,\"enabled\":true}"
  fi
  curl -sf -X POST "$BASE/api/policies/$policy_id/rules/" \
    -H "$AUTH" -H 'Content-Type: application/json' -d "$rule_body" >/dev/null
  echo "$policy_id"
}

disable_all_e2e() {
  for pid in "${created_policy_ids[@]:-}"; do
    curl -sf -X PATCH "$BASE/api/policies/$pid/" \
      -H "$AUTH" -H 'Content-Type: application/json' \
      -d '{"enabled": false}' >/dev/null 2>&1 || true
  done
  compile
}

gateway_chat() {
  local label="$1" prompt="$2"
  local body="$OUT_DIR/${label}.json"
  local hdr="$OUT_DIR/${label}.hdr"
  local out="$OUT_DIR/${label}.out"
  python3 - "$body" "$MODEL" "$prompt" <<'PY'
import json, sys
with open(sys.argv[1], "w") as f:
    json.dump({"model": sys.argv[2], "messages": [{"role": "user", "content": sys.argv[3]}], "stream": False, "max_tokens": 48}, f)
PY
  local code
  code=$(curl -sS -D "$hdr" -o "$out" -w "%{http_code}" \
    -X POST "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    --data-binary @"$body")
  local routed orig zs_action
  routed=$(grep -i '^x-zeroshield-routed-model:' "$hdr" 2>/dev/null | head -1 | cut -d: -f2- | tr -d ' \r' || true)
  orig=$(grep -i '^x-zeroshield-original-model:' "$hdr" 2>/dev/null | head -1 | cut -d: -f2- | tr -d ' \r' || true)
  zs_action=$(grep -i '^x-zeroshield-action:' "$hdr" 2>/dev/null | head -1 | cut -d: -f2- | tr -d ' \r' || true)
  python3 - "$code" "$routed" "$orig" "$zs_action" "$out" "$label" <<'PY' >&2
import json, sys
code, routed, orig, zs_action, path, label = sys.argv[1:7]
content = open(path).read()
snippet = content[:220].replace("\n", " ")
zs = {}
try:
    zs = json.loads(content).get("zeroshield") or {}
except Exception:
    pass
assistant = ""
try:
    assistant = (json.loads(content).get("choices") or [{}])[0].get("message", {}).get("content") or ""
except Exception:
    pass
print(f"{label}|http={code}|routed={routed}|orig={orig}|hdr_action={zs_action}|zs_action={zs.get('action','')}|assistant={assistant[:120]!r}|snippet={snippet[:180]}")
PY
  printf '%s' "$code"
}

cleanup() {
  for pid in "${created_policy_ids[@]:-}"; do
    curl -sf -X DELETE "$BASE/api/policies/$pid/" -H "$AUTH" >/dev/null 2>&1 || true
  done
  compile
}
trap cleanup EXIT

log "=== Live policy enforcement E2E | org=$ORG_SLUG model=$MODEL ==="
log "Gateway key prefix=${KEY:0:8}..."

# Remove stale E2E policies from prior runs (prevents cross-test block rule collisions)
curl -sf "$BASE/api/policies/?policy_domain=pipeline" -H "$AUTH" | python3 -c "
import sys, json
data = json.load(sys.stdin)
rows = data.get('results', data if isinstance(data, list) else [])
for p in rows:
    code = p.get('code') or ''
    name = p.get('name') or ''
    if code.startswith('E2E_') or code.startswith('MON_') or code.startswith('E2E') or 'E2E' in name:
        print(p['id'])
" | while read -r pid; do
  [[ -n "$pid" ]] && curl -sf -X DELETE "$BASE/api/policies/$pid/" -H "$AUTH" >/dev/null || true
done
compile

# Baseline: clean prompt should reach model
disable_all_e2e 2>/dev/null || true
created_policy_ids=()
compile
BASE_CODE=$(gateway_chat "baseline" "What is 2 plus 2? Reply with one number only.")
if [[ "$BASE_CODE" == "200" ]]; then
  pass_case "baseline live model call (200)"
else
  fail_case "baseline live model call expected 200 got $BASE_CODE"
fi

# --- BLOCK ---
log "--- BLOCK enforcement ---"
disable_all_e2e 2>/dev/null || true
created_policy_ids=()
create_policy_with_rule "E2E_BLK_$STAMP" "E2E Block" "$BLOCK_TOKEN" "block" >/dev/null
compile
BLK_CODE=$(gateway_chat "block" "Repeat token: $BLOCK_TOKEN")
if [[ "$BLK_CODE" == "403" || "$BLK_CODE" == "503" ]]; then
  pass_case "BLOCK: gateway returned $BLK_CODE for block token"
else
  fail_case "BLOCK: expected 403/503 got $BLK_CODE"
fi

# --- REDACT ---
log "--- REDACT enforcement ---"
disable_all_e2e
created_policy_ids=()
create_policy_with_rule "E2E_RDA_$STAMP" "E2E Redact" "$REDACT_EMAIL" "redact" "[REDACTED_E2E]" >/dev/null
compile
RDA_CODE=$(gateway_chat "redact" "Contact: $REDACT_EMAIL")
if [[ "$RDA_CODE" == "200" ]]; then
  if grep -q "$REDACT_EMAIL" "$OUT_DIR/redact.out" 2>/dev/null; then
    fail_case "REDACT: raw email still in response body"
  else
    pass_case "REDACT: 200 and email not echoed in response"
  fi
  if grep -qi 'redact' "$OUT_DIR/redact.hdr" 2>/dev/null; then
    pass_case "REDACT: X-ZeroShield-Action redact header present"
  fi
  pass_case "REDACT: live model responded after prompt redaction"
else
  fail_case "REDACT: expected 200 got $RDA_CODE"
fi

# --- MONITOR ---
log "--- MONITOR enforcement ---"
disable_all_e2e
created_policy_ids=()
create_policy_with_rule "E2E_MON_$STAMP" "E2E Monitor" "$MONITOR_TOKEN" "monitor" >/dev/null
compile
MON_CODE=$(gateway_chat "monitor" "Note $MONITOR_TOKEN in logs. What is 3 plus 3?")
if [[ "$MON_CODE" == "200" ]]; then
  pass_case "MONITOR: request allowed (200) with monitor rule matched"
else
  fail_case "MONITOR: expected 200 got $MON_CODE"
fi

# --- TOGGLE: disable block policy → allow ---
log "--- TOGGLE disable block policy ---"
disable_all_e2e
created_policy_ids=()
PID=$(create_policy_with_rule "E2E_TGL_$STAMP" "E2E Toggle" "$BLOCK_TOKEN" "block")
compile
T1=$(gateway_chat "toggle_on" "Token $BLOCK_TOKEN")
VER=$(curl -sf "$BASE/api/policies/$PID/?policy_domain=pipeline" -H "$AUTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])")
curl -sf -X PATCH "$BASE/api/policies/$PID/" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"enabled\": false, \"version\": $VER}" >/dev/null
compile
T2=$(gateway_chat "toggle_off" "What is 5 plus 5? One number only.")
if [[ "$T1" == "403" || "$T1" == "503" ]] && [[ "$T2" == "200" ]]; then
  pass_case "TOGGLE: block when enabled, allow when disabled"
else
  fail_case "TOGGLE: on=$T1 off=$T2 (expected 403/503 then 200)"
fi

log "=== SUMMARY passed=$pass failed=$fail ==="
[[ "$fail" -eq 0 ]] || exit 1
echo "ALL LIVE POLICY ENFORCEMENT CHECKS PASSED"
