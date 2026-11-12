"""#139 pre-promotion Redis check. Run with the control settings:  python manage.py shell < check_redis.py
Compares the live `firewall:config:default` with the platform-default row AND with the 'default' org's row.
Pass: exactly one NULL row, and Redis equals it (not the 'default' org's row, unless both are identical)."""
import json

import redis
from django.conf import settings

from core.models import FirewallConfig, KillSwitch

r = redis.Redis.from_url(settings.REDIS_URL)
raw = r.get("firewall:config:default")
live = json.loads(raw) if raw else None
nulls = list(FirewallConfig.objects.filter(organization__isnull=True).order_by("pk"))
dorg = FirewallConfig.objects.filter(organization__slug="default").first()


def same(row):
    if row is None or live is None:
        return None
    want = json.loads(json.dumps(row.build_gateway_payload(), default=str))
    return {k: (v, live.get(k)) for k, v in want.items() if live.get(k) != v} or True


print("null rows:", [x.pk for x in nulls])
print("redis firewall:config:default present:", live is not None)
print("redis == platform-default row (lowest id):", same(nulls[0]) if nulls else "no NULL row")
print("redis == 'default' org row:", same(dorg) if dorg else "no 'default' org row")
print("kill_switch:default:* keys:", sorted(k.decode() for k in r.scan_iter("kill_switch:default:*")))
print("org-NULL KillSwitch rows:", list(KillSwitch.objects.filter(organization__isnull=True).values_list("pk", "model_name", "is_active")))
