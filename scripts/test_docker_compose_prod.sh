#!/usr/bin/env bash
# Validate docker-compose.prod.yml against docker-compose.yml (config merge + URL parity).
# Does NOT start containers or pull images. Safe to run on any host with Docker Compose v2+.
#
# Usage:
#   ECR_REGISTRY=123456789.dkr.ecr.ap-south-1.amazonaws.com IMAGE_TAG=test \
#     bash scripts/test_docker_compose_prod.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

ECR_REGISTRY="${ECR_REGISTRY:-123456789.dkr.ecr.ap-south-1.amazonaws.com}"
IMAGE_TAG="${IMAGE_TAG:-test-compose-prod}"
# Match deploy-ec2.sh URL exports so local .env cannot shadow prod defaults under test.
export ECR_REGISTRY IMAGE_TAG
export FRONTEND_HOST="${FRONTEND_HOST:-aimeshfirewall.zeroshield.ai}"
export BACKEND_HOST="${BACKEND_HOST:-aimeshbackend.zeroshield.ai}"
export GATEWAY_HOST="${GATEWAY_HOST:-aimeshgateway.zeroshield.ai}"
export FRONTEND_ORIGIN="${FRONTEND_ORIGIN:-https://${FRONTEND_HOST}}"
export BACKEND_PUBLIC_URL="${BACKEND_PUBLIC_URL:-https://${BACKEND_HOST}}"
export GATEWAY_PUBLIC_URL="${GATEWAY_PUBLIC_URL:-https://${GATEWAY_HOST}}"
export GATEWAY_CORS_ORIGINS="${GATEWAY_CORS_ORIGINS:-${FRONTEND_ORIGIN},${BACKEND_PUBLIC_URL},${GATEWAY_PUBLIC_URL}}"
export ASGI_ALLOWED_ORIGINS="${ASGI_ALLOWED_ORIGINS:-${FRONTEND_ORIGIN},${BACKEND_PUBLIC_URL},${GATEWAY_PUBLIC_URL}}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-${FRONTEND_ORIGIN},${BACKEND_PUBLIC_URL},${GATEWAY_PUBLIC_URL}}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-${BACKEND_HOST},${FRONTEND_HOST},${GATEWAY_HOST},localhost,127.0.0.1,control,gateway}"
export DEBUG=false

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

echo "==> Render merged compose config"
MERGED="$(mktemp)"
trap 'rm -f "${MERGED}"' EXIT
"${COMPOSE[@]}" config >"${MERGED}" 2>/tmp/compose-prod-err.txt \
  || { cat /tmp/compose-prod-err.txt >&2; die "compose config failed"; }
pass "compose config renders"

echo "==> Service parity vs base (always-on product path)"
# Every base service must appear in the merged file (including profile-gated ones
# when their profiles are requested). Default merge must include the prod stack.
mapfile -t DEFAULT_SVCS < <("${COMPOSE[@]}" config --services | sort)
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx postgres || die "postgres missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx redis || die "redis missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx rabbitmq || die "rabbitmq missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx control || die "control missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx gateway || die "gateway missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx workers || die "workers missing (prod must clear workers profile)"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx workers-beat || die "workers-beat missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx mcp-broker || die "mcp-broker missing (prod must clear services profile)"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx mcp-sandbox-image || die "mcp-sandbox-image missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx guardrails || die "guardrails missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx vector-retrieval || die "vector-retrieval missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx nginx || die "nginx missing"
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx demo || die "demo missing"
# Dev Vite frontend must NOT be in default prod up
printf '%s\n' "${DEFAULT_SVCS[@]}" | grep -qx frontend && die "frontend should stay behind dev-frontend profile" || true
pass "default prod services present ($(echo "${DEFAULT_SVCS[@]}" | wc -w) services)"

echo "==> Optional profiles still resolve (parity with base)"
for profile in chroma telemetry-pilot mcp-stub transport-stubs; do
  "${COMPOSE[@]}" --profile "${profile}" config --services >/dev/null \
    || die "profile ${profile} failed to render"
done
pass "optional profiles chroma/telemetry-pilot/mcp-stub/transport-stubs render"

echo "==> Production URL env defaults"
python3 - "${MERGED}" <<'PY'
import sys, yaml

path = sys.argv[1]
d = yaml.safe_load(open(path))
services = d["services"]

def env_of(name):
    s = services.get(name) or {}
    env = s.get("environment") or {}
    if isinstance(env, list):
        out = {}
        for item in env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                out[k] = v
            elif isinstance(item, dict):
                out.update(item)
        return out
    return dict(env)

FE = "https://aimeshfirewall.zeroshield.ai"
BE = "https://aimeshbackend.zeroshield.ai"
GW = "https://aimeshgateway.zeroshield.ai"
errors = []

ctrl = env_of("control")
gw = env_of("gateway")

checks = [
    ("control", ctrl, "FRONTEND_ORIGIN", FE),
    ("control", ctrl, "BACKEND_PUBLIC_URL", BE),
    ("control", ctrl, "GATEWAY_PUBLIC_URL", GW),
    ("control", ctrl, "DEBUG", "false"),
    ("control", ctrl, "GATEWAY_URL", "http://gateway:8300"),
    ("gateway", gw, "FRONTEND_ORIGIN", FE),
    ("gateway", gw, "GATEWAY_PUBLIC_URL", GW),
    ("gateway", gw, "MCP_BROKER_URL", "http://mcp-broker:8311"),
    ("gateway", gw, "MCP_STDIO_IN_PROCESS", "false"),
    ("gateway", gw, "GUARDRAILS_SERVICE_URL", "http://guardrails:8310"),
    ("gateway", gw, "VECTOR_SERVICE_URL", "http://vector-retrieval:8312"),
]
for svc, env, key, expect in checks:
    got = (env.get(key) or "").strip()
    if got != expect:
        errors.append(f"{svc}.{key}: expected {expect!r}, got {got!r}")

