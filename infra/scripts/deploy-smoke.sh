#!/usr/bin/env bash
# Post-deploy smoke checks (requires terraform outputs + DNS or direct LB hostnames).
set -euo pipefail

TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../terraform/envs/prod" && pwd)"
REGION="${AWS_REGION:-ap-south-1}"

cd "${TF_DIR}"
NLB="$(terraform output -raw nlb_dns_name 2>/dev/null || true)"
ALB="$(terraform output -raw alb_dns_name 2>/dev/null || true)"
CF="$(terraform output -raw cloudfront_domain_name 2>/dev/null || true)"

if [[ -z "${NLB}" || -z "${ALB}" ]]; then
  echo "Run from initialized terraform env with applied state (nlb_dns_name, alb_dns_name outputs)."
  exit 1
fi

echo "==> Gateway NLB health (TLS)"
curl -sfk --max-time 15 "https://${NLB}/health" | head -c 200
echo ""

echo "==> Control ALB health"
curl -sfk --max-time 15 "https://${ALB}/health/" | head -c 200
echo ""

if [[ -n "${CF}" ]]; then
  echo "==> CloudFront UI root"
  curl -sfI --max-time 15 "https://${CF}/" | head -5
fi

echo "==> ECS services (cluster $(terraform output -raw ecs_cluster_name))"
CLUSTER="$(terraform output -raw ecs_cluster_name)"
aws ecs list-services --cluster "${CLUSTER}" --region "${REGION}" \
  --query 'serviceArns' --output table

echo "Smoke checks completed."
