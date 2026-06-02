#!/usr/bin/env bash
# Run terraform plan for prod (uses Docker if local terraform is missing).
set -euo pipefail

TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../terraform/envs/prod" && pwd)"
VAR_FILE="${TF_DIR}/prod.tfvars"
PLAN_OUT="${TF_DIR}/prod.plan"

run_tf() {
  if command -v terraform >/dev/null 2>&1; then
    terraform "$@"
  else
    docker run --rm \
      -v "${TF_DIR}/../..:/tf" \
      -w /tf/envs/prod \
      -e AWS_REGION="${AWS_REGION:-ap-south-1}" \
      -e AWS_ACCESS_KEY_ID \
      -e AWS_SECRET_ACCESS_KEY \
      -e AWS_SESSION_TOKEN \
      hashicorp/terraform:1.9.8 "$@"
  fi
}

cd "${TF_DIR}"
run_tf init -backend-config=backend.hcl -input=false
run_tf validate
run_tf plan -var-file="${VAR_FILE}" -out="${PLAN_OUT}"
echo "Plan written to ${PLAN_OUT}. Apply with: terraform apply ${PLAN_OUT}"
