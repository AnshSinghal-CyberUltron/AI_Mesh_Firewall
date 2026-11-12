"""Delete header bells whose recipient is not in the event's org (#120, #122).

Develop fanned escalation and isolation bells out to every superuser of every org
(`| Q(is_superuser=True)`, #120 defects 3-4). The fan-out now reaches only the event org's
admins, and GET /api/notifications/ shows a bell only while its recipient is in the
event's org, so these rows are already invisible. This command removes them.

A row is deleted when the recipient's current profile org differs from the event's org,
including a recipient with no profile or no org, a recipient that no longer exists, and an
event without an org. Dry run by default; ``--apply`` deletes in batches. Idempotent.

    python manage.py purge_cross_org_notifications            # report only
    python manage.py purge_cross_org_notifications --apply    # delete
"""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import F, OuterRef, Q, Subquery


def cross_org_notifications():
    """Queryset of Notification rows whose recipient is not in the event's org."""
    from auth.models import UserProfile
    from policy.models import Notification

    recipient_org = UserProfile.objects.filter(user_id=OuterRef("recipient_id")).values("organization_id")[:1]
    return (
        Notification.objects.annotate(recipient_org_id=Subquery(recipient_org))
        .filter(
            Q(recipient_org_id__isnull=True)
            | Q(enforcement_event__organization_id__isnull=True)
            | ~Q(recipient_org_id=F("enforcement_event__organization_id"))
        )
    )


class Command(BaseCommand):
    help = "Delete notification bells whose recipient is not in the event's organization (#120)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete the rows (default: report only)")
        parser.add_argument("--batch-size", type=int, default=1000, help="Rows per DELETE (default 1000)")

    def handle(self, *args, **options):
        from policy.models import Notification

        batch = max(1, int(options["batch_size"]))
        qs = cross_org_notifications()
        pairs = Counter(
            (row["recipient_org_id"], row["enforcement_event__organization_id"])
            for row in qs.values("recipient_org_id", "enforcement_event__organization_id")
        )
        total = sum(pairs.values())
        self.stdout.write(f"cross-org notifications: {total}")
        for (recipient_org, event_org), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  recipient_org={recipient_org} event_org={event_org}: {n}")
        if not options["apply"]:
            self.stdout.write("dry run: nothing deleted (pass --apply)")
            return
        deleted = 0
        while True:
            ids = list(cross_org_notifications().order_by("pk").values_list("pk", flat=True)[:batch])
            if not ids:
                break
            n, _ = Notification.objects.filter(pk__in=ids).delete()
            deleted += n
        self.stdout.write(self.style.SUCCESS(f"deleted: {deleted}"))
