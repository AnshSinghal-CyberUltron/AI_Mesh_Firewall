#!/usr/bin/env bash
# Build frontend and sync to the Terraform-managed UI S3 bucket; invalidate CloudFront.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="${ROOT}/infra/terraform/envs/prod"
REGION="${AWS_REGION:-ap-south-1}"

cd "${TF_DIR}"
BUCKET="$(terraform output -raw ui_s3_bucket)"
DIST_ID="$(terraform output -raw cloudfront_distribution_id)"
ALB="$(terraform output -raw alb_dns_name)"
NLB="$(terraform output -raw nlb_dns_name)"

export VITE_BACKEND_BASE_URL="${VITE_BACKEND_BASE_URL:-https://${ALB}}"
export VITE_GATEWAY_BASE_URL="${VITE_GATEWAY_BASE_URL:-https://${NLB}}"

echo "==> Building frontend (VITE_BACKEND_BASE_URL=${VITE_BACKEND_BASE_URL})"
cd "${ROOT}/frontend"
npm ci
npm run build

echo "==> Syncing to s3://${BUCKET}"
aws s3 sync dist/ "s3://${BUCKET}/" --delete --region "${REGION}"

echo "==> Invalidating CloudFront ${DIST_ID}"
aws cloudfront create-invalidation \
  --distribution-id "${DIST_ID}" \
  --paths "/*"

echo "Frontend published."
