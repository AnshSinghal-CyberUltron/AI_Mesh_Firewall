#!/usr/bin/env bash
# Local verification: compose merge, IAM JSON, env sample, script syntax, optional terraform validate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PASS=0
FAIL=0

ok() { echo "  OK  $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL $*"; FAIL=$((FAIL + 1)); }

echo "==> Observability local verification"

echo "-- Compose config merge"
if docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker-compose.observability.yml \
  config >/dev/null 2>&1; then
  ok "docker compose config (3-file merge)"
else
  bad "docker compose config failed"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.observability.yml config 2>&1 | tail -20
fi

echo "-- Required files"
for f in \
  docker-compose.observability.yml \
  deploy/observability/cloudwatch-agent-config.json \
  deploy/ec2-instance-role-policy.json \
  scripts/bootstrap-ec2-observability.sh \
  scripts/publish-stack-ready-metric.sh \
  scripts/demo-ec2-lifecycle.sh \
  scripts/reload-cloudwatch-agent.sh \
  infra/terraform/envs/ec2-demo/main.tf \
  infra/terraform/modules/observability/main.tf; do
  [[ -f "${f}" ]] && ok "${f}" || bad "missing ${f}"
done

echo "-- Shell syntax"
for s in scripts/bootstrap-ec2-observability.sh scripts/publish-stack-ready-metric.sh \
  scripts/demo-ec2-lifecycle.sh scripts/reload-cloudwatch-agent.sh scripts/deploy-ec2.sh \
  scripts/sync-to-ec2.sh scripts/verify-observability-local.sh; do
  if bash -n "${s}" 2>/dev/null; then ok "bash -n ${s}"; else bad "bash -n ${s}"; fi
done

echo "-- IAM policy JSON"
if python3 -c "import json; json.load(open('deploy/ec2-instance-role-policy.json'))" 2>/dev/null; then
  ok "ec2-instance-role-policy.json valid JSON"
else
  bad "ec2-instance-role-policy.json invalid"
fi
for sid in CloudWatchLogsShip CloudWatchHostMetrics SSMCloudWatchAgentConfig; do
  if grep -q "\"Sid\": \"${sid}\"" deploy/ec2-instance-role-policy.json; then
    ok "IAM Sid ${sid}"
  else
    bad "IAM Sid ${sid} missing"
  fi
done

echo "-- .env.ec2.sample keys"
for key in EC2_OBSERVABILITY CLOUDWATCH_LOG_GROUP_GATEWAY GATEWAY_LOG_JSON DJANGO_LOG_JSON \
  BEDROCK_LOG_PROMPT_PREVIEW CW_ALARM_PREFIX DEMO_ALARM_GRACE_SECONDS; do
  if grep -q "^${key}=" .env.ec2.sample; then ok ".env.ec2.sample ${key}"; else bad ".env.ec2.sample missing ${key}"; fi
done

echo "-- sync-to-ec2 includes observability"
if grep -q 'docker-compose.observability.yml' scripts/sync-to-ec2.sh; then
  ok "sync-to-ec2.sh ships observability overlay"
else
  bad "sync-to-ec2.sh missing observability overlay"
fi

echo "-- deploy-ec2 observability hook"
if grep -q 'EC2_OBSERVABILITY' scripts/deploy-ec2.sh && grep -q 'publish-stack-ready-metric' scripts/deploy-ec2.sh; then
  ok "deploy-ec2.sh observability + StackReady"
else
  bad "deploy-ec2.sh missing observability hooks"
fi

echo "-- Terraform validate (optional)"
if command -v terraform >/dev/null 2>&1; then
  (
    cd infra/terraform/envs/ec2-demo
    terraform init -backend=false -input=false >/dev/null 2>&1
    terraform validate >/dev/null 2>&1
  ) && ok "terraform validate ec2-demo" || bad "terraform validate ec2-demo"
else
  echo "  skip terraform (not installed)"
fi

echo ""
echo "Result: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
