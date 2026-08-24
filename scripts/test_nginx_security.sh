#!/usr/bin/env bash
# Regression gate for origin nginx security headers / Host isolation / version leak.
# Static (source) checks always run. Optional: NGINX_DOCKER=1 (default if docker
# exists) runs nginx -t plus a throwaway container that proves SPA /login headers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

die() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

HTTP="${ROOT}/deploy/nginx.conf"
SSL="${ROOT}/deploy/nginx-ssl.conf"
INC="${ROOT}/deploy/security-headers.inc"
EDGE="${ROOT}/deploy/edge-error.inc"
EDGE_HTTPS="${ROOT}/deploy/require-edge-https.inc"
OPENAPI_DENY="${ROOT}/deploy/deny-public-openapi.inc"
DF="${ROOT}/deploy/Dockerfile.nginx"

[[ -f "${HTTP}" ]] || die "missing ${HTTP}"
[[ -f "${SSL}" ]] || die "missing ${SSL}"
[[ -f "${INC}" ]] || die "missing ${INC} (shared header snippet)"
[[ -f "${EDGE}" ]] || die "missing ${EDGE}"
[[ -f "${EDGE_HTTPS}" ]] || die "missing ${EDGE_HTTPS} (CWE-319 cleartext gate)"
[[ -f "${OPENAPI_DENY}" ]] || die "missing ${OPENAPI_DENY} (CDL #12 gateway OpenAPI gate)"
[[ -f "${DF}" ]] || die "missing ${DF}"

grep -q 'server_tokens off;' "${HTTP}" || die "deploy/nginx.conf missing server_tokens off;"

for key in \
  'X-Content-Type-Options "nosniff"' \
  'X-Frame-Options "DENY"' \
  'Referrer-Policy "strict-origin-when-cross-origin"' \
  'Content-Security-Policy "frame-ancestors '\''none'\''"' \
  'Strict-Transport-Security "max-age=31536000"'
do
  grep -q -- "${key}" "${INC}" || die "security-headers.inc missing ${key}"
done
# Do not expand CSP into script-src / upgrade-insecure-requests here: the SPA
# is a hashed-asset Vite shell; a full CSP belongs to a dedicated iteration.
grep -q 'upgrade-insecure-requests' "${INC}" \
  && die "security-headers.inc must not add upgrade-insecure-requests (false #11 fix; keep frame-ancestors-only)"

grep -q 'COPY deploy/security-headers.inc /etc/nginx/security-headers.inc' "${DF}" \
  || die "Dockerfile.nginx must COPY security-headers.inc next to edge-error.inc (not into conf.d/)"
grep -q 'COPY deploy/require-edge-https.inc /etc/nginx/require-edge-https.inc' "${DF}" \
  || die "Dockerfile.nginx must COPY require-edge-https.inc"
grep -q 'COPY deploy/deny-public-openapi.inc /etc/nginx/deny-public-openapi.inc' "${DF}" \
  || die "Dockerfile.nginx must COPY deny-public-openapi.inc"
grep -E 'COPY deploy/security-headers.inc .*/conf.d/' "${DF}" \
  && die "security-headers.inc must not land in conf.d/ (auto-loaded as a vhost)"
grep -c 'include /etc/nginx/require-edge-https.inc;' "${HTTP}" | grep -qx 3 \
    || die "nginx.conf must include require-edge-https.inc on all 3 named HTTP vhosts"
grep -q 'include /etc/nginx/deny-public-openapi.inc;' "${HTTP}" \
  || die "nginx.conf must include deny-public-openapi.inc on the gateway vhost"
grep -q 'include /etc/nginx/deny-public-openapi.inc;' "${SSL}" \
  || die "nginx-ssl.conf must include deny-public-openapi.inc on the gateway vhost"
awk '
  /listen[[:space:]]+80[[:space:]]+default_server/ { in_def=1 }
  in_def {
    if ($0 ~ /require-edge-https/) { found=1 }
    brace += gsub(/{/, "&") - gsub(/}/, "&")
    if (in_def && brace <= 0 && /}/) { in_def=0 }
  }
  END { exit found ? 0 : 1 }
