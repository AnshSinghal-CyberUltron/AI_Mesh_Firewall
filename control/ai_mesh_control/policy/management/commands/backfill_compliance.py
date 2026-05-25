"""
Management command: backfill_compliance

Iterates existing EnforcementEvent records and creates ComplianceViolation rows
for any event that doesn't already have associated violations.

Run once after deploying the compliance integration:
    docker compose exec backend python manage.py backfill_compliance

Options:
    --batch-size  Number of events to process per batch (default: 500)
    --dry-run     Print counts without writing to the database
"""

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill ComplianceViolation rows for existing EnforcementEvents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of events to process per database batch (default: 500)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print statistics without writing to the database",
        )

    def handle(self, *args, **options):
        from policy.compliance_service import create_violations_for_event
        from policy.models import ComplianceViolation, EnforcementEvent

        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database writes will occur."))

        # Only process events that don't already have compliance violations
        events_without_violations = EnforcementEvent.objects.exclude(
            id__in=ComplianceViolation.objects.values("enforcement_event_id")
        ).order_by("id")

        total = events_without_violations.count()
        self.stdout.write(f"Found {total} EnforcementEvent(s) without compliance violations.")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to backfill."))
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Would process {total} event(s). (dry-run)"))
            return

        processed = 0
        errors = 0
        offset = 0

        while offset < total:
            batch = list(events_without_violations[offset : offset + batch_size])
            for ev in batch:
                try:
                    create_violations_for_event(ev)
                    processed += 1
                except Exception as exc:
                    errors += 1
                    logger.warning("backfill_compliance: error on event %s: %s", ev.id, exc)

            offset += batch_size
            self.stdout.write(f"  Processed {min(offset, total)}/{total}...")

        self.stdout.write(self.style.SUCCESS(f"Backfill complete: {processed} event(s) processed, {errors} error(s)."))
