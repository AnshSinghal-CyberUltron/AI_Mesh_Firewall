#!/usr/bin/env bash
# Publish StackReady=1 after deploy-ec2 health checks (alarms gate on this).
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
NAMESPACE="${CW_STACK_METRIC_NAMESPACE:-AIMeshFirewall/EC2}"
VALUE="${1:-1}"

if ! command -v aws >/dev/null 2>&1; then
  exit 0
fi

aws cloudwatch put-metric-data \
  --region "${REGION}" \
  --namespace "${NAMESPACE}" \
  --metric-data "MetricName=StackReady,Value=${VALUE},Unit=Count" \
  >/dev/null 2>&1 || true
