"""Audit Module 2 enforcement telemetry quality (diagnostic only).

Automatic repair runs in production without this command:
  - Every new event is normalized on Redis drain (_build_enforcement_metadata)
  - Org is resolved from API key prefix during drain
  - Background repair runs on control startup, each drain cycle (rate-limited),
    and Celery beat task module2.tasks.repair_telemetry_metadata

Use this command only to inspect lane distribution and remaining issues:

    docker compose exec control python manage.py module2_telemetry_health
    docker compose exec control python manage.py module2_telemetry_health --org zeroshield --period 24h
    docker compose exec control python manage.py module2_telemetry_health --json
    docker compose exec control python manage.py module2_telemetry_health --apply-metadata-fixes --yes
"""

import json

from django.core.management.base import BaseCommand

from module2.telemetry_health import (
    ISSUE_LEGACY_MCP,
    ISSUE_LEGACY_VECTOR_POLLUTION,
    ISSUE_NULL_ORG,
    ISSUE_ORG_KEY_MISMATCH,
    ISSUE_RAG_STAGE,
    ISSUE_UEBA_BLIND_SPOT,
    apply_metadata_fixes,
    run_telemetry_health,
)
from policy.models import EnforcementEvent


_ISSUE_HELP = {
    ISSUE_LEGACY_MCP: "Add event_type=mcp_tool_call (use --apply-metadata-fixes)",
    ISSUE_UEBA_BLIND_SPOT: "Ensure gateway emits key_prefix on chat enforcement events",
    ISSUE_NULL_ORG: "Re-ingest with organization_id or assign org on the event row",
    ISSUE_ORG_KEY_MISMATCH: "Align user profile org, API key org, and event organization_id",
    ISSUE_RAG_STAGE: "Set event_type=rag_pipeline on RAG pipeline stage events",
    ISSUE_LEGACY_VECTOR_POLLUTION: "Fixed in analytics — no row action needed (informational)",
}


class Command(BaseCommand):
    help = "Audit Module 2 telemetry quality and optionally repair legacy MCP metadata."

    def add_arguments(self, parser):
        parser.add_argument("--period", default="7d", help="Lookback window: 1h, 24h, 7d, 30d")
        parser.add_argument("--org", default=None, help="Organization slug (default: all orgs)")
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        parser.add_argument(
            "--apply-metadata-fixes",
            action="store_true",
            help="Backfill event_type=mcp_tool_call on legacy MCP envelopes",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Required with --apply-metadata-fixes to write changes",
        )
        parser.add_argument(
            "--samples",
            type=int,
            default=5,
            help="Sample event IDs per issue type (default: 5)",
        )

    def handle(self, *args, **options):
        org_slug = options["org"]
        events_qs = EnforcementEvent.objects.all().order_by("-created_at")

        if org_slug:
            from auth.models import Organization

            try:
                org = Organization.objects.get(slug=org_slug)
            except Organization.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Organization not found: {org_slug}"))
                return
            events_qs = events_qs.filter(organization=org)

        report = run_telemetry_health(
            events_qs,
            period=options["period"],
            organization_slug=org_slug,
            max_samples_per_issue=options["samples"],
        )

        if options["json"]:
            self.stdout.write(json.dumps(report.to_dict(), indent=2))
        else:
            self._print_report(report)

        if options["apply_metadata_fixes"]:
            dry_run = not options["yes"]
            if dry_run:
                self.stdout.write(self.style.WARNING("\nDry-run metadata fixes (pass --yes to apply):"))
            stats = apply_metadata_fixes(report.remediable_event_ids, dry_run=dry_run)
            self.stdout.write(
                f"Metadata fixes: examined={stats['examined']} updated={stats['updated']} skipped={stats['skipped']}"
            )

    def _print_report(self, report):
        self.stdout.write(self.style.MIGRATE_HEADING("Module 2 telemetry health"))
        self.stdout.write(f"Period: {report.period} (since {report.since_iso})")
        if report.organization_slug:
            self.stdout.write(f"Organization: {report.organization_slug}")
        self.stdout.write(f"Total events scanned: {report.total_events}")

        if report.total_events == 0:
            self.stdout.write(self.style.WARNING("No enforcement events in window."))
            return

        self.stdout.write("\nLane distribution:")
        for lane, count in sorted(report.lane_counts.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  {lane:14} {count}")

        if not report.issue_counts:
            self.stdout.write(self.style.SUCCESS("\nNo telemetry quality issues detected."))
            return

        self.stdout.write(self.style.WARNING("\nIssues detected:"))
        for code, count in report.issue_counts.most_common():
            self.stdout.write(f"  {code}: {count}")
            hint = _ISSUE_HELP.get(code)
            if hint:
                self.stdout.write(f"    -> {hint}")
            samples = report.sample_event_ids.get(code, [])
            if samples:
                self.stdout.write(f"    sample event ids: {', '.join(str(i) for i in samples)}")
