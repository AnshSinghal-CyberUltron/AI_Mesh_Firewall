#!/usr/bin/env bash
# Edge_Regression_Gate (GW16b / B5, L16b-7) — static grep gate for the v2 edge config.
#
# Mirrors scripts/test_nginx_security.sh's always-run static section: no Docker, no
# container, pure source grep over the conf template (the literal directives survive
# envsubst of ${B3_UPSTREAM}). Surfaced to CI via tests/edge/test_edge_config_gate.py.
#
# THE PRIMARY CONTRACT (L16b-7, R7.1/R7.2): the upstream keepalive block is the fix for
# the measured 0.9986-conns/request / 502-above-420-RPS defect. If `keepalive 64` (or a
# keepalive directive in the upstream block) is removed, this gate MUST exit non-zero.
# It also statically asserts the SSE-transparency mechanism, the CWE-1021 unknown-Host
# 403 catch-all, the five security headers, and server_tokens off (R4), so the whole
# Host/header posture is checked with no Docker (R7.3).
set -euo pipefail

# The script lives in gateway_v2/deploy/edge/; resolve its own directory and read the
# sibling conf + include from there (no repo-ROOT assumption needed).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${SCRIPT_DIR}/gateway-edge.conf"
INC="${SCRIPT_DIR}/security-headers.inc"

die() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

[[ -f "${CONF}" ]] || die "missing ${CONF} (the v2 edge config GW16b asserts)"
[[ -f "${INC}" ]] || die "missing ${INC} (the v2 edge security-header include)"

# --- The upstream keepalive block (THE L16b-7 regression contract) -------------------
grep -q -- 'upstream b3_gateway' "${CONF}" \
  || die "gateway-edge.conf missing the named 'upstream b3_gateway' block — GW16b requires a pooled upstream to the B3 gateway (L16b-7)"
# Match the DIRECTIVE, not a comment: a keepalive directive begins the line (only
# leading whitespace before it) and ends with a semicolon. The explanatory comment
# `# keepalive 64 is THE fix ...` must NOT satisfy this gate — only the live directive
# `keepalive 64;` does. This is what makes removing the real directive FAIL the gate.
grep -Eq -- '^[[:space:]]*keepalive[[:space:]]+64;' "${CONF}" \
  || die "gateway-edge.conf missing the 'keepalive 64;' directive in the upstream block — THIS IS THE L16b-7 REGRESSION ASSERTION: removing the keepalive directive reintroduces the 0.9986-conns/request / 502-above-420-RPS defect and MUST fail this gate (GW16b R7.1/R7.2)"
pass "upstream keepalive block present (upstream b3_gateway + keepalive 64) — L16b-7"

# --- keepalive correctness (HTTP/1.1 + cleared hop-by-hop Connection header) ---------
grep -q -- 'proxy_http_version 1.1' "${CONF}" \
  || die "gateway-edge.conf missing 'proxy_http_version 1.1' — GW16b requires HTTP/1.1 for upstream keepalive correctness (R1.2)"
pass "proxy_http_version 1.1 present"

grep -q 'proxy_set_header Connection ""' "${CONF}" \
  || die "gateway-edge.conf missing 'proxy_set_header Connection \"\"' — GW16b requires clearing the hop-by-hop Connection header so nginx reuses the pooled connection (R1.2)"
pass 'proxy_set_header Connection "" present'

# --- SSE transparency MECHANISM (X-Accel-Buffering, not proxy_buffering) --------------
grep -q -- 'X-Accel-Buffering no' "${CONF}" \
  || die "gateway-edge.conf missing 'X-Accel-Buffering no' — GW16b requires X-Accel-Buffering: no as the SSE-transparency mechanism, NOT proxy_buffering (R2.1)"
pass "X-Accel-Buffering no present (SSE transparency mechanism)"

# --- SSE read timeout covering a > 350s stream (L16b-4 config support) ----------------
grep -q -- 'proxy_read_timeout 400s' "${CONF}" \
  || die "gateway-edge.conf missing 'proxy_read_timeout 400s' — GW16b requires an SSE read timeout covering a > 350s stream (R2.5 / L16b-4)"
pass "proxy_read_timeout 400s present (covers a > 350s stream)"

# --- version-leak suppression ---------------------------------------------------------
grep -q -- 'server_tokens off' "${CONF}" \
  || die "gateway-edge.conf missing 'server_tokens off' — GW16b requires the nginx version to be suppressed (R4)"
pass "server_tokens off present"

# --- the security-header include is wired on the named server ------------------------
grep -q -- 'include /etc/nginx/edge/security-headers.inc' "${CONF}" \
  || die "gateway-edge.conf missing 'include /etc/nginx/edge/security-headers.inc' — GW16b requires the security headers on served responses (R4.2)"
pass "security-headers.inc wired into the named server"

# --- CWE-1021 unknown-Host default_server 403 catch-all ------------------------------
grep -q -- 'listen 80 default_server' "${CONF}" \
  || die "gateway-edge.conf missing 'listen 80 default_server' — GW16b requires an unknown-Host catch-all (CWE-1021 Host isolation, R4.1)"
grep -q -- 'return 403' "${CONF}" \
  || die "gateway-edge.conf missing 'return 403' — GW16b requires the unknown-Host catch-all to reject with 403 rather than serve named content (CWE-1021, R4.1)"
pass "default_server unknown-Host 403 catch-all present (CWE-1021)"

# --- the five security headers in the include (loop, mirrors test_nginx_security.sh) --
for key in \
  'X-Content-Type-Options "nosniff"' \
  'X-Frame-Options "DENY"' \
  'Referrer-Policy "strict-origin-when-cross-origin"' \
  'Content-Security-Policy "frame-ancestors '\''none'\''"' \
  'Strict-Transport-Security "max-age=31536000"'
do
  grep -q -- "${key}" "${INC}" \
    || die "security-headers.inc missing ${key} — GW16b requires the five browser security headers (R4.2)"
done
pass "all five security headers present in security-headers.inc"

pass "edge-config static gate GREEN — upstream keepalive (L16b-7), SSE transparency, CWE-1021 Host 403, security headers all present"
