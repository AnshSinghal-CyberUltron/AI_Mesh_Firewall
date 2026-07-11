cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
echo "=== gateway logs: injection request zs-239e24ff38ec ==="
$COMPOSE logs --since 20m gateway </dev/null 2>&1 | grep -Ei 'zs-239e24ff38ec|blocked by scanner|input_blocked|prompt_injection|jailbreak|scan.*verdict|redact' | tail -40
echo "=== recent scanner/enforcement lines ==="
$COMPOSE logs --since 20m gateway </dev/null 2>&1 | grep -Ei 'blocked by scanner|input_blocked|Input blocked|redaction|threat_type' | tail -30
