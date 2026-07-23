set -e
cd /home/ec2-user/AI_Mesh_Firewall
REG=935951870001.dkr.ecr.ap-south-1.amazonaws.com
NEWTAG=v1.0.20260709-pii-redact2
echo "===ECR LOGIN (instance role)==="
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin "$REG" >/dev/null && echo "login ok"
echo "===WRITE OVERRIDE==="
cat > docker-compose.gwfix.yml <<YAML
services:
  gateway:
    image: ${REG}/ai-mesh-gateway:${NEWTAG}
YAML
cat docker-compose.gwfix.yml
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
echo "===PULL GATEWAY==="
$COMPOSE pull gateway
echo "===RECREATE GATEWAY==="
$COMPOSE up -d --no-deps --force-recreate gateway
echo "===WAIT + HEALTH==="
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8300/health >/dev/null 2>&1 && { echo "gateway health OK"; break; }; sleep 2; done
echo "===RUNNING IMAGE==="
$COMPOSE ps --format '{{.Service}} {{.Image}} {{.Status}}' gateway
