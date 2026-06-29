#!/usr/bin/env bash
# Self-signed cert for Cloudflare SSL mode "Full" (not Full strict).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSL_DIR="${ROOT}/deploy/ssl"
mkdir -p "${SSL_DIR}"

openssl req -x509 -nodes -days 825 \
  -newkey rsa:2048 \
  -keyout "${SSL_DIR}/origin.key" \
  -out "${SSL_DIR}/origin.crt" \
  -subj "/CN=zeroshield.ai/O=ZeroShield/C=IN" \
  -addext "subjectAltName=DNS:aimeshfirewall.zeroshield.ai,DNS:aimeshbackend.zeroshield.ai,DNS:aimeshgateway.zeroshield.ai,DNS:zeroshield.ai"

chmod 600 "${SSL_DIR}/origin.key"
echo "Wrote ${SSL_DIR}/origin.crt and origin.key"
echo "Cloudflare: SSL/TLS → Overview → set mode to Full (not Full strict) unless you use a Cloudflare Origin Certificate."
