#!/usr/bin/env bash
# Local build → ECR push → sync deploy config → EC2 pull + compose up.
# No application source is copied to EC2 — only compose, .env, and deploy scripts.
#
# Usage:
#   ./scripts/deploy-full-ec2.sh
#   ./scripts/deploy-full-ec2.sh --tag v1.0.2
#   ./scripts/deploy-full-ec2.sh --skip-build
#   AWS_PROFILE=deploy ./scripts/deploy-full-ec2.sh --tag v1.0.2
#
# Prerequisites (laptop/CI):
#   - Docker, aws CLI, rsync, ssh to EC2
#   - IAM that can push to ECR (deploy/ecr-push-policy.json)
#
# Prerequisites (EC2):
#   - Instance role with ECR pull (deploy/ec2-instance-role-policy.json)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_HOST="${SSH_HOST:-AIMeshFirewall}"
REMOTE_DIR="${REMOTE_DIR:-/home/ec2-user/AI_Mesh_Firewall}"
REGION="${AWS_REGION:-ap-south-1}"
PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"

SKIP_BUILD=false
SYNC_ONLY=false
EXPLICIT_TAG=""

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: deploy-full-ec2.sh [options]

  --tag TAG       ECR image tag (default: IMAGE_TAG from .env, else vYYYYMMDD-<git-sha>)
  --skip-build    Skip build/push; deploy existing IMAGE_TAG from .env
  --sync-only     Build/push/sync config but skip remote compose up
  -h, --help      Show this help

Workflow:
  1. Build gateway, control, workers, nginx images locally
  2. Push to ECR (ECR_REGISTRY in .env)
  3. Build frontend dist locally (VITE_* from .env) — baked into ai-mesh-nginx image
  4. Sync compose + .env + deploy scripts to EC2 (no source code, no bind mounts)
  5. SSH: ECR login, pull images, docker compose up

Environment:
  SSH_HOST, REMOTE_DIR, DOCKER_PLATFORM, AWS_REGION, ECR_REGISTRY, IMAGE_TAG

Examples:
  make ecr-push TAG=v1.0.2 && make sync-ec2-deploy
  ./scripts/deploy-full-ec2.sh --tag v1.0.2
  AWS_PROFILE=deploy ./scripts/deploy-full-ec2.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=true; shift ;;
    --sync-only) SYNC_ONLY=true; shift ;;
    --build-on-ec2) die "--build-on-ec2 removed: build locally and push to ECR instead" ;;
    --build-local) shift ;;
    --tag)
      [[ $# -ge 2 ]] || die "--tag requires a value"
      EXPLICIT_TAG="$2"
      shift 2
      ;;
    --tag=*) EXPLICIT_TAG="${1#--tag=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1 (try --help)" ;;
  esac
done

cd "${ROOT}"

[[ -f .env ]] || die "Missing .env — copy .env.ec2.sample and fill in values."

# shellcheck disable=SC1091
set -a && source .env && set +a

command -v docker >/dev/null 2>&1 || die "docker not installed"
command -v aws >/dev/null 2>&1 || die "aws CLI not installed"
command -v scp >/dev/null 2>&1 || die "scp not installed (install OpenSSH or Git for Windows)"
command -v ssh >/dev/null 2>&1 || die "ssh not installed"

_resolve_tag() {
  if [[ -n "${EXPLICIT_TAG}" ]]; then
    echo "${EXPLICIT_TAG}"
    return
  fi
  if [[ -n "${IMAGE_TAG:-}" ]]; then
    echo "${IMAGE_TAG}"
    return
  fi
  local sha
  sha="$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo "local")"
  echo "v$(date +%Y%m%d)-${sha}"
}

_resolve_ecr_registry() {
  if [[ -n "${ECR_REGISTRY:-}" ]]; then
    echo "${ECR_REGISTRY}"
    return
  fi
  local account
  account="$(aws sts get-caller-identity --query Account --output text --region "${REGION}" 2>/dev/null)" \
    || die "Set ECR_REGISTRY in .env (cannot derive — AWS credentials unusable)"
  echo "${account}.dkr.ecr.${REGION}.amazonaws.com"
}

_update_env_kv() {
  local key="$1" value="$2" file="$3"
  local tmp
  tmp="$(mktemp)"
  if grep -q "^${key}=" "${file}"; then
    sed "s|^${key}=.*|${key}=${value}|" "${file}" > "${tmp}"
    mv "${tmp}" "${file}"
  else
    rm -f "${tmp}"
    echo "${key}=${value}" >> "${file}"
  fi
}

TAG="$(_resolve_tag)"
ECR_REGISTRY="$(_resolve_ecr_registry)"

echo "============================================================"
echo " AI Mesh Firewall — ECR deploy"
echo " Tag:      ${TAG}"
echo " ECR:      ${ECR_REGISTRY}"
echo " EC2:      ${SSH_HOST}:${REMOTE_DIR}"
echo " Platform: ${PLATFORM}"
echo " Build:    $( [[ "${SKIP_BUILD}" == true ]] && echo skip || echo local )"
echo "============================================================"

echo "==> [1/4] Build frontend for production (VITE_* from .env; baked into nginx image)"
bash "${ROOT}/infra/scripts/build-frontend-prod.sh"

if [[ "${SKIP_BUILD}" != true ]]; then
  echo "==> [2/4] Build and push images locally → ECR"
  DOCKER_PLATFORM="${PLATFORM}" AWS_REGION="${REGION}" ECR_REGISTRY="${ECR_REGISTRY}" \
    bash "${ROOT}/infra/scripts/build-push-images.sh" "${TAG}"
else
  echo "==> [2/4] Skipping ECR build/push (--skip-build)"
fi

echo "==> [3/4] Update .env IMAGE_TAG and ECR_REGISTRY"
_update_env_kv "IMAGE_TAG" "${TAG}" "${ROOT}/.env"
_update_env_kv "ECR_REGISTRY" "${ECR_REGISTRY}" "${ROOT}/.env"
set -a && source .env && set +a
echo "    IMAGE_TAG=${IMAGE_TAG}"
echo "    ECR_REGISTRY=${ECR_REGISTRY}"

echo "==> [4/4] Sync deploy config to EC2 (no app source)"
bash "${ROOT}/scripts/sync-to-ec2.sh"

if [[ "${SYNC_ONLY}" == true ]]; then
  echo "==> Skipping remote deploy (--sync-only)"
  exit 0
fi

echo "==> Remote deploy on EC2 (ECR pull + compose up)"
ssh -t "${SSH_HOST}" "cd '${REMOTE_DIR}' && bash scripts/deploy-ec2.sh"

cat <<EOF

============================================================
 Deploy finished.

  Images : ${ECR_REGISTRY}/ai-mesh-{gateway,control,workers,nginx}:${TAG}
  UI     : https://${FRONTEND_HOST:-aimeshfirewall.zeroshield.ai}
  API    : https://${BACKEND_HOST:-aimeshbackend.zeroshield.ai}/api/
  Gateway: https://${GATEWAY_HOST:-aimeshgateway.zeroshield.ai}/v1/

Redeploy after a new push:
  make ecr-push TAG=<tag> && make sync-ec2-deploy

EOF
