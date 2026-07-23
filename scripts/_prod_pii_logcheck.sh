cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
$COMPOSE logs gateway --tail 80 2>/dev/null | grep -E "zs-2c252fbaa92d|no-op|input_blocked|PII/secret|redacting" | tail -20
