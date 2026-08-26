"""Refresh hourly analytics facts used by 24h/7d/30d dashboard rollup reads."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rebuild AnalyticsHourly* facts for one org or every org (Phase 0c C-2)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24 * 30,
            help="Lookback hours to materialize (default 720 = 30d).",
        )
        parser.add_argument("--org-id", type=int, default=None, help="Limit to one organization id.")

    def handle(self, *args, **options):
        from auth.models import Organization
        from policy.analytics_rollup import refresh_org_rollups

        hours = max(int(options["hours"]), 1)
        org_id = options["org_id"]
        if org_id is not None:
            orgs = Organization.objects.filter(pk=org_id)
        else:
            orgs = Organization.objects.all().order_by("id")
        count = orgs.count()
        self.stdout.write(f"Refreshing analytics rollups for {count} org(s), hours={hours}.")
        for org in orgs:
            refresh_org_rollups(org.id, hours=hours)
            self.stdout.write(self.style.SUCCESS(f"  org_id={org.id} slug={org.slug} refreshed"))
