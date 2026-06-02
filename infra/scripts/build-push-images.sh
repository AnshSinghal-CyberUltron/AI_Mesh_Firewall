#!/usr/bin/env bash
# Build (linux/arm64 for c8g by default) and push gateway, control, workers to ECR.
# Usage: ./infra/scripts/build-push-images.sh [tag]
#   DOCKER_PLATFORM=linux/amd64 ./infra/scripts/build-push-images.sh v1.0.1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-v1.0.0}"
REGION="${AWS_REGION:-ap-south-1}"
PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

for repo in ai-mesh-gateway ai-mesh-control ai-mesh-workers; do
  aws ecr describe-repositories --repository-names "${repo}" --region "${REGION}" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "${repo}" --region "${REGION}" >/dev/null
done

aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR}"

cd "${ROOT}"

docker build --platform "${PLATFORM}" -f gateway/Dockerfile \
  -t "${ECR}/ai-mesh-gateway:${TAG}" .
docker push "${ECR}/ai-mesh-gateway:${TAG}"

docker build --platform "${PLATFORM}" -f control/Dockerfile \
  -t "${ECR}/ai-mesh-control:${TAG}" .
docker push "${ECR}/ai-mesh-control:${TAG}"

docker build --platform "${PLATFORM}" -f workers/Dockerfile \
  -t "${ECR}/ai-mesh-workers:${TAG}" .
docker push "${ECR}/ai-mesh-workers:${TAG}"

echo "Pushed (${PLATFORM}):"
echo "  ${ECR}/ai-mesh-gateway:${TAG}"
echo "  ${ECR}/ai-mesh-control:${TAG}"
echo "  ${ECR}/ai-mesh-workers:${TAG}"
echo ""
echo "On EC2 .env set:"
echo "  ECR_REGISTRY=${ECR}"
echo "  IMAGE_TAG=${TAG}"
