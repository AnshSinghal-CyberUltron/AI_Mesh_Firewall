cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE exec -T control sh -c 'cd /app/control && python manage.py shell -c "
from auth.models import Organization
from core.models import FirewallConfig
org=Organization.objects.get(slug=\"zeroshield\")
try:
    fc=FirewallConfig.objects.get(organization=org)
    print(f\"FWMODE enforcement_mode={fc.enforcement_mode!r} block_on_injection={getattr(fc,\"scan_block_on_injection\",None)} scan_input={getattr(fc,\"scan_input\",None)} redact_pii={getattr(fc,\"redact_pii\",None)}\")
except FirewallConfig.DoesNotExist:
    print(\"FWMODE no FirewallConfig for org\")
"' </dev/null 2>/dev/null | grep FWMODE
