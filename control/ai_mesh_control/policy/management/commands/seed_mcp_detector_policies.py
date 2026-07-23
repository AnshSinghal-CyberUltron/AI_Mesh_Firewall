"""Seed system ``detector`` guardrail policies replicating each MCP server's current
posture + scan-control enforcement (Phase 2 collapse-to-one-surface).

Usage:
    python manage.py seed_mcp_detector_policies                # all active orgs
    python manage.py seed_mcp_detector_policies --org-id 3     # one org
    python manage.py seed_mcp_detector_policies --reset        # recreate seeded rules

Idempotent. Safe to run repeatedly. Posture stays live as the safety net — the seeded
policies enforce the SAME action at the SAME scope, so this is behavior-identical until
Phase 3 retires the posture.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from auth.models import Organization
from policy.mcp_seed import seed_mcp_detector_policies


class Command(BaseCommand):
    help = "Seed system detector guardrail policies from each MCP server's posture + scan-controls."

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=int, default=None,
                            help="Only seed this organization id (default: all active orgs).")
        parser.add_argument("--reset", action="store_true",
                            help="Delete and recreate the seeded detector rules for matched orgs.")

    def handle(self, *args, **options):
        org_id = options.get("org_id")
        reset = options.get("reset", False)

        if org_id is not None:
            qs = Organization.objects.filter(id=org_id)
            if not qs.exists():
                raise CommandError(f"Organization id={org_id} not found.")
        else:
            qs = Organization.objects.filter(is_active=True)

        total_orgs = 0
        total_policies = 0
        total_rules = 0
        for org in qs:
            results = seed_mcp_detector_policies(org, reset_rules=reset)
            total_orgs += 1
            for code, created, n_rules in results:
                total_policies += 1
                total_rules += n_rules
                self.stdout.write(
                    f"  org={org.id} ({org.slug}): {code} "
                    f"{'created' if created else 'exists'} +{n_rules} rule(s)"
                )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded detector policies for {total_orgs} org(s): "
            f"{total_policies} server policy(ies), {total_rules} new rule(s)."
        ))
