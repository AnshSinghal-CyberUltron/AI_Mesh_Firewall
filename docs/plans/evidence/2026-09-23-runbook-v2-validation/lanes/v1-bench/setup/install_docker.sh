#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# wait for any boot-time apt/unattended-upgrades to release the lock
for i in $(seq 1 60); do
  if ! sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 && ! sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1; then break; fi
  sleep 5
done
sudo apt-get update -y -q
sudo apt-get install -y -q ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update -y -q
sudo apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
sudo docker version --format '{{.Server.Version}}'
sudo docker compose version
