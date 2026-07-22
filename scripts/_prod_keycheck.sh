set -e
cd /home/ec2-user/AI_Mesh_Firewall
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
KEY=$(grep -E '^DEMO_GATEWAY_KEY=' .env | head -1 | cut -d= -f2-)
PFX=${KEY:0:8}
echo "===CURRENT DEMO KEY PREFIX==="
echo "prefix=$PFX len=${#KEY}"
echo "===DB LOOKUP BY PREFIX==="
$COMPOSE exec -T control sh -c "cd /app/control && python manage.py shell -c \"
from core.models import GatewayAPIKey
qs=GatewayAPIKey.objects.filter(prefix='$PFX')
if not qs:
    print('NO DB MATCH for prefix')
for k in qs:
    print(f'org={k.organization_id} active={k.is_active} expires={k.expires_at} project={k.project_id!r} name={k.name!r}')
\"" 2>/dev/null | grep -E 'org=|NO DB MATCH'
echo "===LIVE GATEWAY AUTH TEST (models endpoint)==="
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8300/v1/models -H "Authorization: Bearer $KEY")
echo "GET /v1/models -> HTTP $code"
