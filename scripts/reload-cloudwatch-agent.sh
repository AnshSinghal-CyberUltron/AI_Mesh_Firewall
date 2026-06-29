#!/usr/bin/env bash
# Reload CloudWatch Agent from SSM after terraform apply updates config.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
SSM_CONFIG="${CW_AGENT_CONFIG_SSM:-/ai-mesh-firewall/ec2/cloudwatch-agent/config}"
CTL="/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"

[[ -x "${CTL}" ]] || { echo "CloudWatch Agent not installed"; exit 1; }

sudo "${CTL}" -a fetch-config -m ec2 -c "ssm:${SSM_CONFIG}" -s
echo "CloudWatch Agent reloaded from ssm:${SSM_CONFIG}"
