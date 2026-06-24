#!/usr/bin/env bash
# Pull ECR images and start full stack on EC2 (c8g.2xlarge / private VPC).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not installed"
command -v aws >/dev/null 2>&1 || die "aws CLI not installed (needed for ECR login)"

_ensure_compose() {
  if docker compose version >/dev/null 2>&1; then
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    return 0
  fi
  echo "==> Installing Docker Compose v2 plugin (one-time)"
  local arch plugin_dir="/usr/local/lib/docker/cli-plugins"
  arch="$(uname -m)"
  case "${arch}" in
    aarch64|arm64) arch="aarch64" ;;
    x86_64|amd64) arch="x86_64" ;;
    *) die "unsupported arch ${arch} for compose install" ;;
  esac
  if command -v sudo >/dev/null 2>&1; then
    sudo mkdir -p "${plugin_dir}"
    sudo curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${arch}" \
      -o "${plugin_dir}/docker-compose"
    sudo chmod +x "${plugin_dir}/docker-compose"
  else
    mkdir -p "${HOME}/.docker/cli-plugins"
    curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${arch}" \
      -o "${HOME}/.docker/cli-plugins/docker-compose"
    chmod +x "${HOME}/.docker/cli-plugins/docker-compose"
  fi
  docker compose version >/dev/null 2>&1 || die "Docker Compose install failed"
}

_ensure_compose

if [[ ! -f .env ]]; then
  [[ -f .env.ec2.sample ]] && cp .env.ec2.sample .env && die "Created .env — set ECR_REGISTRY, IMAGE_TAG, secrets, then re-run"
  die "missing .env"
fi

set -a && source .env && set +a

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f docker-compose.yml -f docker-compose.prod.yml)
else
  die "Docker Compose not available after install"
fi

# Optional CloudWatch app log shipping (requires log groups + IAM from terraform ec2-demo)
if [[ "${EC2_OBSERVABILITY:-0}" == "1" ]] && [[ -f docker-compose.observability.yml ]]; then
  COMPOSE+=(-f docker-compose.observability.yml)
  echo "==> Observability overlay enabled (awslogs → CloudWatch)"
fi

bash scripts/bootstrap-ec2-observability.sh || true
bash scripts/publish-stack-ready-metric.sh 0 || true

[[ -n "${ECR_REGISTRY:-}" ]] || die "set ECR_REGISTRY in .env"
[[ -n "${IMAGE_TAG:-}" ]] || die "set IMAGE_TAG in .env"
# control signs compiled policy bundles with POLICY_SIGNING_KEY and the gateway
# verifies them (docker-compose.yml passes it to control/gateway/workers).
[[ -n "${POLICY_SIGNING_KEY:-}" ]] || die "set POLICY_SIGNING_KEY in .env (required by control + gateway for policy bundle signing)"

export FRONTEND_ORIGIN="${FRONTEND_ORIGIN:-https://${FRONTEND_HOST:-aimeshfirewall.zeroshield.ai}}"
export BACKEND_PUBLIC_URL="${BACKEND_PUBLIC_URL:-https://${BACKEND_HOST:-aimeshbackend.zeroshield.ai}}"
export GATEWAY_PUBLIC_URL="${GATEWAY_PUBLIC_URL:-https://${GATEWAY_HOST:-aimeshgateway.zeroshield.ai}}"
export GATEWAY_CORS_ORIGINS="${GATEWAY_CORS_ORIGINS:-${FRONTEND_ORIGIN},${BACKEND_PUBLIC_URL}}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-${BACKEND_HOST},${FRONTEND_HOST},${GATEWAY_HOST},localhost,127.0.0.1,control}"

# Production guard: Django ALLOWED_HOSTS must be a concrete host list — never
# empty and never the '*' wildcard (Host-header spoofing / cache poisoning).
_validate_allowed_hosts() {
  local hosts="${ALLOWED_HOSTS//[[:space:]]/}"
  if [[ -z "${hosts}" ]]; then
    die "ALLOWED_HOSTS is empty — set a comma-separated host list in .env (e.g. ALLOWED_HOSTS=aimeshbackend.zeroshield.ai,aimeshfirewall.zeroshield.ai,aimeshgateway.zeroshield.ai)"
  fi
  local -a entries
  IFS=',' read -r -a entries <<< "${hosts}"
  local entry
  for entry in "${entries[@]}"; do
    if [[ "${entry}" == "*" ]]; then
      die "ALLOWED_HOSTS contains '*' — wildcard Host headers are not allowed in production deploys; list explicit hosts in .env"
    fi
  done
}
_validate_allowed_hosts

