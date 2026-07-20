"""
Seed the CISO 100-rule policy package for organization(s).

Local:
    docker compose exec control python manage.py seed_ciso_policy_package --org-slug zeroshield

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
from policy.ciso_policy_catalog import MIN_RULE_COUNT, build_rule_dicts, package_metadata
from policy.ciso_seed import compile_organization_policies, seed_ciso_policy_package


class Command(BaseCommand):
    help = (
        f"Seed CISO 100 enterprise guardrail package ({MIN_RULE_COUNT} rules) "
        "for one or more organizations."
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

        catalog_len = len(build_rule_dicts())
        meta = package_metadata()
        self.stdout.write(
            f"CISO package: {meta['package_id']} v{meta['package_version']} "
            f"({catalog_len} rules in catalog)"
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
                    f"reset={reset} compile={not no_compile}"
                )
            self.stdout.write(self.style.SUCCESS("Dry run complete."))
            return

        total_rules_added = 0
        for org in qs:
            policy, created, added = seed_ciso_policy_package(org, reset_rules=reset)
            total_rules_added += added
            compile_result = None
            if not no_compile:
                compile_result = compile_organization_policies(org)
            self.stdout.write(
                f"  org={org.id} ({org.slug}): policy={policy.code} "
                f"{'created' if created else 'exists'} "
                f"rules={policy.rules.count()} (+{added} new)"
                + (
                    f" compiled_rules={compile_result.get('rule_count')}"
                    if compile_result
                    else ""
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded CISO package for {qs.count()} org(s); "
                f"{total_rules_added} new rule(s) added."
            )
        )
        if not no_compile:
            self.stdout.write("Compiled policies pushed to Redis (POLICY_SYNC).")
