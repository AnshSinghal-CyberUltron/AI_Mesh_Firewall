#!/usr/bin/env bash
# THIS VM ONLY. Complete Docker wipe (containers/images/volumes/networks/build cache).
# Does not delete GCP VMs. Preserves git tree, .env, and key files outside Docker.
set -euo pipefail
REPO="${REPO:-/home/contact_cyberultron_com/AI_Mesh_Firewall}"
PRESERVE="${HOME}/.t02-preserve"
mkdir -p "$PRESERVE"
chmod 700 "$PRESERVE"
if [[ -f "$REPO/.env" ]]; then
  cp -a "$REPO/.env" "$PRESERVE/.env"
  chmod 600 "$PRESERVE/.env"
fi
for f in /tmp/t01-openrouter.key /tmp/v3a02.keys.json /tmp/t02-secrets.env /tmp/t02.keys.json; do
  if [[ -f "$f" ]]; then
    cp -a "$f" "$PRESERVE/$(basename "$f")" || true
    chmod 600 "$PRESERVE/$(basename "$f")" 2>/dev/null || true
  fi
done
cd "$REPO"
echo "[t02-wipe] stop/remove ALL containers on this VM (running prune does not remove in-use images)"
if docker ps -aq | grep -q .; then
  docker stop $(docker ps -q) || true
  docker rm -f $(docker ps -aq) || true
fi
echo "[t02-wipe] sudo docker system prune -a --volumes -f"
sudo docker system prune -a --volumes -f
sudo docker builder prune -af || true
sudo docker network prune -f || true
echo "[t02-wipe] remaining docker:"
docker ps -a || true
docker images || true
docker volume ls || true
docker network ls || true
echo "[t02-wipe] done. .env preserved at $PRESERVE/.env"