VITE_FRONTEND_BASE_URL="${VITE_FRONTEND_BASE_URL:-$FRONTEND_ORIGIN}"
VITE_BACKEND_BASE_URL="${VITE_BACKEND_BASE_URL:-$BACKEND_PUBLIC_URL}"
# Dedicated gateway host for browser fetch + UI display (CORS allow-list includes firewall origin).
VITE_GATEWAY_SAME_ORIGIN="${VITE_GATEWAY_SAME_ORIGIN:-false}"
VITE_GATEWAY_BASE_URL="${VITE_GATEWAY_BASE_URL:-$GATEWAY_PUBLIC_URL}"

REGION="${AWS_REGION:-ap-south-1}"

_has_instance_role() {
  local arn
  arn="$(AWS_ACCESS_KEY_ID= AWS_SECRET_ACCESS_KEY= AWS_SESSION_TOKEN= \
    aws sts get-caller-identity --query Arn --output text 2>/dev/null || true)"
  [[ "${arn}" == *":assumed-role/"* ]]
}

# Quarantined/compromised IAM users (e.g. AWSCompromisedKeyQuarantineV3) cannot call ECR.
# On EC2, use an instance profile instead of BedrockAPIKey-* user keys in .env.
if [[ "${USE_EC2_INSTANCE_ROLE:-true}" == "true" ]] && _has_instance_role; then
  echo "==> EC2 instance IAM role detected — using it for ECR (not static .env keys)"
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  if grep -qE '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)=' .env 2>/dev/null; then
    # Strip static AWS keys into a temp FIRST so the backup never retains live
    # credentials, then install the sanitized copy as both backup and .env.
    ENV_STRIPPED="$(mktemp .env.stripped.XXXXXX)"
    sed -E '/^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)=/d' .env > "${ENV_STRIPPED}"
    ENV_BACKUP=".env.bak.$(date +%s)"
    cp -f "${ENV_STRIPPED}" "${ENV_BACKUP}"
    chmod 600 "${ENV_BACKUP}"
    mv -f "${ENV_STRIPPED}" .env
    chmod 600 .env
    # Prune old backups: always keep the newest 3, delete the rest once older than 7 days.
    ls -1t .env.bak.* 2>/dev/null | tail -n +4 | while IFS= read -r _old_bak; do
      find "${_old_bak}" -maxdepth 0 -type f -mtime +7 -exec rm -f {} \; 2>/dev/null
    done || true
    set -a && source .env && set +a
  fi
elif [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]; then
  echo "==> Using AWS_ACCESS_KEY_ID from environment/.env for ECR"
  if ! aws sts get-caller-identity --region "${REGION}" >/dev/null 2>&1; then
    die "AWS credentials in .env are invalid. Attach an EC2 instance role (deploy/ec2-instance-role-policy.json) or rotate IAM keys."
  fi
fi

echo "==> ECR login (${ECR_REGISTRY})"
ECR_PASSWORD="$(aws ecr get-login-password --region "${REGION}")" \
  || die "ecr:GetAuthorizationToken failed — attach deploy/ec2-instance-role-policy.json to this EC2 role"
printf '%s' "${ECR_PASSWORD}" | docker login --username AWS --password-stdin "${ECR_REGISTRY%%/*}"

if command -v sudo >/dev/null 2>&1; then
  sudo tee /etc/sysctl.d/99-ai-mesh.conf >/dev/null <<'EOF' || true
net.core.somaxconn = 65535
net.ipv4.ip_local_port_range = 1024 65535
fs.file-max = 1048576
EOF
  sudo sysctl -p /etc/sysctl.d/99-ai-mesh.conf 2>/dev/null || true
fi

echo "==> Pull application images from ECR"
"${COMPOSE[@]}" pull gateway control workers workers-beat nginx
# Demo is OPTIONAL and isolated: a missing/failed demo image must never abort the
# platform deploy. Pull tolerantly (it 502s behind nginx if absent — never crashes it).
"${COMPOSE[@]}" pull demo 2>/dev/null \
  || echo "    (ai-mesh-demo image absent for this tag — /demo/ will be unavailable; platform unaffected)"

