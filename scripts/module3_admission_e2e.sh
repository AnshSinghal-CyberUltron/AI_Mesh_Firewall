#!/usr/bin/env bash
# Live Module 3 Phase 1 admission e2e: Cosign sign → register → allow/deny → Module 2.
#
# Prerequisites:
#   - control on CONTROL_URL, gateway with MODULE3_ADMISSION_MODE=verify + cosign.pub
#   - cosign CLI + secrets/cosign.key (scripts/module3_gen_cosign_keypair.sh)
#   - Celery workers for deny → Module 2 incidents
#   - Admin JWT credentials (TEST_EMAIL / TEST_PASSWORD)
#
# Usage:
#   bash scripts/module3_admission_e2e.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${CONTROL_URL:-http://127.0.0.1:8100}"
EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"
COSIGN_KEY="${COSIGN_KEY:-${ROOT}/secrets/cosign.key}"
COSIGN_PUB="${COSIGN_PUB:-${ROOT}/secrets/cosign.pub}"
REQUIRE_VERIFY="${REQUIRE_VERIFY:-1}"
POLL_SEC="${POLL_SEC:-30}"
POLL_INTERVAL="${POLL_INTERVAL:-2}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "OK: $*"; }

command -v cosign >/dev/null 2>&1 || fail "cosign CLI not on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 required for JSON parsing"
[[ -f "$COSIGN_KEY" ]] || fail "Missing $COSIGN_KEY — run scripts/module3_gen_cosign_keypair.sh"
[[ -f "$COSIGN_PUB" ]] || fail "Missing $COSIGN_PUB"

echo "=== Module 3 admission e2e (control=$BASE) ==="

TOKEN=$(curl -sf -X POST "$BASE/api/auth/token/" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access'])") \
  || fail "login failed"
AUTH="Authorization: Bearer $TOKEN"

MODEL_SHA="${MODEL_SHA:-$(python3 -c 'import hashlib; print(hashlib.sha256(b"module3-e2e-model").hexdigest())')}"
DATA_SHA="${DATA_SHA:-$(python3 -c 'import hashlib; print(hashlib.sha256(b"module3-e2e-data").hexdigest())')}"
TS="$(date +%s)"
NAME="e2e-model-${TS}"
VERSION="1.0.0"
IMAGE_REF="registry.example.com/zeroshield/${NAME}:${VERSION}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

printf '%s' "$MODEL_SHA" > "$TMP/payload.txt"
COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign sign-blob --key "$COSIGN_KEY" \
  --output-signature "$TMP/payload.sig" "$TMP/payload.txt" >/dev/null

if base64 --help 2>&1 | grep -q -- '-w'; then
  SIG_B64="$(base64 -w0 "$TMP/payload.sig")"
else
  SIG_B64="$(base64 < "$TMP/payload.sig" | tr -d '\n')"
fi

# Register artifact
REG=$(curl -sf -X POST "$BASE/api/module3/llmops/artifacts/" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{
    \"name\": \"$NAME\",
    \"version\": \"$VERSION\",
    \"image_ref\": \"$IMAGE_REF\",
    \"data_sha256\": \"$DATA_SHA\",
    \"model_sha256\": \"$MODEL_SHA\",
    \"signature_digest\": \"$SIG_B64\",
    \"source\": \"ci\"
  }") || fail "artifact register failed"
ART_ID=$(echo "$REG" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
pass "registered artifact id=$ART_ID"

# Allow path
ALLOW=$(curl -sf -X POST "$BASE/api/module3/llmops/verify/" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"artifact_id\": $ART_ID}") || fail "verify (allow) failed"

echo "$ALLOW" | python3 -c "
import sys, json, os
d = json.load(sys.stdin)
mode = (d.get('gateway') or {}).get('admission_mode') or ''
method = (d.get('gateway') or {}).get('verification_method') or ''
result = (d.get('decision') or {}).get('result')
print(f'  admission_mode={mode} verification_method={method} result={result}')
require = os.environ.get('REQUIRE_VERIFY', '1') == '1'
if require and mode != 'verify':
    raise SystemExit('gateway still in passthrough — start with docker-compose.module3-verify.yml')
if result != 'allow':
    raise SystemExit(f'expected allow, got {result}: {(d.get(\"decision\") or {}).get(\"reason\")}')
if require and method in ('', 'passthrough'):
    raise SystemExit(f'expected Cosign verification_method, got {method!r}')
" || fail "allow path assertion failed"
pass "allow path (Cosign verify)"

# Deny path — tampered signature
DENY=$(curl -sf -X POST "$BASE/api/module3/llmops/verify/" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{
    \"artifact_id\": $ART_ID,
    \"signature_digest\": \"invalid:missing-cosign\"
  }") || fail "verify (deny) failed"

echo "$DENY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
result = (d.get('decision') or {}).get('result')
if result != 'deny':
    raise SystemExit(f'expected deny, got {result}')
" || fail "deny path assertion failed"
pass "deny path"

# Poll Module 2 for admission-denied incident
TITLE_NEEDLE="LLMOps admission denied: ${NAME}:${VERSION}"
deadline=$((SECONDS + POLL_SEC))
found=0
while (( SECONDS < deadline )); do
  if curl -sf "$BASE/api/module2/incidents/?page_size=50" -H "$AUTH" \
    | python3 -c "
import sys, json
needle = '''${TITLE_NEEDLE}'''
data = json.load(sys.stdin)
rows = data.get('results') or data if isinstance(data, list) else data.get('results') or []
for r in rows:
    if needle in str(r.get('title') or ''):
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null; then
    found=1
    break
  fi
  sleep "$POLL_INTERVAL"
done

if [[ "$found" -ne 1 ]]; then
  fail "Module 2 incident not found within ${POLL_SEC}s (is Celery workers profile up?): $TITLE_NEEDLE"
fi
pass "Module 2 incident for deny"

echo "=== Module 3 admission e2e PASSED ==="
