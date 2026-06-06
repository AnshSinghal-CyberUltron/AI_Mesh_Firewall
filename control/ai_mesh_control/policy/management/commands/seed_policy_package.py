"""
Seed the organization policy package (50+ policies across pipeline / rag / mcp /
vector, each a category with multiple rules) for one or more organizations.

The command ships inside the control Docker image. After changing the catalog,
rebuild + recreate control (no source volume mounts):

    docker compose build control && docker compose up -d control

Local:
    docker compose exec control python manage.py seed_policy_package --org-slug zeroshield
    # or:  bash scripts/seed_policy_package.sh zeroshield

Production (EC2 / ECR — rebuild + push control first, then on the host):
    docker compose exec control python manage.py seed_policy_package --org-slug <slug>

Options:
    --org-id / --org-slug   Target one organization (required unless --all-orgs)
    --all-orgs              Seed every active organization
    --reset                 Delete and recreate every package rule from the catalog
    --no-compile            Skip Redis policy compilation after seeding
    --dry-run               Validate the catalog and show targets without DB writes
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from auth.models import Organization
from policy.policy_package.catalog import MIN_POLICY_COUNT, package_metadata
from policy.policy_package.seed import compile_organization_policies, seed_policy_package


class Command(BaseCommand):
    help = (
        f"Seed the organization policy package ({MIN_POLICY_COUNT}+ policies across "
        "pipeline/rag/mcp/vector, each with multiple rules) for one or more orgs."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group()
        target.add_argument("--org-id", type=int, default=None, help="Organization primary key.")
        target.add_argument("--org-slug", type=str, default=None, help="Organization slug (e.g. zeroshield).")
        target.add_argument("--all-orgs", action="store_true", help="Seed all active organizations.")
        parser.add_argument("--reset", action="store_true", help="Remove existing package rules and recreate from catalog.")
        parser.add_argument("--no-compile", action="store_true", help="Do not push compiled policies to Redis after seeding.")
        parser.add_argument("--dry-run", action="store_true", help="Validate catalog and show targets without DB writes.")

    def handle(self, *args, **options):
        org_id = options.get("org_id")
        org_slug = options.get("org_slug")
        all_orgs = options.get("all_orgs")
        reset = options.get("reset", False)
        no_compile = options.get("no_compile", False)
        dry_run = options.get("dry_run", False)

        meta = package_metadata()
        self.stdout.write(
            f"Policy package: {meta['package_id']} v{meta['package_version']} "
            f"({meta['policy_count']} policies, {meta['rule_count']} rules) "
            f"by domain: {meta['policies_by_domain']}"
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
            self.stdout.write(self.style.SUCCESS("Dry run complete (catalog validated)."))
            return

        for org in qs:
            summary = seed_policy_package(org, reset=reset)
            if not no_compile:
                compile_organization_policies(org)
            self.stdout.write(
                f"  org={org.id} ({org.slug}): "
                f"policies +{summary['policies_created']} new / "
                f"{summary['policies_existing']} existing, "
                f"rules +{summary['rules_added']}, "
                f"vector +{summary['vector_created']}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"Seeded policy package for {qs.count()} org(s).")
        )
        if not no_compile:
            self.stdout.write("Compiled policy + vector bundles pushed to Redis per organization.")
