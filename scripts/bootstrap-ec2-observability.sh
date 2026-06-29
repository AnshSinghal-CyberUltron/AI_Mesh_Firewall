#!/usr/bin/env bash
# Idempotent EC2 bootstrap: CloudWatch Agent + docker log rotation.
# Safe on every boot / deploy — config lives on EBS + SSM (after terraform apply).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-ap-south-1}"
SSM_CONFIG="${CW_AGENT_CONFIG_SSM:-/ai-mesh-firewall/ec2/cloudwatch-agent/config}"
AGENT_CONFIG_LOCAL="${ROOT}/deploy/observability/cloudwatch-agent-config.json"

log() { echo "==> $*"; }

_ensure_docker_log_rotation() {
  local daemon_json="/etc/docker/daemon.json"
  if command -v sudo >/dev/null 2>&1; then
    sudo mkdir -p /etc/docker
    if [[ ! -f "${daemon_json}" ]] || ! grep -q max-size "${daemon_json}" 2>/dev/null; then
      log "Configure docker json-file rotation (prevent disk fill between demos)"
      sudo tee "${daemon_json}" >/dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
EOF
      sudo systemctl restart docker || true
    fi
  fi
}

_install_cloudwatch_agent() {
  if command -v amazon-cloudwatch-agent >/dev/null 2>&1; then
    return 0
  fi
  log "Installing amazon-cloudwatch-agent"
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y amazon-cloudwatch-agent
  elif command -v apt-get >/dev/null 2>&1; then
    wget -q "https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb" -O /tmp/cwagent.deb || true
    sudo dpkg -i /tmp/cwagent.deb || sudo apt-get install -y /tmp/cwagent.deb
  else
    log "WARN: unknown OS — install CloudWatch Agent manually"
    return 1
  fi
}

_start_cloudwatch_agent() {
  local ctl="/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"
  [[ -x "${ctl}" ]] || return 0

  if aws ssm get-parameter --name "${SSM_CONFIG}" --region "${REGION}" >/dev/null 2>&1; then
    log "Starting CloudWatch Agent from SSM ${SSM_CONFIG}"
    sudo "${ctl}" -a fetch-config -m ec2 -c "ssm:${SSM_CONFIG}" -s
  elif [[ -f "${AGENT_CONFIG_LOCAL}" ]]; then
    log "Starting CloudWatch Agent from local file (run terraform ec2-demo for SSM)"
    sudo "${ctl}" -a fetch-config -m ec2 -c "file:${AGENT_CONFIG_LOCAL}" -s
  else
    log "WARN: no CW Agent config found"
  fi
}

_ensure_docker_log_rotation
_install_cloudwatch_agent || true
_start_cloudwatch_agent || true

log "Observability bootstrap complete"
