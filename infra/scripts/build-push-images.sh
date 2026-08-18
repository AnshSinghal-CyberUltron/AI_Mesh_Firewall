#!/usr/bin/env bash
# Parallel ECR build+push — saturate build VM (16c / ~60GiB / high-IOPS).
# Prod EC2 is c8g (aarch64) → default platform linux/arm64.
#
# Usage:
#   ./infra/scripts/build-push-images.sh [tag]
#   DOCKER_PLATFORM=linux/arm64 FORCE_REBUILD=1 ./infra/scripts/build-push-images.sh latest
#
# Env:
#   FORCE_REBUILD=1     → --no-cache on every image
#   AIM_BUILDX_BUILDER  → buildx builder name (default aim-fast)
#   USE_ENV_AWS_KEYS=true → honor AWS_* from .env (default: ignore .env keys)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=vm-capacity.sh
source "${ROOT}/infra/scripts/vm-capacity.sh"
aim_capacity_export

TAG="${1:-v1.0.0}"
REGION="${AWS_REGION:-ap-south-1}"
PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"
LOG="/tmp/aim-ecr-parallel-build-${TAG}.log"
exec > >(tee -a "$LOG") 2>&1

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

aim_capacity_export

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

echo "=== AI Mesh parallel ECR build start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
aim_capacity_print
echo "TAG=$TAG  PLATFORM=$PLATFORM  ECR=$ECR"

_ensure_ecr_repos() {
  local repo
  for repo in ai-mesh-gateway ai-mesh-control ai-mesh-workers ai-mesh-nginx ai-mesh-demo ai-mesh-mcp-broker ai-mesh-mcp-sandbox; do
    if aws ecr describe-repositories --repository-names "${repo}" --region "${REGION}" >/dev/null 2>&1; then
      continue
    fi
    aws ecr create-repository --repository-name "${repo}" --region "${REGION}" >/dev/null \
      || die "create ECR repo ${repo} failed"
  done
}
_ensure_ecr_repos

echo "=== ECR login ==="
ECR_PASSWORD="$(aws ecr get-login-password --region "${REGION}" 2>/dev/null)" \
  || die "ecr:GetAuthorizationToken failed (region=${REGION})"
[[ -n "${ECR_PASSWORD}" ]] || die "ECR login password empty"
printf '%s' "${ECR_PASSWORD}" | docker login --username AWS --password-stdin "${ECR}"

cd "${ROOT}"

if [[ ! -f "${ROOT}/frontend/dist/index.html" ]]; then
  echo "==> frontend/dist missing — building production UI (VITE_* from .env)"
  bash "${ROOT}/infra/scripts/build-frontend-prod.sh"
fi

aim_ensure_buildx_builder "$AIM_BUILDX_BUILDER"

COMMON=(
  docker buildx build
  --builder "$AIM_BUILDX_BUILDER"
  --platform "$PLATFORM"
  --push
  --provenance=false
  --sbom=false
  --network=host
)
if [[ "${FORCE_REBUILD:-0}" == "1" || "${FORCE_REBUILD:-}" == "true" ]]; then
  COMMON+=(--no-cache)
  echo "==> FORCE_REBUILD: --no-cache"
fi

# Append -t repo:TAG and -t repo:latest (unless TAG is already latest).
append_ecr_tags() {
  local -n _arr=$1
  local repo="$2"
  _arr+=(-t "${ECR}/${repo}:${TAG}")
  if [[ "${TAG}" != "latest" ]]; then
    _arr+=(-t "${ECR}/${repo}:latest")
  fi
}

echo "=== launching 7 image builds in PARALLEL ==="
pids=()
names=()

