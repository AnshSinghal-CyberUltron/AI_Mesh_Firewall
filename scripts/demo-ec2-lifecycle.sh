#!/usr/bin/env bash
# Demo lifecycle: suppress infra alarms before stop; re-enable after start + deploy.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
INSTANCE_ID="${EC2_INSTANCE_ID:-}"
ALARM_PREFIX="${CW_ALARM_PREFIX:-ai-mesh-firewall-demo-}"

_alarms() {
  aws cloudwatch describe-alarms --region "${REGION}" \
    --alarm-name-prefix "${ALARM_PREFIX}" \
    --query 'MetricAlarms[].AlarmName' --output text 2>/dev/null || true
}

disable_alarms() {
  local names
  names="$(_alarms)"
  [[ -n "${names}" ]] || return 0
  aws cloudwatch disable-alarm-actions --region "${REGION}" --alarm-names ${names}
  echo "Disabled alarm actions: ${names}"
}

enable_alarms() {
  local names
  names="$(_alarms)"
  [[ -n "${names}" ]] || return 0
  aws cloudwatch enable-alarm-actions --region "${REGION}" --alarm-names ${names}
  echo "Enabled alarm actions: ${names}"
}

stop_demo() {
  disable_alarms
  bash "$(dirname "$0")/publish-stack-ready-metric.sh" 0
  if [[ -n "${INSTANCE_ID}" ]]; then
    aws ec2 stop-instances --region "${REGION}" --instance-ids "${INSTANCE_ID}"
    echo "Stopping ${INSTANCE_ID} (containers pause with instance; config persists on EBS)"
  else
    echo "Set EC2_INSTANCE_ID to stop the instance from this script"
  fi
}

start_demo() {
  if [[ -n "${INSTANCE_ID}" ]]; then
    aws ec2 start-instances --region "${REGION}" --instance-ids "${INSTANCE_ID}"
    aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"
  fi
  bash "$(dirname "$0")/bootstrap-ec2-observability.sh"
  bash "$(dirname "$0")/deploy-ec2.sh"
  sleep "${DEMO_ALARM_GRACE_SECONDS:-900}"
  enable_alarms
  echo "Demo stack up; alarms enabled after ${DEMO_ALARM_GRACE_SECONDS:-900}s grace"
}

case "${1:-}" in
  stop) stop_demo ;;
  start) start_demo ;;
  disable-alarms) disable_alarms ;;
  enable-alarms) enable_alarms ;;
  *)
    echo "Usage: EC2_INSTANCE_ID=i-xxx $0 {stop|start|disable-alarms|enable-alarms}"
    exit 1
    ;;
esac
