"""Create ModelState rows for every active LLMModelConfig (per org)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from auth.models import Organization
from core.model_state_bootstrap import ensure_model_states_for_org


class Command(BaseCommand):
    help = "Idempotently create ModelState rows for active LLM model configs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            default="",
            help="Limit to a single organization slug.",
        )

    def handle(self, *args, **options):
        slug = (options.get("org") or "").strip()
        qs = Organization.objects.all()
        if slug:
            qs = qs.filter(slug=slug)

        total_created = 0
        for org in qs.order_by("slug"):
            created, active = ensure_model_states_for_org(org)
            total_created += created
            self.stdout.write(f"{org.slug}: created={created} active_configs={active}")

        self.stdout.write(self.style.SUCCESS(f"Done. Created {total_created} ModelState row(s)."))
