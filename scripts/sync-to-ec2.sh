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

RSYNC_SSH=(rsync -az -e ssh)

echo "==> Sync compose files"
"${RSYNC_SSH[@]}" \
  "${ROOT}/docker-compose.yml" \
  "${ROOT}/docker-compose.prod.yml" \
  "${SSH_HOST}:${REMOTE_DIR}/"

echo "==> Sync deploy scripts"
"${RSYNC_SSH[@]}" \
  "${ROOT}/scripts/deploy-ec2.sh" \
  "${ROOT}/scripts/ec2-generate-origin-ssl.sh" \
  "${SSH_HOST}:${REMOTE_DIR}/scripts/"

echo "==> Sync .env (strips static AWS keys — EC2 uses instance role for ECR pull)"
ENV_SYNC="$(mktemp)"
grep -vE '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)=' "${ROOT}/.env" > "${ENV_SYNC}"
grep -q '^USE_EC2_INSTANCE_ROLE=' "${ENV_SYNC}" || echo 'USE_EC2_INSTANCE_ROLE=true' >> "${ENV_SYNC}"
"${RSYNC_SSH[@]}" "${ENV_SYNC}" "${SSH_HOST}:${REMOTE_DIR}/.env"
rm -f "${ENV_SYNC}"

ssh "${SSH_HOST}" "chmod +x '${REMOTE_DIR}/scripts/deploy-ec2.sh' '${REMOTE_DIR}/scripts/ec2-generate-origin-ssl.sh'"

echo "Synced deploy config to ${SSH_HOST}:${REMOTE_DIR} (images: ${ECR_REGISTRY} tag ${IMAGE_TAG})"

if [[ "${DEPLOY_AFTER}" == true ]]; then
  echo "==> Remote deploy (ECR pull + compose up)"
  ssh -t "${SSH_HOST}" "cd '${REMOTE_DIR}' && bash scripts/deploy-ec2.sh"
fi
