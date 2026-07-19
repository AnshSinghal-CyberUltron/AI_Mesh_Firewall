"""Assign differentiated routing metadata to org LLM models and sync Redis."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from auth.models import Organization
from core.models import LLMModelConfig
from core.routing_catalog import differentiate_model_defaults
from core.signals import _sync_all_llm_models

_UPDATE_FIELDS = (
    "cost_per_1k_input_tokens",
    "cost_per_1k_output_tokens",
    "latency_sla_ms",
    "risk_score",
    "routing_priority",
    "data_sensitivity_level",
)


class Command(BaseCommand):
    help = (
        "Differentiate LLMModelConfig routing metadata (cost/latency/risk/"
        "priority/sensitivity) so auto-routing weight knobs change winners, "
        "then push llm:model_configs to Redis."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-slug",
            default="zeroshield",
            help="Organization slug (default: zeroshield). Use 'all' for every org.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print planned updates without writing.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Also update inactive models (default: active only).",
        )

    def handle(self, *args, **options):
        slug = (options.get("org_slug") or "zeroshield").strip().lower()
        dry = bool(options.get("dry_run"))
        include_inactive = bool(options.get("include_inactive"))

        if slug == "all":
            orgs = list(Organization.objects.all())
        else:
            org = Organization.objects.filter(slug=slug).first()
            if org is None:
                self.stderr.write(self.style.ERROR(f"Organization not found: {slug}"))
                return
            orgs = [org]

        updated = 0
        for org in orgs:
            qs = LLMModelConfig.objects.filter(organization=org)
            if not include_inactive:
                qs = qs.filter(is_active=True)
            for cfg in qs.iterator():
                # Skip pure platform rows that are not LiteLLM inference targets
                # only when provider=internal AND name is zeroshield-guard style —
                # still assign safe_sensitive so catalog stays honest.
                fields = differentiate_model_defaults(cfg.model_name)
                profile = fields.pop("_profile", "balanced")
                changes = {k: fields[k] for k in _UPDATE_FIELDS}
                if dry:
                    self.stdout.write(
                        f"[dry-run] {org.slug}/{cfg.model_name} → {profile} {changes}"
                    )
                    updated += 1
                    continue
                for key, val in changes.items():
                    setattr(cfg, key, val)
                cfg.save(update_fields=list(changes.keys()) + ["updated_at"])
                updated += 1
                self.stdout.write(
                    f"Updated {org.slug}/{cfg.model_name} profile={profile} "
                    f"sens={changes['data_sensitivity_level']} "
                    f"cost_in={changes['cost_per_1k_input_tokens']} "
                    f"lat={changes['latency_sla_ms']} "
                    f"risk={changes['risk_score']} "
                    f"prio={changes['routing_priority']}"
                )

        if dry:
            self.stdout.write(self.style.WARNING(f"Dry-run: {updated} model(s) would update."))
            return

        _sync_all_llm_models(None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Differentiated {updated} model(s); Redis llm:model_configs synced."
            )
        )
