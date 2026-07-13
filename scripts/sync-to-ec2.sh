#!/usr/bin/env bash
# Sync deploy config to EC2 (no application source — images come from ECR only).
# Usage:
#   ./scripts/sync-to-ec2.sh                    # sync only
#   ./scripts/sync-to-ec2.sh --deploy           # sync + remote deploy (pull + compose up)
#   SSH_HOST=AIMeshFirewall REMOTE_DIR=~/AI_Mesh_Firewall ./scripts/sync-to-ec2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_HOST="${SSH_HOST:-AIMeshFirewall}"
REMOTE_DIR="${REMOTE_DIR:-/home/ec2-user/AI_Mesh_Firewall}"
DEPLOY_AFTER=false

for arg in "$@"; do
  case "$arg" in
    --deploy) DEPLOY_AFTER=true ;;
    -h|--help)
      echo "Usage: $0 [--deploy]"
      echo "Syncs compose, .env, and deploy scripts — application images (incl. nginx UI) come from ECR only."
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

cd "$ROOT"

[[ -f .env ]] || { echo "Missing .env in repo root — copy .env.ec2.sample and fill in values." >&2; exit 1; }

# shellcheck disable=SC1091
set -a && source .env && set +a
if [[ -z "${ECR_REGISTRY:-}" || -z "${IMAGE_TAG:-}" ]]; then
  echo "Add ECR_REGISTRY and IMAGE_TAG to .env before syncing." >&2
  exit 1
fi

echo "==> Prepare remote directory on ${SSH_HOST}:${REMOTE_DIR}"
ssh "${SSH_HOST}" "mkdir -p '${REMOTE_DIR}/scripts'"

echo "==> Remove stale app source from old deploys (if any)"
ssh "${SSH_HOST}" "if command -v sudo >/dev/null 2>&1; then \
  sudo rm -rf '${REMOTE_DIR}/frontend' '${REMOTE_DIR}/control' '${REMOTE_DIR}/gateway' '${REMOTE_DIR}/workers'; \
else \
  rm -rf '${REMOTE_DIR}/frontend' '${REMOTE_DIR}/control' '${REMOTE_DIR}/gateway' '${REMOTE_DIR}/workers'; \
fi" || true

# Use scp -C (available in Git for Windows / OpenSSH) instead of rsync.
_scp() { scp -C "$@"; }

echo "==> Sync compose files"
COMPOSE_FILES=(
  "${ROOT}/docker-compose.yml"
  "${ROOT}/docker-compose.prod.yml"
)
[[ -f "${ROOT}/docker-compose.observability.yml" ]] && COMPOSE_FILES+=("${ROOT}/docker-compose.observability.yml")
_scp "${COMPOSE_FILES[@]}" "${SSH_HOST}:${REMOTE_DIR}/"

echo "==> Sync deploy / observability scripts and agent config"
ssh "${SSH_HOST}" "mkdir -p '${REMOTE_DIR}/scripts' '${REMOTE_DIR}/deploy/observability'"
OBS_SCRIPTS=(
  deploy-ec2.sh
  ec2-generate-origin-ssl.sh
  bootstrap-ec2-observability.sh
  publish-stack-ready-metric.sh
  demo-ec2-lifecycle.sh
  reload-cloudwatch-agent.sh
)
for s in "${OBS_SCRIPTS[@]}"; do
  [[ -f "${ROOT}/scripts/${s}" ]] || continue
  _scp "${ROOT}/scripts/${s}" "${SSH_HOST}:${REMOTE_DIR}/scripts/"
done
# Post-deploy prod helpers (demo key mint/bind, gateway verify).
for s in "${ROOT}"/scripts/_prod_*.sh; do
  [[ -f "${s}" ]] || continue
  _scp "${s}" "${SSH_HOST}:${REMOTE_DIR}/scripts/"
done
if [[ -f "${ROOT}/deploy/observability/cloudwatch-agent-config.json" ]]; then
  _scp \
    "${ROOT}/deploy/observability/cloudwatch-agent-config.json" \
    "${ROOT}/deploy/observability/alarm-email-example.html" \
    "${ROOT}/deploy/observability/alarm-runbooks.json" \
    "${ROOT}/deploy/observability/prometheus-scrape.example.yml" \
    "${SSH_HOST}:${REMOTE_DIR}/deploy/observability/"
fi

echo "==> Sync .env (strips static AWS keys — EC2 uses instance role for ECR pull)"
ENV_SYNC="$(mktemp)"
grep -vE '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)=' "${ROOT}/.env" > "${ENV_SYNC}"
grep -q '^USE_EC2_INSTANCE_ROLE=' "${ENV_SYNC}" || echo 'USE_EC2_INSTANCE_ROLE=true' >> "${ENV_SYNC}"
_scp "${ENV_SYNC}" "${SSH_HOST}:${REMOTE_DIR}/.env"
rm -f "${ENV_SYNC}"

ssh "${SSH_HOST}" "chmod +x '${REMOTE_DIR}/scripts/'*.sh 2>/dev/null || true"

echo "Synced deploy config to ${SSH_HOST}:${REMOTE_DIR} (images: ${ECR_REGISTRY} tag ${IMAGE_TAG})"

if [[ "${DEPLOY_AFTER}" == true ]]; then
  echo "==> Remote deploy (ECR pull + compose up)"
  ssh -t "${SSH_HOST}" "cd '${REMOTE_DIR}' && bash scripts/deploy-ec2.sh"
fi
