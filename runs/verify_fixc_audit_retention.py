"""FIX C live verification: tenant-scoped audit-log retention cleanup.

Seeds old AuditLog rows for two orgs, sets a SHORT retention for ORG A and a
LONG retention for ORG B, runs cleanup_old_audit_logs, and asserts ORG A's old
rows are purged while ORG B's old rows SURVIVE (no cross-tenant wipe).
Cleans up after itself.
"""
import datetime as _dt

from django.utils import timezone

from auth.models import Organization
from core.models import AuditLog, FirewallConfig
from core.tasks import cleanup_old_audit_logs

MARKER = "FIXC_VERIFY"

orgA = Organization.objects.get(slug="zeroshield")
orgB = Organization.objects.get(slug="acme-test")

# Snapshot original retention to restore later.
cfgA = FirewallConfig.load(organization=orgA)
cfgB = FirewallConfig.load(organization=orgB)
origA, origB = cfgA.retention_days, cfgB.retention_days

# Clean any leftovers from prior runs.
AuditLog.objects.filter(action=MARKER).delete()

old_ts = timezone.now() - _dt.timedelta(days=400)

def seed(org, n):
    for i in range(n):
        log = AuditLog.objects.create(
            organization=org, action=MARKER, resource="t", details=f"{org.slug}-{i}"
        )
        AuditLog.objects.filter(pk=log.pk).update(created_at=old_ts)

seed(orgA, 5)
seed(orgB, 5)
# Also seed orphan (no org) old rows to test orphan pass independence.
for i in range(3):
    log = AuditLog.objects.create(organization=None, action=MARKER, resource="t", details=f"orphan-{i}")
    AuditLog.objects.filter(pk=log.pk).update(created_at=old_ts)

beforeA = AuditLog.objects.filter(action=MARKER, organization=orgA).count()
beforeB = AuditLog.objects.filter(action=MARKER, organization=orgB).count()
beforeO = AuditLog.objects.filter(action=MARKER, organization__isnull=True).count()
print(f"SEEDED  A={beforeA} B={beforeB} orphan={beforeO}")

# ORG A: very short retention (1 day) => its 400-day-old rows must be purged.
# ORG B: long retention (3650 days) => its 400-day-old rows must SURVIVE.
cfgA.retention_days = 1
cfgA.save(update_fields=["retention_days"])
cfgB.retention_days = 3650
cfgB.save(update_fields=["retention_days"])

cleanup_old_audit_logs()

afterA = AuditLog.objects.filter(action=MARKER, organization=orgA).count()
afterB = AuditLog.objects.filter(action=MARKER, organization=orgB).count()
afterO = AuditLog.objects.filter(action=MARKER, organization__isnull=True).count()
print(f"AFTER   A={afterA} B={afterB} orphan={afterO}")

ok_a = afterA == 0          # short-retention org purged
ok_b = afterB == beforeB    # long-retention org UNTOUCHED (no cross-tenant wipe)
print("RESULT  ORG_A_purged =", ok_a, "| ORG_B_survived =", ok_b,
      "| orphan_purged =", afterO == 0)
print("FIXC_PASS" if (ok_a and ok_b) else "FIXC_FAIL")

# Restore.
cfgA.retention_days = origA
cfgA.save(update_fields=["retention_days"])
cfgB.retention_days = origB
cfgB.save(update_fields=["retention_days"])
AuditLog.objects.filter(action=MARKER).delete()
print("RESTORED retention A=%s B=%s; cleaned markers" % (origA, origB))
