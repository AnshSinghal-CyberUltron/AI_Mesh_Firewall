"""Drain gateway-v2 audit streams into EnforcementEvent once, or until stopped."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Copy gateway-v2 audit records into EnforcementEvent rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep reading. The control process already does this in its drain thread.",
        )

    def handle(self, *args, **options):
        import time

        from core.v2_audit_ingest import ingest_pending

        if not options["loop"]:
            count = ingest_pending()
            self.stdout.write(f"v2 audit ingest wrote {count} row(s)")
            return
        while True:
            try:
                ingest_pending()
            except Exception as exc:  # noqa: BLE001 — a tick must not kill the loop.
                self.stderr.write(f"v2 audit ingest failed: {type(exc).__name__}")
            time.sleep(2)
