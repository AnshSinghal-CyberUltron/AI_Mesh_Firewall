#!/usr/bin/env bash
# Local end-to-end gate for production compose + deploy tooling.
# Does NOT pull from ECR, does NOT SSH to EC2, does NOT bind :80/:443
# (avoids colliding with the live docker compose stack on this host).
#
# Usage:
#   bash scripts/verify_prod_local.sh
#   BUILD_MCP=1 bash scripts/verify_prod_local.sh   # also docker-build mcp broker+sandbox
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

die() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
section() { echo; echo "==> $*"; }

ECR_REGISTRY="${ECR_REGISTRY:-local.ecr.test}"
IMAGE_TAG="${IMAGE_TAG:-local-verify}"
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

COMPOSE_PROD=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
COMPOSE_LOCAL=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.prod.local.yml)

section "1/6 Compose prod config gate"
bash "${ROOT}/scripts/test_docker_compose_prod.sh"
pass "test_docker_compose_prod.sh"

section "2/6 Deploy script syntax"
for s in \
  scripts/deploy-ec2.sh \
  scripts/deploy-full-ec2.sh \
  scripts/sync-to-ec2.sh \
  scripts/ensure_mcp_sandbox_network.sh \
  scripts/test_docker_compose_prod.sh \
  scripts/verify_prod_local.sh \
  scripts/build-prod-images-local.sh \
  infra/scripts/build-push-images.sh \
  infra/scripts/build-frontend-prod.sh
do
  [[ -f "${ROOT}/${s}" ]] || die "missing ${s}"
  bash -n "${ROOT}/${s}" || die "bash -n failed: ${s}"
done
pass "bash -n on deploy scripts"

section "3/6 Required Dockerfiles + nginx hosts"
for f in \
  gateway/Dockerfile \
  control/Dockerfile \
  workers/Dockerfile \
  deploy/Dockerfile.nginx \
  services/mcp-broker/Dockerfile \
  services/mcp-broker/sandbox-image/Dockerfile \
  examples/zeroshield-openai-demo/Dockerfile \
  deploy/nginx.conf \
  deploy/nginx-ssl.conf \
  .env.ec2.sample \
  docker-compose.yml \
  docker-compose.prod.yml \
  docker-compose.prod.local.yml
do
  [[ -f "${ROOT}/${f}" ]] || die "missing ${f}"
done
for host in aimeshfirewall.zeroshield.ai aimeshbackend.zeroshield.ai aimeshgateway.zeroshield.ai; do
  grep -q "server_name ${host}" "${ROOT}/deploy/nginx.conf" || die "nginx.conf missing ${host}"
  grep -q "server_name ${host}" "${ROOT}/deploy/nginx-ssl.conf" || die "nginx-ssl.conf missing ${host}"
done
grep -q 'location /demo/' "${ROOT}/deploy/nginx.conf" || die "nginx.conf missing /demo/"
grep -q 'location /demo/' "${ROOT}/deploy/nginx-ssl.conf" || die "nginx-ssl.conf missing /demo/ (HTTPS must proxy demo)"
grep -q 'auth_basic' "${ROOT}/deploy/nginx.conf" && grep -A5 'location /demo/' "${ROOT}/deploy/nginx.conf" | grep -q 'auth_basic' \
  && die "nginx.conf /demo/ must not use auth_basic (in-app login)"
pass "Dockerfiles + nginx server_name hosts + /demo HTTP/HTTPS"

section "4/6 Prod.local overlay renders (non-conflicting ports)"
"${COMPOSE_LOCAL[@]}" config >/tmp/prod-local-merged.yml \
  || die "prod.local compose config failed"
python3 - <<'PY'
import yaml
d = yaml.safe_load(open("/tmp/prod-local-merged.yml"))
svcs = d["services"]
need = [
    "postgres", "redis", "rabbitmq", "control", "gateway", "workers",
    "workers-beat", "mcp-broker", "mcp-sandbox-image", "guardrails",
    "vector-retrieval", "nginx", "demo",
]
missing = [n for n in need if n not in svcs]
if missing:
    raise SystemExit(f"prod.local missing services: {missing}")
nginx_ports = svcs["nginx"].get("ports") or []
published = set()
for p in nginx_ports:
    if isinstance(p, dict):
        published.add(str(p.get("published")))
    elif isinstance(p, str):
        published.add(p.split(":")[0])
# local overlay must NOT steal host :80/:443 from a running stack
if "80" in published or "443" in published:
    raise SystemExit(f"prod.local nginx still publishes 80/443: {nginx_ports}")
if "18080" not in published:
    raise SystemExit(f"prod.local nginx expected published 18080, got {nginx_ports}")
# pull_policy never so local tags work offline
for name in ("control", "gateway", "nginx", "mcp-broker"):
    pp = (svcs[name].get("pull_policy") or "").lower()
    if pp not in ("never", "build", ""):
        # empty is ok if image is local; prefer never
        pass
print("PASS: prod.local ports + service set OK")
PY
pass "docker-compose.prod.local.yml"

section "5/6 Makefile deploy targets exist"
grep -q '^deploy-full-ec2:' Makefile || die "Makefile missing deploy-full-ec2"
grep -q '^test-prod-compose:' Makefile || die "Makefile missing test-prod-compose"
grep -q '^verify-prod-local:' Makefile || die "Makefile missing verify-prod-local"
grep -q '^ecr-push:' Makefile || die "Makefile missing ecr-push"
grep -q '^build-prod-images-local:' Makefile || die "Makefile missing build-prod-images-local"
grep -q '^up-prod-local:' Makefile || die "Makefile missing up-prod-local"
grep -q '^down-prod-local:' Makefile || die "Makefile missing down-prod-local"
grep -q 'ensure_mcp_sandbox_network' scripts/sync-to-ec2.sh || die "sync-to-ec2.sh must sync ensure_mcp_sandbox_network.sh"
pass "Makefile targets + sync MCP network script"

section "6/6 Optional MCP image builds (BUILD_MCP=1)"
if [[ "${BUILD_MCP:-0}" == "1" ]]; then
  bash "${ROOT}/scripts/build-prod-images-local.sh" --mcp-only
  pass "local mcp-broker + mcp-sandbox images built"
else
  echo "SKIP: set BUILD_MCP=1 to docker-build mcp broker/sandbox locally"
fi

echo
echo "============================================================"
echo " verify_prod_local: ALL CHECKS PASSED"
echo " Prod hosts: ${FRONTEND_HOST} | ${BACKEND_HOST} | ${GATEWAY_HOST}"
echo " Next (real EC2 deploy): make deploy-full-ec2 TAG=<tag>"
echo " Local prod-like up (needs images): make build-prod-images-local && make up-prod-local"
echo "============================================================"