# ALLOWED_HOSTS / CORS must include all three public hosts
for key in ("ALLOWED_HOSTS", "ASGI_ALLOWED_ORIGINS", "CORS_ALLOWED_ORIGINS"):
    val = ctrl.get(key) or ""
    for host in ("aimeshfirewall.zeroshield.ai", "aimeshbackend.zeroshield.ai", "aimeshgateway.zeroshield.ai"):
        if host not in val:
            errors.append(f"control.{key} missing {host}: {val!r}")

cors = gw.get("GATEWAY_CORS_ORIGINS") or ""
for origin in (FE, BE, GW):
    if origin not in cors:
        errors.append(f"gateway.GATEWAY_CORS_ORIGINS missing {origin}: {cors!r}")

# ECR images (no local build) for core deployables
for name, repo in (
    ("control", "ai-mesh-control"),
    ("gateway", "ai-mesh-gateway"),
    ("workers", "ai-mesh-workers"),
    ("nginx", "ai-mesh-nginx"),
    ("mcp-broker", "ai-mesh-mcp-broker"),
    ("mcp-sandbox-image", "ai-mesh-mcp-sandbox"),
):
    img = (services.get(name) or {}).get("image") or ""
    if repo not in img:
        errors.append(f"{name}.image missing {repo}: {img!r}")
    if (services.get(name) or {}).get("build"):
        errors.append(f"{name} still has build: (prod must pull from ECR)")

# nginx publishes 80+443
nginx_ports = (services.get("nginx") or {}).get("ports") or []
published = set()
for p in nginx_ports:
    if isinstance(p, dict):
        published.add(str(p.get("published")))
    else:
        published.add(str(p).split(":")[0] if isinstance(p, str) else "")
if "80" not in published or "443" not in published:
    errors.append(f"nginx ports must publish 80 and 443, got {nginx_ports!r}")

# control must NOT hardcode gunicorn command (entrypoint owns workers)
ctrl_cmd = (services.get("control") or {}).get("command")
if ctrl_cmd:
    errors.append(f"control.command should be unset (use entrypoint); got {ctrl_cmd!r}")

if errors:
    print("URL/env parity failures:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("PASS: production URL + ECR + CORS env parity")
PY

echo "==> nginx baked config lists all three production hosts + /demo"
# Prefer repo source of truth (baked into ai-mesh-nginx at image build)
for host in aimeshfirewall.zeroshield.ai aimeshbackend.zeroshield.ai aimeshgateway.zeroshield.ai; do
  grep -q "server_name ${host}" deploy/nginx.conf \
    || die "deploy/nginx.conf missing server_name ${host}"
done
grep -q 'location /demo/' deploy/nginx.conf || die "deploy/nginx.conf missing /demo/"
grep -q 'location /demo/' deploy/nginx-ssl.conf || die "deploy/nginx-ssl.conf missing /demo/"
SKIP_NGINX_DOCKER=1 bash "${ROOT}/scripts/test_nginx_security.sh" || die "test_nginx_security.sh"
pass "deploy/nginx.conf + nginx-ssl.conf have hosts and /demo/"

echo "==> Base service inventory covered by prod overlay or explicit profile"
python3 - <<'PY'
import subprocess, os, yaml, sys
os.environ.setdefault("ECR_REGISTRY", "123456789.dkr.ecr.ap-south-1.amazonaws.com")
os.environ.setdefault("IMAGE_TAG", "test")
root = "."
base = subprocess.check_output(
    ["docker", "compose", "-f", "docker-compose.yml",
     "--profile", "workers", "--profile", "services", "--profile", "chroma",
     "--profile", "transport-stubs", "--profile", "telemetry-pilot",
     "--profile", "mcp-stub", "--profile", "dev-frontend",
     "config"],
    text=True,
)
# Note: frontend profile is named in prod overlay as dev-frontend; base frontend has no profile.
# Re-render base without inventing a profile that doesn't exist:
base = subprocess.check_output(
    ["docker", "compose", "-f", "docker-compose.yml",
     "--profile", "workers", "--profile", "services", "--profile", "chroma",
     "--profile", "transport-stubs", "--profile", "telemetry-pilot",
     "config"],
    text=True,
)
prod = subprocess.check_output(
    ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
     "--profile", "chroma", "--profile", "transport-stubs", "--profile", "telemetry-pilot",
     "--profile", "mcp-stub", "--profile", "dev-frontend",
     "config"],
    text=True,
)
base_svcs = set(yaml.safe_load(base)["services"])
prod_svcs = set(yaml.safe_load(prod)["services"])
# frontend exists in base always; in prod only with dev-frontend profile
missing = sorted(base_svcs - prod_svcs)
# demo/nginx are prod-only additions — fine
extra = sorted(prod_svcs - base_svcs)
if missing:
    print("FAIL: base services absent from prod merge:", missing)
    sys.exit(1)
print("PASS: all base services present in prod merge; prod-only extras:", extra)
PY

echo
echo "ALL CHECKS PASSED"
echo "Default prod services:"
printf '  %s\n' "${DEFAULT_SVCS[@]}"
