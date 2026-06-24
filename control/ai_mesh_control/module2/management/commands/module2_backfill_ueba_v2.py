"""One-shot backfill for UEBA v2 fields and initial baselines."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import GatewayAPIKey
from module2.tasks import compute_ueba_risk_snapshots, refresh_api_key_baselines
from module2.ueba_service import get_or_create_org_settings, refresh_baseline_for_key
from policy.models import EnforcementEvent


class Command(BaseCommand):
    help = "Backfill UEBA v2: simulator purpose, learning mode, baselines, and initial snapshots."

    def handle(self, *args, **options):
        sim_updated = GatewayAPIKey.objects.filter(name="simulator-default").update(key_purpose="simulator")
        learning_updated = GatewayAPIKey.objects.exclude(ueba_mode="active").update(ueba_mode="learning")
        self.stdout.write(f"Set simulator-default purpose ({sim_updated} rows); reset learning mode ({learning_updated} rows).")

        from module2.ueba_metrics import count_lifetime_events_by_prefix

        baseline_count = 0
        for key in GatewayAPIKey.objects.select_related("organization").iterator():
            org = key.organization
            if not org:
                continue
            get_or_create_org_settings(org)
            events = EnforcementEvent.objects.filter(organization=org)
            lifetime = count_lifetime_events_by_prefix([key], events).get(key.prefix, 0)
            if lifetime >= 50:
                if refresh_baseline_for_key(key, events):
                    baseline_count += 1
                    if key.ueba_mode != "active":
                        key.ueba_mode = "active"
                        if not key.ueba_baseline_locked_at:
                            key.ueba_baseline_locked_at = timezone.now()
                        key.save(update_fields=["ueba_mode", "ueba_baseline_locked_at"])

        self.stdout.write(f"Computed initial baselines for {baseline_count} keys with ≥50 events.")

        refresh_stats = refresh_api_key_baselines()
        snapshot_stats = compute_ueba_risk_snapshots()
        self.stdout.write(f"refresh_api_key_baselines: {refresh_stats}")
        self.stdout.write(f"compute_ueba_risk_snapshots: {snapshot_stats}")
        self.stdout.write(self.style.SUCCESS("UEBA v2 backfill complete."))
