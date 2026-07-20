#!/usr/bin/env bash
# Build all production images locally and tag them for compose.prod(.local).
# Does NOT push to ECR (use infra/scripts/build-push-images.sh / make ecr-push for that).
#
# Usage:
#   bash scripts/build-prod-images-local.sh
#   bash scripts/build-prod-images-local.sh --mcp-only
#   ECR_REGISTRY=local.ecr.test IMAGE_TAG=local bash scripts/build-prod-images-local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

die() { echo "ERROR: $*" >&2; exit 1; }

MCP_ONLY=false
PLATFORM="${DOCKER_PLATFORM:-}"
ECR_REGISTRY="${ECR_REGISTRY:-local.ecr.test}"
IMAGE_TAG="${IMAGE_TAG:-local}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mcp-only) MCP_ONLY=true; shift ;;
    --tag)
      [[ $# -ge 2 ]] || die "--tag requires a value"
      IMAGE_TAG="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--mcp-only] [--tag TAG]"
      exit 0
      ;;
    *) die "Unknown arg: $1" ;;
  esac
done

export ECR_REGISTRY IMAGE_TAG

PLATFORM_ARGS=()
if [[ -n "${PLATFORM}" ]]; then
  PLATFORM_ARGS=(--platform "${PLATFORM}")
fi

_build() {
  local dockerfile="$1" tag="$2"
  shift 2
  local context="${ROOT}"
  # Optional 3rd positional: alternate build context (demo).
  if [[ $# -gt 0 && -d "$1" ]]; then
    context="$1"
    shift
  fi
  echo "==> docker build -f ${dockerfile} -t ${tag} (context=${context})"
  docker build ${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"} \
    -f "${dockerfile}" -t "${tag}" "$@" "${context}"
}

echo "ECR_REGISTRY=${ECR_REGISTRY}  IMAGE_TAG=${IMAGE_TAG}"

if [[ "${MCP_ONLY}" != true ]]; then
  [[ -f frontend/dist/index.html ]] \
    || bash "${ROOT}/infra/scripts/build-frontend-prod.sh"

  _build gateway/Dockerfile "${ECR_REGISTRY}/ai-mesh-gateway:${IMAGE_TAG}"
  _build control/Dockerfile "${ECR_REGISTRY}/ai-mesh-control:${IMAGE_TAG}"
  _build workers/Dockerfile "${ECR_REGISTRY}/ai-mesh-workers:${IMAGE_TAG}"
  _build deploy/Dockerfile.nginx "${ECR_REGISTRY}/ai-mesh-nginx:${IMAGE_TAG}" \
    --build-arg "DEMO_AUTH_USER=${DEMO_AUTH_USER:-superuser}" \
    --build-arg "DEMO_AUTH_PASSWORD=${DEMO_AUTH_PASSWORD:-change-me}"
  _build examples/zeroshield-openai-demo/Dockerfile \
    "${ECR_REGISTRY}/ai-mesh-demo:${IMAGE_TAG}" \
    "${ROOT}/examples/zeroshield-openai-demo"
fi

_build services/mcp-broker/Dockerfile "${ECR_REGISTRY}/ai-mesh-mcp-broker:${IMAGE_TAG}"
_build services/mcp-broker/sandbox-image/Dockerfile "${ECR_REGISTRY}/ai-mesh-mcp-sandbox:${IMAGE_TAG}"
# Broker default MCP_SANDBOX_IMAGE=ai-mesh/mcp-sandbox:latest — also tag locally.
docker tag "${ECR_REGISTRY}/ai-mesh-mcp-sandbox:${IMAGE_TAG}" "ai-mesh/mcp-sandbox:${IMAGE_TAG}"
docker tag "${ECR_REGISTRY}/ai-mesh-mcp-sandbox:${IMAGE_TAG}" "ai-mesh/mcp-sandbox:latest"

echo
echo "Local images ready:"
docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E "ai-mesh-(gateway|control|workers|nginx|demo|mcp-broker|mcp-sandbox):${IMAGE_TAG}\$" \
  || true
echo
echo "Start prod-like stack (non-conflicting ports):"
echo "  make up-prod-local ECR_REGISTRY=${ECR_REGISTRY} IMAGE_TAG=${IMAGE_TAG}"
