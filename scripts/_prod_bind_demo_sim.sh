cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
KEY=$(grep -E '^DEMO_GATEWAY_KEY=' .env | head -1 | cut -d= -f2-)
PREFIX="${KEY:0:8}"

cat > /tmp/promote_demo_sim.py <<PY
from core.models import GatewayAPIKey
from auth.models import Organization
org = Organization.objects.get(slug='zeroshield')
key = GatewayAPIKey.objects.filter(organization=org, prefix='${PREFIX}', is_active=True).first()
if key is None:
    raise SystemExit('demo key not found for prefix ${PREFIX}')
promoted, raw = GatewayAPIKey.promote_as_org_simulator(org, key)
print('SIM_PREFIX=' + promoted.prefix)
print('SIM_KEY_ID=' + str(promoted.id))
print('SIM_PROJECT=' + str(promoted.project_id))
print('SIM_NAME=' + str(promoted.name))
PY

$COMPOSE cp /tmp/promote_demo_sim.py control:/tmp/promote_demo_sim.py >/dev/null 2>&1
$COMPOSE exec -T control sh -c 'cd /app/control && python manage.py shell < /tmp/promote_demo_sim.py' </dev/null || true
rm -f /tmp/promote_demo_sim.py

echo "=== telemetry health (repair metadata if needed) ==="
$COMPOSE exec -T control sh -c "cd /app/control && python manage.py module2_telemetry_health --org zeroshield --period 24h --apply-metadata-fixes --yes" </dev/null || true

echo "=== demo internal health ==="
$COMPOSE exec -T demo sh -c "python - <<'PY'
import urllib.request
for path in ('/api/health','/api/readiness','/api/models'):
    try:
        r=urllib.request.urlopen('http://127.0.0.1:8770'+path, timeout=15)
        print(path,'->',r.status)
    except Exception as e:
        print(path,'-> ERR', getattr(e,'code',e))
PY" </dev/null || true