_run() {
  local name="$1"
  shift
  (
    echo "[${name}] START $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$@"
    echo "[${name}] DONE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ) >"/tmp/aim-build-${name}.log" 2>&1 &
  pids+=($!)
  names+=("${name}")
}

gw_args=("${COMMON[@]}"); append_ecr_tags gw_args ai-mesh-gateway
gw_args+=(-f gateway/Dockerfile .)
_run gateway "${gw_args[@]}"

ctl_args=("${COMMON[@]}"); append_ecr_tags ctl_args ai-mesh-control
ctl_args+=(-f control/Dockerfile .)
_run control "${ctl_args[@]}"

wrk_args=("${COMMON[@]}"); append_ecr_tags wrk_args ai-mesh-workers
wrk_args+=(-f workers/Dockerfile .)
_run workers "${wrk_args[@]}"

demo_args=("${COMMON[@]}"); append_ecr_tags demo_args ai-mesh-demo
demo_args+=(-f examples/zeroshield-openai-demo/Dockerfile examples/zeroshield-openai-demo)
_run demo "${demo_args[@]}"

ngx_args=("${COMMON[@]}"); append_ecr_tags ngx_args ai-mesh-nginx
ngx_args+=(
  --build-arg "DEMO_AUTH_USER=${DEMO_AUTH_USER:-superuser}"
  --build-arg "DEMO_AUTH_PASSWORD=${DEMO_AUTH_PASSWORD:-change-me}"
  -f deploy/Dockerfile.nginx .
)
_run nginx "${ngx_args[@]}"

brk_args=("${COMMON[@]}"); append_ecr_tags brk_args ai-mesh-mcp-broker
brk_args+=(-f services/mcp-broker/Dockerfile .)
_run mcp-broker "${brk_args[@]}"

# Note: do NOT -t ai-mesh/mcp-sandbox here — buildx --push would try Docker Hub.
# Broker on EC2 pulls ${ECR}/ai-mesh-mcp-sandbox:${TAG} (compose sets MCP_SANDBOX_IMAGE).
sbx_args=("${COMMON[@]}"); append_ecr_tags sbx_args ai-mesh-mcp-sandbox
sbx_args+=(-f services/mcp-broker/sandbox-image/Dockerfile .)
_run mcp-sandbox "${sbx_args[@]}"

echo "PIDs: ${pids[*]}  names: ${names[*]}"
echo "Per-image logs: /tmp/aim-build-*.log  aggregate: $LOG"

if ! aim_wait_pids pids names; then
  echo "ONE OR MORE BUILDS FAILED — see $LOG and /tmp/aim-build-*.log"
  for n in "${names[@]}"; do
    echo "---- tail /tmp/aim-build-${n}.log ----"
    tail -n 40 "/tmp/aim-build-${n}.log" 2>/dev/null || true
  done
  exit 1
fi

echo "=== ECR image verify ==="
for repo in ai-mesh-gateway ai-mesh-control ai-mesh-workers ai-mesh-nginx ai-mesh-demo ai-mesh-mcp-broker ai-mesh-mcp-sandbox; do
  aws ecr describe-images --repository-name "$repo" --region "$REGION" \
    --image-ids "imageTag=$TAG" \
    --query 'imageDetails[0].{tag:imageTags[0],pushed:imagePushedAt,bytes:imageSizeInBytes}' \
    --output table 2>/dev/null || echo "MISSING ${repo}:${TAG}"
done

_update_env_kv "IMAGE_TAG" "${TAG}" "${ROOT}/.env"
_update_env_kv "ECR_REGISTRY" "${ECR}" "${ROOT}/.env"

echo "=== ALL PARALLEL BUILDS PUSHED $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Platform: ${PLATFORM}"
echo "  ${ECR}/ai-mesh-gateway:${TAG}"
echo "  ${ECR}/ai-mesh-control:${TAG}"
echo "  ${ECR}/ai-mesh-workers:${TAG}"
echo "  ${ECR}/ai-mesh-nginx:${TAG}"
echo "  ${ECR}/ai-mesh-demo:${TAG}"
echo "  ${ECR}/ai-mesh-mcp-broker:${TAG}"
echo "  ${ECR}/ai-mesh-mcp-sandbox:${TAG}"
if [[ "${TAG}" != "latest" ]]; then
  echo "  (+ :latest aliases for the same digests)"
fi
echo "Updated .env: ECR_REGISTRY=${ECR} IMAGE_TAG=${TAG}"
echo "Log: $LOG"
