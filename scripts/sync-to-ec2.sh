#!/usr/bin/env bash
# Copy deploy artifacts to EC2 and optionally run deploy-ec2.sh remotely.
# Usage:
#   ./scripts/sync-to-ec2.sh                    # sync only
#   ./scripts/sync-to-ec2.sh --deploy           # sync + remote deploy
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
ssh "${SSH_HOST}" "mkdir -p '${REMOTE_DIR}/deploy' '${REMOTE_DIR}/scripts' '${REMOTE_DIR}/frontend'"

RSYNC_SSH=(rsync -az --delete -e ssh)

echo "==> Sync compose + deploy config"
"${RSYNC_SSH[@]}" \
  "${ROOT}/docker-compose.yml" \
  "${ROOT}/docker-compose.prod.yml" \
  "${SSH_HOST}:${REMOTE_DIR}/"

"${RSYNC_SSH[@]}" \
  "${ROOT}/deploy/" \
  "${SSH_HOST}:${REMOTE_DIR}/deploy/"

"${RSYNC_SSH[@]}" \
  "${ROOT}/scripts/deploy-ec2.sh" \
  "${SSH_HOST}:${REMOTE_DIR}/scripts/"

echo "==> Sync .env (strips static AWS keys — EC2 uses instance role)"
ENV_SYNC="$(mktemp)"
grep -vE '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)=' "${ROOT}/.env" > "${ENV_SYNC}"
grep -q '^USE_EC2_INSTANCE_ROLE=' "${ENV_SYNC}" || echo 'USE_EC2_INSTANCE_ROLE=true' >> "${ENV_SYNC}"
"${RSYNC_SSH[@]}" "${ENV_SYNC}" "${SSH_HOST}:${REMOTE_DIR}/.env"
rm -f "${ENV_SYNC}"

echo "==> Sync frontend sources (npm build runs on EC2)"
"${RSYNC_SSH[@]}" \
  --exclude node_modules \
  --exclude dist \
  "${ROOT}/frontend/" \
  "${SSH_HOST}:${REMOTE_DIR}/frontend/"

ssh "${SSH_HOST}" "chmod +x '${REMOTE_DIR}/scripts/deploy-ec2.sh'"

echo "Synced to ${SSH_HOST}:${REMOTE_DIR}"

if [[ "${DEPLOY_AFTER}" == true ]]; then
  echo "==> Remote deploy"
  ssh -t "${SSH_HOST}" "cd '${REMOTE_DIR}' && bash scripts/deploy-ec2.sh"
fi
