"""Strip legacy / disconnected model names from FirewallConfig allowlists."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from auth.models import Organization
from core.firewall_model_governance import sanitize_allowlist_for_org
from core.models import FirewallConfig


class Command(BaseCommand):
    help = (
        "Intersect FirewallConfig.allowed_models with org-connected LLMModelConfig rows; "
        "fix default_model when stale. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            default="",
            help="Limit to a single organization slug.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report changes without writing to the database.",
        )

    def handle(self, *args, **options):
        slug = (options.get("org") or "").strip()
        dry_run = bool(options.get("dry_run"))

        qs = FirewallConfig.objects.select_related("organization").filter(
            organization__isnull=False
        )
        if slug:
            qs = qs.filter(organization__slug=slug)

        updated = 0
        scanned = 0
        for row in qs.order_by("organization__slug"):
            scanned += 1
            org = row.organization
            new_allowed, new_default, changed = sanitize_allowlist_for_org(
                org,
                allowed_models=row.allowed_models,
                default_model=row.default_model,
            )
            if not changed:
                continue

            updated += 1
            self.stdout.write(
                f"{org.slug}: allowed_models {row.allowed_models!r} -> {new_allowed!r}; "
                f"default_model {row.default_model!r} -> {new_default!r}"
            )
            if dry_run:
                continue

            row.allowed_models = new_allowed
            row.default_model = new_default
            row.save(update_fields=["allowed_models", "default_model", "updated_at"])

        suffix = " (dry-run)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Done{suffix}. Scanned {scanned} config(s), would update {updated}."
            )
        )
