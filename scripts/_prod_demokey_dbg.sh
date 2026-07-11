cd /home/ec2-user/AI_Mesh_Firewall
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
cat > /tmp/mint_demo_key.py <<'PY'
from core.models import GatewayAPIKey
from auth.models import Organization
org = Organization.objects.get(slug='zeroshield')
src = GatewayAPIKey.objects.filter(organization=org, is_active=True).exclude(owner__isnull=True).first()
owner = src.owner if src else None
print("OWNER:", owner, "profile_org:", getattr(getattr(owner,'profile',None),'organization',None))
raw = None; key = None
for k in GatewayAPIKey.objects.filter(organization=org, is_active=True, project_id='demo-app').order_by('-created_at'):
    r = k.recover_secret()
    print("EXISTING demo-app key", k.prefix, "recoverable=", bool(r))
    if r:
        key = k; raw = r; break
if not raw:
    key, raw = GatewayAPIKey.generate_key(name='Demo App Key', owner=owner, project_id='demo-app', expires_at=None)
    key.store_secret(raw)
    if not key.organization_id:
        key.organization = org; key.save(update_fields=['organization'])
print("RESULT prefix=", key.prefix, "org=", key.organization_id, "active=", key.is_active, "rawlen=", len(raw))
PY
$COMPOSE cp /tmp/mint_demo_key.py control:/tmp/mint_demo_key.py >/dev/null 2>&1
$COMPOSE exec -T control sh -c 'cd /app/control && python manage.py shell < /tmp/mint_demo_key.py' 2>&1 | grep -E 'OWNER:|EXISTING|RESULT|Error|Traceback|Exception' 