' "${HTTP}" && die "HTTP default_server must NOT include require-edge-https.inc (ALB health)"

grep -q 'PROTO_HDR="X-Forwarded-Proto: https"' "${ROOT}/scripts/deploy-ec2.sh" \
  || die "deploy-ec2.sh named-host :80 probes must send X-Forwarded-Proto: https (ALB sim)"

grep -q 'include /etc/nginx/security-headers.inc;' "${HTTP}" \
  || die "nginx.conf must include /etc/nginx/security-headers.inc at http level"

# Every location that sets add_header must re-include the snippet in the same
# block (nginx drops inherited add_header). Cache-Control on SPA / is the live bug.
python3 - <<'PY' || die "add_header locations missing security-headers.inc"
from pathlib import Path
import re, sys

files = [
    Path("deploy/nginx.conf"),
    Path("deploy/nginx-ssl.conf"),
    Path("deploy/edge-error.inc"),
]
missing = []
for path in files:
    text = path.read_text()
    # Split on location blocks (non-greedy until matching close is hard; scan
    # each "location ... { ... }" at one brace depth).
    i = 0
    while True:
        m = re.search(r"location\s+[^{]+\{", text[i:])
        if not m:
            break
        start = i + m.start()
        brace = i + m.end() - 1
        depth = 0
        j = brace
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = text[start : j + 1]
        i = j + 1
        if "add_header" not in block:
            continue
        if "include /etc/nginx/security-headers.inc;" not in block:
            first = block.splitlines()[0].strip()
            missing.append(f"{path}: {first}")

if missing:
    print("locations with add_header but no security-headers.inc:")
    for row in missing:
        print(" -", row)
    sys.exit(1)
print("ok")
PY

# Unknown-Host default_server must not try_files the SPA.
if awk '
  BEGIN { in_def=0; brace=0 }
  /listen[[:space:]]+80[[:space:]]+default_server/ { in_def=1 }
  in_def {
    if ($0 ~ /try_files/ && $0 ~ /index\.html/) { found=1 }
    brace += gsub(/{/, "&") - gsub(/}/, "&")
    if (in_def && brace <= 0 && /}/) { in_def=0 }
  }
  END { exit found ? 0 : 1 }
' "${HTTP}"; then
  die "HTTP default_server still try_files /index.html (unknown Host must not serve the SPA)"
fi

grep -q 'listen 443 ssl default_server' "${SSL}" \
  || die "nginx-ssl.conf missing listen 443 ssl default_server (unknown Host on :443)"
if grep -A80 'listen 443 ssl default_server' "${SSL}" | grep -q 'try_files'; then
  die "HTTPS default_server must not try_files the SPA"
fi

# Named SPA location / must send Cache-Control AND include the snippet.
python3 - <<'PY' || die "named SPA location / missing Cache-Control + snippet"
from pathlib import Path
import re, sys

def spa_ok(path: str) -> bool:
    text = Path(path).read_text()
    # First server block for aimeshfirewall
    m = re.search(
        r"server_name aimeshfirewall\.zeroshield\.ai;.*?location / \{(.*?)\}",
        text,
        re.S,
    )
    if not m:
        print(f"no SPA location / in {path}")
        return False
    body = m.group(1)
    if "include /etc/nginx/security-headers.inc;" not in body:
        print(f"{path} SPA / missing snippet")
        return False
    if "Cache-Control" not in body:
        print(f"{path} SPA / missing Cache-Control")
        return False
    return True

ok = spa_ok("deploy/nginx.conf") and spa_ok("deploy/nginx-ssl.conf")
sys.exit(0 if ok else 1)
PY

pass "source gates (server_tokens, snippet, SPA /, default_server)"

# Optional live nginx -t + header proof.
if [[ "${SKIP_NGINX_DOCKER:-}" == "1" ]]; then
  pass "skipped docker nginx -t (SKIP_NGINX_DOCKER=1)"
  exit 0
fi
if ! command -v docker >/dev/null 2>&1; then
  pass "skipped docker nginx -t (docker not available)"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
