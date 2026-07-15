#!/usr/bin/env bash
# Example: Cosign sign-blob for Module 3 admission (detached fingerprint signature).
# Requires: cosign CLI + cosign.key (from scripts/module3_gen_cosign_keypair.sh).
#
# Payload MUST be the exact 64-hex model fingerprint (no "sha256:" prefix, no newline).
# Match MODULE3_COSIGN_VERIFY_IMAGE=0 (default blob mode).
#
# Optional live register+verify when CONTROL_URL + credentials are set:
#   POST_TO_CONTROL=1 bash scripts/module3_cosign_sign_example.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_SHA="${MODEL_SHA:-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb}"
IMAGE_REF="${IMAGE_REF:-registry.example.com/acme/support-model:1.4.0}"
KEY="${COSIGN_KEY:-${ROOT}/secrets/cosign.key}"
if [[ ! -f "$KEY" && -f "cosign.key" ]]; then
  KEY="cosign.key"
fi

if [[ ! -f "$KEY" ]]; then
  echo "Missing $KEY — run: scripts/module3_gen_cosign_keypair.sh" >&2
  exit 1
fi

if [[ ! "$MODEL_SHA" =~ ^[a-fA-F0-9]{64}$ ]]; then
  echo "MODEL_SHA must be 64 hex characters (no sha256: prefix)" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Exact bytes — no trailing newline (gateway verifies the same)
printf '%s' "$MODEL_SHA" > "$TMP/payload.txt"
COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign sign-blob --key "$KEY" --output-signature "$TMP/payload.sig" "$TMP/payload.txt"

if base64 --help 2>&1 | grep -q -- '-w'; then
  SIG_B64="$(base64 -w0 "$TMP/payload.sig")"
else
  SIG_B64="$(base64 < "$TMP/payload.sig" | tr -d '\n')"
fi

cat <<EOF
# Register / verify payload fields:
image_ref=${IMAGE_REF}
model_sha256=${MODEL_SHA}
signature_digest=${SIG_B64}
# (optional alias) signature_digest=cosign-blob:${SIG_B64}

# Full live e2e:
#   bash scripts/module3_admission_e2e.sh
# POST /api/module3/llmops/verify/ with MODULE3_ADMISSION_MODE=verify
# and MODULE3_COSIGN_PUBLIC_KEY_FILE=/run/secrets/cosign.pub
EOF

if [[ "${POST_TO_CONTROL:-0}" == "1" ]]; then
  BASE="${CONTROL_URL:-http://127.0.0.1:8100}"
  EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
  PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"
  NAME="${ARTIFACT_NAME:-acme-support-model}"
  VERSION="${ARTIFACT_VERSION:-1.4.0}"
  TOKEN=$(curl -sf -X POST "$BASE/api/auth/token/" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access'])")
  AUTH="Authorization: Bearer $TOKEN"
  curl -sf -X POST "$BASE/api/module3/llmops/artifacts/" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{
      \"name\": \"$NAME\",
      \"version\": \"$VERSION\",
      \"image_ref\": \"$IMAGE_REF\",
      \"model_sha256\": \"$MODEL_SHA\",
      \"signature_digest\": \"$SIG_B64\",
      \"source\": \"ci\"
    }"
  echo ""
  curl -sf -X POST "$BASE/api/module3/llmops/verify/" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"image_ref\": \"$IMAGE_REF\", \"model_sha256\": \"$MODEL_SHA\", \"signature_digest\": \"$SIG_B64\"}"
  echo ""
fi
