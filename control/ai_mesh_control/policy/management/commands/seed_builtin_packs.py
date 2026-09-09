"""
Seed the built-in detection-family policy packages (DEFAULT-OFF) for organization(s).

Re-homes the gateway's previously-built-in Tier-1 library as per-org, toggleable
system ``Policy`` packages — one per family from ``policy.builtin_packs_catalog``.
Modeled on ``seed_ciso_policy_package``; the material difference is that every
family package is seeded **``enabled=False``** (policy-driven-detection Requirement
4.2), so seeding never causes detection until an operator explicitly enables a
package.

Local:
    docker compose exec control python manage.py seed_builtin_packs --org-slug zeroshield

Options:
    --org-id / --org-slug   Target one organization (required unless --all-orgs)
    --all-orgs              Seed every active organization
    --reset                 Delete and recreate all package rules from catalog
    --no-compile            Skip Redis policy compilation after seeding
    --dry-run               Print what would happen without writing
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from auth.models import Organization
from policy.builtin_packs_catalog import (
    PACKAGE_ID,
    PACKAGE_VERSION,
    FAMILIES,
    build_all_rule_dicts,
)
from policy.builtin_packs_seed import compile_organization_policies, seed_builtin_packs


class Command(BaseCommand):
    help = (
        "Seed the built-in detection-family packages (DEFAULT-OFF, one Policy per "
        "family) for one or more organizations."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group()
        target.add_argument("--org-id", type=int, default=None, help="Organization primary key.")
        target.add_argument("--org-slug", type=str, default=None, help="Organization slug.")
        target.add_argument("--all-orgs", action="store_true", help="Seed all active organizations.")
        parser.add_argument("--reset", action="store_true", help="Remove existing package rules.")
        parser.add_argument("--no-compile", action="store_true", help="Skip Redis compile/push.")
        parser.add_argument("--dry-run", action="store_true", help="Validate without DB writes.")

    def handle(self, *args, **options):
        org_id = options.get("org_id")
        org_slug = options.get("org_slug")
        all_orgs = options.get("all_orgs")
        reset = options.get("reset", False)
        no_compile = options.get("no_compile", False)
        dry_run = options.get("dry_run", False)

        total_catalog_rules = sum(len(r) for r in build_all_rule_dicts().values())
        self.stdout.write(
            f"Built-in packs: {PACKAGE_ID} v{PACKAGE_VERSION} "
            f"({len(FAMILIES)} families, {total_catalog_rules} rules in catalog) "
            f"— seeded DEFAULT-OFF (enabled=False)"
        )

        if org_id is not None:
            qs = Organization.objects.filter(id=org_id)
            if not qs.exists():
                raise CommandError(f"Organization id={org_id} not found.")
        elif org_slug:
            qs = Organization.objects.filter(slug=org_slug)
            if not qs.exists():
                raise CommandError(f"Organization slug={org_slug!r} not found.")
        elif all_orgs:
            qs = Organization.objects.filter(is_active=True)
        else:
            raise CommandError("Specify --org-id, --org-slug, or --all-orgs.")

        if dry_run:
            for org in qs:
                self.stdout.write(
                    f"  [dry-run] would seed org={org.id} slug={org.slug} "
                    f"{len(FAMILIES)} family package(s) DEFAULT-OFF "
                    f"reset={reset} compile={not no_compile}"
                )
            self.stdout.write(self.style.SUCCESS("Dry run complete."))
            return

        total_rules_added = 0
        total_policies_created = 0
        for org in qs:
            summary = seed_builtin_packs(org, reset_rules=reset)
            total_rules_added += summary["rules_added"]
            total_policies_created += summary["policies_created"]
            compile_result = None
            if not no_compile:
                compile_result = compile_organization_policies(org)
            self.stdout.write(
                f"  org={org.id} ({org.slug}): "
                f"{len(summary['families'])} family package(s), "
                f"+{summary['policies_created']} created, +{summary['rules_added']} new rule(s) "
                f"(all enabled=False)"
                + (
                    f" compiled_rules={compile_result.get('rule_count')}"
                    if compile_result
                    else ""
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded built-in packs for {qs.count()} org(s); "
                f"{total_policies_created} new policy(ies), "
                f"{total_rules_added} new rule(s) added — all DEFAULT-OFF."
            )
        )
        if not no_compile:
            self.stdout.write(
                "Compiled policies pushed to Redis (POLICY_SYNC); "
                "default-OFF packages contribute no rules until enabled."
            )
