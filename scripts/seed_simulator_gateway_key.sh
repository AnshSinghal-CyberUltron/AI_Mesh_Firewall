#!/usr/bin/env bash
# Seed Redis simulator:default_gateway_key for UI auto-bootstrap (dev only).
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T control python manage.py shell -c "
from django.contrib.auth import get_user_model
from auth.models import Organization
from core.models import GatewayAPIKey
import redis, os
User = get_user_model()
org = Organization.objects.first()
user = User.objects.filter(email='admin@zeroshield.io').first() or User.objects.first()
inst, raw = GatewayAPIKey.generate_key(
    name='simulator-default',
    owner=user,
    project_id='simulator-default',
)
if org and not inst.organization_id:
    inst.organization = org
    inst.save(update_fields=['organization'])
r = redis.Redis.from_url(os.environ.get('REDIS_URL','redis://redis:6379/0'), decode_responses=True)
r.set('simulator:default_gateway_key', raw)
print('Seeded simulator default gateway key prefix:', inst.prefix)
"
