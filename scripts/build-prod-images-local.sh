#!/usr/bin/env bash
# Build all production images locally in PARALLEL (no ECR push).
# Saturates VM cores via background docker builds + BuildKit.
#
# Usage:
#   bash scripts/build-prod-images-local.sh
#   bash scripts/build-prod-images-local.sh --mcp-only
#   bash scripts/build-prod-images-local.sh --tag local
#   ECR_REGISTRY=local.ecr.test IMAGE_TAG=local make build-prod-images-local
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=../infra/scripts/vm-capacity.sh
source "${ROOT}/infra/scripts/vm-capacity.sh"
aim_capacity_export

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

aim_capacity_print
echo "ECR_REGISTRY=${ECR_REGISTRY}  IMAGE_TAG=${IMAGE_TAG}  PLATFORM=${PLATFORM:-host}"

PLATFORM_ARGS=()
if [[ -n "${PLATFORM}" ]]; then
  PLATFORM_ARGS=(--platform "${PLATFORM}")
fi
NET_ARGS=(--network "${AIM_DOCKER_NETWORK:-host}")

pids=()
names=()

_run_build() {
  local name="$1" dockerfile="$2" tag="$3"
  shift 3
  local context="${ROOT}"
  if [[ $# -gt 0 && -d "$1" ]]; then
    context="$1"
    shift
  fi
  (
    echo "[${name}] START $(date -u +%Y-%m-%dT%H:%M:%SZ) -f ${dockerfile} -t ${tag}"
    docker build "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" "${NET_ARGS[@]}" \
      -f "${dockerfile}" -t "${tag}" "$@" "${context}"
    echo "[${name}] DONE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ) >"/tmp/aim-build-local-${name}.log" 2>&1 &
  pids+=($!)
  names+=("${name}")
}

if [[ "${MCP_ONLY}" != true ]]; then
  [[ -f frontend/dist/index.html ]] \
    || bash "${ROOT}/infra/scripts/build-frontend-prod.sh"

  _run_build gateway gateway/Dockerfile "${ECR_REGISTRY}/ai-mesh-gateway:${IMAGE_TAG}"
  _run_build control control/Dockerfile "${ECR_REGISTRY}/ai-mesh-control:${IMAGE_TAG}"
  _run_build workers workers/Dockerfile "${ECR_REGISTRY}/ai-mesh-workers:${IMAGE_TAG}"
  _run_build nginx deploy/Dockerfile.nginx "${ECR_REGISTRY}/ai-mesh-nginx:${IMAGE_TAG}" \
    --build-arg "DEMO_AUTH_USER=${DEMO_AUTH_USER:-superuser}" \
    --build-arg "DEMO_AUTH_PASSWORD=${DEMO_AUTH_PASSWORD:-change-me}"
  _run_build demo examples/zeroshield-openai-demo/Dockerfile \
    "${ECR_REGISTRY}/ai-mesh-demo:${IMAGE_TAG}" \
    "${ROOT}/examples/zeroshield-openai-demo"
fi

_run_build mcp-broker services/mcp-broker/Dockerfile "${ECR_REGISTRY}/ai-mesh-mcp-broker:${IMAGE_TAG}"
_run_build mcp-sandbox services/mcp-broker/sandbox-image/Dockerfile "${ECR_REGISTRY}/ai-mesh-mcp-sandbox:${IMAGE_TAG}"

echo "PIDs: ${pids[*]}  names: ${names[*]}"
echo "Logs: /tmp/aim-build-local-*.log"

if ! aim_wait_pids pids names; then
  echo "ONE OR MORE LOCAL BUILDS FAILED"
  for n in "${names[@]}"; do
    echo "---- tail /tmp/aim-build-local-${n}.log ----"
    tail -n 40 "/tmp/aim-build-local-${n}.log" 2>/dev/null || true
  done
  exit 1
fi

# Broker default MCP_SANDBOX_IMAGE=ai-mesh/mcp-sandbox:latest
docker tag "${ECR_REGISTRY}/ai-mesh-mcp-sandbox:${IMAGE_TAG}" "ai-mesh/mcp-sandbox:${IMAGE_TAG}"
docker tag "${ECR_REGISTRY}/ai-mesh-mcp-sandbox:${IMAGE_TAG}" "ai-mesh/mcp-sandbox:latest"

echo
echo "Local images ready (parallel build OK):"
docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E "ai-mesh-(gateway|control|workers|nginx|demo|mcp-broker|mcp-sandbox):${IMAGE_TAG}\$" \
  || true
echo
echo "Start prod-like stack (non-conflicting ports):"
echo "  make up-prod-local ECR_REGISTRY=${ECR_REGISTRY} IMAGE_TAG=${IMAGE_TAG}"
