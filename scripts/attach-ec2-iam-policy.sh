#!/usr/bin/env bash
# Attach ECR pull + Bedrock policy to the EC2 instance role (run with IAM admin credentials).
# Usage: AWS_PROFILE=admin ./scripts/attach-ec2-iam-policy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE_NAME="${EC2_INSTANCE_ROLE_NAME:-ai-mesh-InstanceRole}"
POLICY_NAME="${EC2_INSTANCE_POLICY_NAME:-ai-mesh-ec2-ecr-bedrock}"
POLICY_FILE="${ROOT}/deploy/ec2-instance-role-policy.json"
REGION="${AWS_REGION:-ap-south-1}"

die() { echo "ERROR: $*" >&2; exit 1; }

command -v aws >/dev/null 2>&1 || die "aws CLI required"
[[ -f "${POLICY_FILE}" ]] || die "missing ${POLICY_FILE}"

echo "==> Caller identity"
aws sts get-caller-identity --region "${REGION}" || die "AWS credentials not configured"

ARN="$(aws sts get-caller-identity --query Arn --output text)"
if [[ "${ARN}" == *"BedrockAPIKey"* ]] || [[ "${ARN}" == *"Quarantine"* ]]; then
  die "Use an IAM admin/deploy principal, not BedrockAPIKey or quarantined users"
fi

echo "==> Attach inline policy ${POLICY_NAME} to role ${ROLE_NAME}"
aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "${POLICY_NAME}" \
  --policy-document "file://${POLICY_FILE}"

echo "==> Verify"
aws iam get-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "${POLICY_NAME}" \
  --query PolicyDocument.Statement[*].Sid \
  --output text

echo ""
echo "Done. On EC2 run:"
echo "  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE"
echo "  aws ecr get-login-password --region ${REGION} >/dev/null && echo ECR OK"
echo "  cd ~/AI_Mesh_Firewall && bash scripts/deploy-ec2.sh"
