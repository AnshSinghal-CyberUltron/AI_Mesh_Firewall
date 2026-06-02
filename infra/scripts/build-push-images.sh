#!/usr/bin/env bash
# Build and push gateway + control images to ECR (workers reuse control image).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-v1.0.0}"
REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR}"

cd "${ROOT}"
docker build -f gateway/Dockerfile -t "${ECR}/ai-mesh-gateway:${TAG}" .
docker push "${ECR}/ai-mesh-gateway:${TAG}"

docker build -f control/Dockerfile -t "${ECR}/ai-mesh-control:${TAG}" .
docker push "${ECR}/ai-mesh-control:${TAG}"

echo "Pushed:"
echo "  ${ECR}/ai-mesh-gateway:${TAG}"
echo "  ${ECR}/ai-mesh-control:${TAG}"