echo "==> Start infrastructure"
"${COMPOSE[@]}" up -d --no-build postgres redis rabbitmq

for _ in $(seq 1 30); do
  "${COMPOSE[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-ai_mesh_firewall}" >/dev/null 2>&1 && break
  sleep 2
done

echo "==> Control + migrations"
"${COMPOSE[@]}" up -d --no-build control
for _ in $(seq 1 45); do
  curl -sf "http://127.0.0.1:8100/api/health/" >/dev/null 2>&1 && break
  sleep 2
done
"${COMPOSE[@]}" exec -T control python manage.py migrate --noinput

if [[ "${SKIP_ADMIN:-}" != "1" ]]; then
  "${COMPOSE[@]}" exec -T control python manage.py ensure_zeroshield_admin \
    ${ZEROSHIELD_ADMIN_PASSWORD:+--password "$ZEROSHIELD_ADMIN_PASSWORD"} || true
fi

if [[ "${SKIP_PII_SEED:-}" != "1" ]] && [[ -n "${SEED_PII_POLICY_ORG_SLUG:-}" ]]; then
  echo "==> PII policy package (org slug=${SEED_PII_POLICY_ORG_SLUG})"
  "${COMPOSE[@]}" exec -T control python manage.py seed_pii_policy_package \
    --org-slug "${SEED_PII_POLICY_ORG_SLUG}" \
    ${RESET_PII_SEED:+--reset} || true
fi

echo "==> Gateway, workers, nginx (restart: unless-stopped)"
"${COMPOSE[@]}" --profile workers up -d --no-build gateway workers workers-beat nginx

# Optional demo app (before nginx reload so the /demo/ upstream is resolvable).
# Tolerant: failure here never blocks the platform deploy.
"${COMPOSE[@]}" up -d --no-build demo 2>/dev/null \
  || echo "    (demo service not started — /demo/ unavailable; platform unaffected)"

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:8300/health" >/dev/null 2>&1 && break
  sleep 2
done

echo "==> Reload nginx (static + config baked into ai-mesh-nginx image)"
"${COMPOSE[@]}" up -d --force-recreate nginx
"${COMPOSE[@]}" exec -T nginx nginx -t
sleep 2

FH="${FRONTEND_HOST:-aimeshfirewall.zeroshield.ai}"
BH="${BACKEND_HOST:-aimeshbackend.zeroshield.ai}"
GH="${GATEWAY_HOST:-aimeshgateway.zeroshield.ai}"

_check_v1_proxy() {
  local scheme="$1"
  local port="$2"
  shift 2
  local curl_args=("$@")
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "${curl_args[@]}" \
    -X POST "${scheme}://127.0.0.1:${port}/v1/chat/completions" \
    -H "Host: ${FH}" \
    -H "Content-Type: application/json" \
    -d '{"model":"auto","messages":[{"role":"user","content":"ping"}]}' || echo "000")"
  [[ "${code}" != "405" && "${code}" != "000" ]] || die "nginx ${scheme} /v1/ still serves SPA (POST returned ${code})"
}

curl -sf -H "Host: ${FH}" "http://127.0.0.1/" -o /dev/null || die "nginx UI vhost failed (is port 80 published?)"
curl -sf -H "Host: ${FH}" "http://127.0.0.1/gw-health" || die "nginx UI → gateway health proxy failed"
_check_v1_proxy http 80
curl -sf -H "Host: ${BH}" "http://127.0.0.1/api/health/" || die "nginx backend vhost failed"
curl -sf -H "Host: ${GH}" "http://127.0.0.1/health" || die "nginx gateway vhost failed"

if curl -sfk -o /dev/null "https://127.0.0.1/gw-health" 2>/dev/null; then
  curl -sfk -H "Host: ${FH}" "https://127.0.0.1/gw-health" || die "nginx HTTPS UI → gateway health proxy failed"
  _check_v1_proxy https 443 -k
fi

cat <<EOF

Stack is up (ECR ${IMAGE_TAG}).

  UI       : https://${FH}  (nginx :443)
  Control  : https://${BH}/api/
  Gateway  : https://${GH}/v1/

Point Route53 A records for all three hosts to this EC2.
Open SG inbound :80 (and :443 when TLS is added).

Redeploy after new images:
  docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

EOF

bash scripts/publish-stack-ready-metric.sh 1 || true
