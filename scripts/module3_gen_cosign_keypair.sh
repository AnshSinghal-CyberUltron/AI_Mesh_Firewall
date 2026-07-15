#!/usr/bin/env bash
# Generate a Cosign keypair for Module 3 local/prod-profile admission (dev keys only).
# Writes:
#   secrets/cosign.key  (private — never commit)
#   secrets/cosign.pub  (public — mounted at /run/secrets/cosign.pub)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS="${ROOT}/secrets"
mkdir -p "$SECRETS"

if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign CLI not found on PATH. Install: https://docs.sigstore.dev/cosign/system_config/installation/" >&2
  exit 1
fi

if [[ -f "${SECRETS}/cosign.key" || -f "${SECRETS}/cosign.pub" ]]; then
  echo "secrets/cosign.key or secrets/cosign.pub already exists — refusing to overwrite." >&2
  echo "Remove them first if you want a new pair." >&2
  exit 1
fi

cd "$SECRETS"
# Empty password for local/dev; production should use vault/KMS.
COSIGN_PASSWORD="" cosign generate-key-pair
echo ""
echo "Wrote:"
echo "  ${SECRETS}/cosign.key  (keep private)"
echo "  ${SECRETS}/cosign.pub  (mount for gateway verify mode)"
echo ""
echo "Next:"
echo "  docker compose -f docker-compose.yml -f docker-compose.module3-verify.yml up -d gateway"
echo "  # and run workers so deny → Module 2 incidents are processed"
echo "  bash scripts/module3_admission_e2e.sh"
