cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
echo "=== remove fragile gateway override ==="
rm -f docker-compose.gwfix.yml && echo "removed gwfix override"
echo "=== ECR login ==="
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 935951870001.dkr.ecr.ap-south-1.amazonaws.com >/dev/null 2>&1 && echo "login ok"
echo "=== pull + recreate gateway (standard config) ==="
$COMPOSE pull gateway </dev/null 2>&1 | tail -2
$COMPOSE up -d --no-deps --force-recreate gateway </dev/null 2>&1 | tail -4
sleep 6
echo "=== gateway health ==="
for i in $(seq 1 20); do curl -sf http://127.0.0.1:8300/health >/dev/null 2>&1 && { echo "health OK"; break; }; sleep 2; done
echo "=== running gateway config image id (expect sha256:2b5a719c...) ==="
docker inspect ai_mesh_firewall-gateway-1 --format '{{.Image}}'
$COMPOSE ps --format '{{.Service}} {{.Image}} {{.Status}}' gateway