mkdir -p "${TMP}/html" "${TMP}/ssl" "${TMP}/edge"
printf '%s\n' '<!doctype html><title>login shell</title>' > "${TMP}/html/index.html"
# Minimal origin cert so 02-ssl.conf parses.
openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
  -keyout "${TMP}/ssl/origin.key" -out "${TMP}/ssl/origin.crt" \
  -subj "/CN=zeroshield.ai" >/dev/null 2>&1
cp "${ROOT}/deploy/edge-error.json" "${TMP}/edge/__edge_error.json"

# Stock alpine image ships default.conf and resolves proxy_pass hosts at start.
# Prod Dockerfile.nginx deletes default.conf; stub Docker DNS names here.
NGINX_MOUNTS=(
  -v "${HTTP}:/etc/nginx/conf.d/01-http.conf:ro"
  -v "${SSL}:/etc/nginx/conf.d/02-ssl.conf:ro"
  -v "${INC}:/etc/nginx/security-headers.inc:ro"
  -v "${EDGE}:/etc/nginx/edge-error.inc:ro"
  -v "${EDGE_HTTPS}:/etc/nginx/require-edge-https.inc:ro"
  -v "${OPENAPI_DENY}:/etc/nginx/deny-public-openapi.inc:ro"
  -v "${TMP}/edge:/etc/nginx/edge_errors:ro"
  -v "${TMP}/html:/usr/share/nginx/html:ro"
  -v "${TMP}/ssl:/etc/nginx/ssl:ro"
  --add-host control:127.0.0.1
  --add-host gateway:127.0.0.1
  --add-host demo:127.0.0.1
)

docker run --rm "${NGINX_MOUNTS[@]}" nginx:1.30-alpine \
  sh -c 'rm -f /etc/nginx/conf.d/default.conf && nginx -t' \
  || die "nginx -t failed with baked-path includes"

# Header proof: start nginx, curl named Host /login vs unknown Host /login.
cid="$(docker run -d --rm \
  "${NGINX_MOUNTS[@]}" \
  -p 127.0.0.1:18091:80 \
  -p 127.0.0.1:18453:443 \
  nginx:1.30-alpine \
  sh -c 'rm -f /etc/nginx/conf.d/default.conf && nginx -g "daemon off;"')"
cleanup_cid() { docker rm -f "${cid}" >/dev/null 2>&1 || true; }
trap 'cleanup_cid; rm -rf "${TMP}"' EXIT

for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -sf -o /dev/null -H "Host: 127.0.0.1" "http://127.0.0.1:18091/" && break
  sleep 0.2
done

# CWE-319: cleartext named-vhost GET /login must 301 to HTTPS; POST /api/auth
# must 403 (not proxy). ALB-like requests send X-Forwarded-Proto: https from a
# private address (docker bridge is RFC1918 → trusted in the geo map).
clear_login="$(curl -sI -H "Host: aimeshfirewall.zeroshield.ai" "http://127.0.0.1:18091/login")"
echo "${clear_login}" | grep -qE '^HTTP/1\.[01] 301' \
  || die "cleartext GET /login expected 301, got:"$'\n'"${clear_login}"
echo "${clear_login}" | grep -qi '^location: https://aimeshfirewall.zeroshield.ai/login' \
  || die "cleartext GET /login missing Location https://…/login"$'\n'"${clear_login}"

clear_post="$(curl -sD - -o /dev/null -X POST -H "Host: aimeshfirewall.zeroshield.ai" \
  -H "Content-Type: application/json" --data-raw '{"email":"x","password":"y"}' \
  "http://127.0.0.1:18091/api/auth/token/")"
echo "${clear_post}" | grep -qE '^HTTP/1\.[01] 403' \
  || die "cleartext POST /api/auth/token/ expected 403, got:"$'\n'"${clear_post}"

edge_post="$(curl -sD - -o /dev/null -X POST -H "Host: aimeshfirewall.zeroshield.ai" \
  -H "X-Forwarded-Proto: https" -H "Content-Type: application/json" \
  --data-raw '{"email":"x","password":"y"}' \
  "http://127.0.0.1:18091/api/auth/token/")"
