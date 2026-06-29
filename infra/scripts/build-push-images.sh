#!/usr/bin/env bash
# Build (linux/arm64 for c8g by default) and push gateway, control, workers, nginx to ECR.
# Run on your laptop/CI with ECR-capable AWS credentials — EC2 only pulls.
# Usage: ./infra/scripts/build-push-images.sh [tag]
#   DOCKER_PLATFORM=linux/amd64 ./infra/scripts/build-push-images.sh v1.0.1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-v1.0.0}"
REGION="${AWS_REGION:-ap-south-1}"
PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"

die() { echo "ERROR: $*" >&2; exit 1; }

_update_env_kv() {
  local key="$1" value="$2" file="$3"
  [[ -f "${file}" ]] || return 0
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

if [[ -f "${ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  set -a && source "${ROOT}/.env" && set +a
fi

# .env often holds Bedrock/service IAM keys for containers — not for laptop ECR push.
# Default AWS CLI chain (profile, SSO, instance role) is used unless USE_ENV_AWS_KEYS=true.
if [[ "${USE_ENV_AWS_KEYS:-}" != "true" ]]; then
  if [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]; then
    echo "==> Using default AWS CLI credentials for ECR (ignoring AWS_* keys from .env)"
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  fi
fi

if [[ -n "${ECR_REGISTRY:-}" ]]; then
  ECR="${ECR_REGISTRY%%/*}"
else
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
    || die "aws sts failed — use ECR_REGISTRY in .env or fix AWS credentials"
  ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
fi

_ensure_ecr_repos() {
  local repo
  for repo in ai-mesh-gateway ai-mesh-control ai-mesh-workers ai-mesh-nginx ai-mesh-demo; do
    if aws ecr describe-repositories --repository-names "${repo}" --region "${REGION}" >/dev/null 2>&1; then
      continue
    fi
    aws ecr create-repository --repository-name "${repo}" --region "${REGION}" >/dev/null \
      || die "create ECR repo ${repo} failed — create manually or use admin/deploy IAM credentials"
  done
}
_ensure_ecr_repos

ECR_PASSWORD="$(aws ecr get-login-password --region "${REGION}" 2>/dev/null)" \
  || die "ecr:GetAuthorizationToken failed (region=${REGION}). Run: aws sts get-caller-identity — use an IAM user/role with ECR push, or set USE_ENV_AWS_KEYS=true if .env keys should be used."
[[ -n "${ECR_PASSWORD}" ]] || die "ECR login password empty"
printf '%s' "${ECR_PASSWORD}" | docker login --username AWS --password-stdin "${ECR}"

cd "${ROOT}"

if [[ ! -f "${ROOT}/frontend/dist/index.html" ]]; then
  echo "==> frontend/dist missing — building production UI (VITE_* from .env)"
  bash "${ROOT}/infra/scripts/build-frontend-prod.sh"
fi

docker build --platform "${PLATFORM}" -f gateway/Dockerfile \
  -t "${ECR}/ai-mesh-gateway:${TAG}" .
docker push "${ECR}/ai-mesh-gateway:${TAG}"

docker build --platform "${PLATFORM}" -f control/Dockerfile \
  -t "${ECR}/ai-mesh-control:${TAG}" .
docker push "${ECR}/ai-mesh-control:${TAG}"

docker build --platform "${PLATFORM}" -f workers/Dockerfile \
  -t "${ECR}/ai-mesh-workers:${TAG}" .
docker push "${ECR}/ai-mesh-workers:${TAG}"

docker build --platform "${PLATFORM}" -f examples/zeroshield-openai-demo/Dockerfile \
  -t "${ECR}/ai-mesh-demo:${TAG}" examples/zeroshield-openai-demo
docker push "${ECR}/ai-mesh-demo:${TAG}"

# nginx bakes the /demo/ Basic Auth credential (apr1 hash) from these build args.
docker build --platform "${PLATFORM}" -f deploy/Dockerfile.nginx \
  --build-arg "DEMO_AUTH_USER=${DEMO_AUTH_USER:-superuser}" \
  --build-arg "DEMO_AUTH_PASSWORD=${DEMO_AUTH_PASSWORD:-change-me}" \
  -t "${ECR}/ai-mesh-nginx:${TAG}" .
docker push "${ECR}/ai-mesh-nginx:${TAG}"

_update_env_kv "IMAGE_TAG" "${TAG}" "${ROOT}/.env"
_update_env_kv "ECR_REGISTRY" "${ECR}" "${ROOT}/.env"

echo "Pushed (${PLATFORM}):"
echo "  ${ECR}/ai-mesh-gateway:${TAG}"
echo "  ${ECR}/ai-mesh-control:${TAG}"
echo "  ${ECR}/ai-mesh-workers:${TAG}"
echo "  ${ECR}/ai-mesh-nginx:${TAG}"
echo "  ${ECR}/ai-mesh-demo:${TAG}"
echo ""
echo "Updated .env (sync-to-ec2 copies this to EC2):"
echo "  ECR_REGISTRY=${ECR}"
echo "  IMAGE_TAG=${TAG}"
