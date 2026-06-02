#!/usr/bin/env bash
# Bootstrap remote Terraform state, DynamoDB lock, and ECR repos for AI Mesh Firewall prod.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="ai-mesh-tfstate-${ACCOUNT_ID}"
LOCK_TABLE="ai-mesh-tflock"

echo "==> Region: ${REGION}  Account: ${ACCOUNT_ID}"

if ! aws s3api head-bucket --bucket "${STATE_BUCKET}" 2>/dev/null; then
  echo "==> Creating state bucket ${STATE_BUCKET}"
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${STATE_BUCKET}" --region "${REGION}"
  else
    aws s3api create-bucket \
      --bucket "${STATE_BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
  aws s3api put-bucket-versioning \
    --bucket "${STATE_BUCKET}" \
    --versioning-configuration Status=Enabled
else
  echo "==> State bucket exists: ${STATE_BUCKET}"
fi

if ! aws dynamodb describe-table --table-name "${LOCK_TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  echo "==> Creating lock table ${LOCK_TABLE}"
  aws dynamodb create-table \
    --table-name "${LOCK_TABLE}" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${REGION}"
  aws dynamodb wait table-exists --table-name "${LOCK_TABLE}" --region "${REGION}"
else
  echo "==> Lock table exists: ${LOCK_TABLE}"
fi

for repo in ai-mesh-gateway ai-mesh-control; do
  if ! aws ecr describe-repositories --repository-names "${repo}" --region "${REGION}" >/dev/null 2>&1; then
    echo "==> Creating ECR repo ${repo}"
    aws ecr create-repository \
      --repository-name "${repo}" \
      --image-scanning-configuration scanOnPush=true \
      --encryption-configuration encryptionType=AES256 \
      --region "${REGION}"
  else
    echo "==> ECR repo exists: ${repo}"
  fi
done

BACKEND_HCL="${ROOT}/infra/terraform/envs/prod/backend.hcl"
if [[ -f "${BACKEND_HCL}" ]]; then
  sed -i.bak "s/ai-mesh-tfstate-<ACCOUNT>/ai-mesh-tfstate-${ACCOUNT_ID}/" "${BACKEND_HCL}"
  rm -f "${BACKEND_HCL}.bak"
  echo "==> Updated ${BACKEND_HCL} with bucket name"
fi

echo ""
echo "Next steps:"
echo "  1. Create Secrets Manager entries (see plan Phase 3) and fill infra/terraform/envs/prod/prod.tfvars"
echo "  2. cd infra/terraform/envs/prod && terraform init -backend-config=backend.hcl"
echo "  3. terraform plan -var-file=prod.tfvars -out=prod.plan"
echo "  4. ${ROOT}/infra/scripts/build-push-images.sh v1.0.0"
echo "  5. terraform apply prod.plan"
