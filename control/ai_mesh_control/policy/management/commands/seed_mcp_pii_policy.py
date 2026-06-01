"""
Seed the baseline MCP PII guardrail policy for organizations.

Usage:
    python manage.py seed_mcp_pii_policy                # all active orgs
    python manage.py seed_mcp_pii_policy --org-id 3     # one org
    python manage.py seed_mcp_pii_policy --reset        # recreate default rules

Idempotent. Safe to run repeatedly (e.g. on deploy).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from auth.models import Organization
from policy.mcp_seed import seed_mcp_pii_policy


class Command(BaseCommand):
    help = "Seed the baseline enabled MCP PII guardrail policy for organizations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-id",
            type=int,
            default=None,
            help="Only seed this organization id (default: all active orgs).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete and recreate the default seeded rules for matched orgs.",
        )

    def handle(self, *args, **options):
        org_id = options.get("org_id")
        reset = options.get("reset", False)

        if org_id is not None:
            qs = Organization.objects.filter(id=org_id)
            if not qs.exists():
                raise CommandError(f"Organization id={org_id} not found.")
        else:
            qs = Organization.objects.filter(is_active=True)

        total = 0
        created_count = 0
        for org in qs:
            policy, created = seed_mcp_pii_policy(org, reset_rules=reset)
            total += 1
            created_count += int(created)
            self.stdout.write(
                f"  org={org.id} ({org.slug}): policy={policy.code} "
                f"{'created' if created else 'exists'} "
                f"rules={policy.rules.count()}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded MCP PII policy for {total} org(s); {created_count} new policy(ies)."
            )
        )
