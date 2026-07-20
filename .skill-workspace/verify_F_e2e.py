# F end-to-end (real path) after the drain-bug fix:
#   1. Clear the stale break-test backlog (unscoped garbage) + orphaned processing key.
#   2. POST a real HTTP ingestion event via a per-org agent key (appended at the TAIL).
#   3. Let the LIVE control-thread drain (every 1s) persist it — no manual drain loop
#      (that would race the live consumers). Confirm the row lands and the queue stays low.
import time, uuid, requests, redis
from django.conf import settings
from auth.models import Organization
from core.models import OrganizationAgentKey
from core.tasks import REDIS_TELEMETRY_KEY
from policy.models import EnforcementEvent

B = "http://localhost:8000"
rc = redis.from_url(settings.REDIS_URL, decode_responses=True)

before_len = rc.llen(REDIS_TELEMETRY_KEY)
rc.delete(REDIS_TELEMETRY_KEY)                       # stale break-test flood (unscoped → never persisted)
rc.delete(f"{REDIS_TELEMETRY_KEY}:processing")       # orphaned shared processing key from the old bounce
print(f"cleared stale backlog: {before_len} -> {rc.llen(REDIS_TELEMETRY_KEY)}")

org = Organization.objects.order_by("id").first()
inst, raw = OrganizationAgentKey.generate_key(org, name="verify-F-e2e")
H = {"Authorization": f"Bearer {raw}"}
marker = uuid.uuid4().hex[:10]
payload = {"agent_id": f"e2e-{marker}", "timestamp": "2026-06-12T00:00:00Z",
           "event_type": "request", "data": {"action": "allow", "marker": marker}}
r = requests.post(B + "/api/ingestion/events/", json=payload, headers=H, timeout=15)
print("HTTP ingest:", r.status_code, r.json())

found = 0
for _ in range(25):                                  # live drain runs every 1s
    time.sleep(1.0)
    found = EnforcementEvent.objects.filter(
        organization_id=org.id, metadata__source="ingestion_api", metadata__agent_id=f"e2e-{marker}"
    ).count()
    if found:
        break
queue_after = rc.llen(REDIS_TELEMETRY_KEY)
inst.delete()

ok = (r.status_code == 202 and r.json().get("persisted") is True and found == 1)
print(f"\nF-E2E {'PASS' if ok else 'FAIL'}: http={r.status_code} persisted_flag={r.json().get('persisted')} "
      f"db_rows={found} queue_len_after={queue_after} (live drain kept up)")