echo "${edge_post}" | grep -qE '^HTTP/1\.[01] 403' \
  && die "ALB-like POST /api/auth/token/ must not 403 (need to reach proxy), got:"$'\n'"${edge_post}"

hdrs="$(curl -sI -H "Host: aimeshfirewall.zeroshield.ai" \
  -H "X-Forwarded-Proto: https" "http://127.0.0.1:18091/login")"
echo "${hdrs}" | grep -qi '^x-frame-options: DENY' || die "/login missing X-Frame-Options: DENY"$'\n'"${hdrs}"
echo "${hdrs}" | grep -qi '^x-content-type-options: nosniff' || die "/login missing X-Content-Type-Options"
echo "${hdrs}" | grep -qi '^content-security-policy:.*frame-ancestors' || die "/login missing CSP frame-ancestors"
echo "${hdrs}" | grep -qi '^strict-transport-security: max-age=31536000' || die "/login missing HSTS"
echo "${hdrs}" | grep -qi '^referrer-policy: strict-origin-when-cross-origin' || die "/login missing Referrer-Policy"
echo "${hdrs}" | grep -qi '^cache-control:.*no-store' || die "/login missing Cache-Control no-store"
if echo "${hdrs}" | grep -qi '^server: nginx/'; then
  die "Server still discloses version: ${hdrs}"
fi
echo "${hdrs}" | grep -qi '^server: nginx' || die "expected Server: nginx (tokens off, name remains)"

get_body_hdrs="$(curl -sD - -o /dev/null -H "Host: aimeshfirewall.zeroshield.ai" \
  -H "X-Forwarded-Proto: https" "http://127.0.0.1:18091/login")"
echo "${get_body_hdrs}" | grep -qi '^x-frame-options: DENY' || die "GET /login missing X-Frame-Options"$'\n'"${get_body_hdrs}"

tls_hdrs="$(curl -skI -H "Host: aimeshfirewall.zeroshield.ai" "https://127.0.0.1:18453/login")"
echo "${tls_hdrs}" | grep -qi '^x-frame-options: DENY' || die "HTTPS /login missing X-Frame-Options"$'\n'"${tls_hdrs}"
echo "${tls_hdrs}" | grep -qi '^strict-transport-security:' || die "HTTPS /login missing HSTS"
if echo "${tls_hdrs}" | grep -qi '^server: nginx/'; then
  die "HTTPS Server still discloses version: ${tls_hdrs}"
fi

tls_unk="$(curl -skI -H "Host: evil.example" "https://127.0.0.1:18453/login")"
echo "${tls_unk}" | grep -qE '^HTTP/2 403|^HTTP/1\.[01] 403' || die "HTTPS unknown Host /login expected 403, got:"$'\n'"${tls_unk}"

unk="$(curl -sI -H "Host: evil.example" "http://127.0.0.1:18091/login")"
echo "${unk}" | grep -qE '^HTTP/1\.[01] 403' || die "unknown Host /login expected 403, got:"$'\n'"${unk}"

okroot="$(curl -sI -H "Host: 127.0.0.1" "http://127.0.0.1:18091/")"
echo "${okroot}" | grep -qE '^HTTP/1\.[01] 200' || die "default_server GET / expected 200 for ALB health, got:"$'\n'"${okroot}"

gw_oa="$(curl -sI -H "Host: aimeshgateway.zeroshield.ai" \
  -H "X-Forwarded-Proto: https" "http://127.0.0.1:18091/openapi.json")"
echo "${gw_oa}" | grep -qE '^HTTP/1\.[01] 401' \
  || die "gateway /openapi.json expected 401, got:"$'\n'"${gw_oa}"
gw_docs="$(curl -sI -H "Host: aimeshgateway.zeroshield.ai" \
  -H "X-Forwarded-Proto: https" "http://127.0.0.1:18091/docs")"
echo "${gw_docs}" | grep -qE '^HTTP/1\.[01] 401' \
  || die "gateway /docs expected 401, got:"$'\n'"${gw_docs}"

pass "docker nginx -t + /login headers + unknown-Host 403 + gateway OpenAPI 401"
