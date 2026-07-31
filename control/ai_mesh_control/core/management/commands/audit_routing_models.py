"""Audit active LLMModelConfig rows for non-routable inference targets."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from auth.models import Organization
from core.models import LLMModelConfig, is_reserved_inference_model_name


class Command(BaseCommand):
    help = (
        "Report active org inference models that cannot be served by the gateway router "
        "(reserved Bedrock foundation ids, undecryptable keys). Use --fix to deactivate."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Deactivate models flagged as non-routable.",
        )
        parser.add_argument(
            "--org",
            default="",
            help="Limit audit to a single organization slug.",
        )

    def handle(self, *args, **options):
        fix = bool(options.get("fix"))
        org_slug = str(options.get("org") or "").strip()
        orgs = Organization.objects.all()
        if org_slug:
            orgs = orgs.filter(slug=org_slug)

        total_flagged = 0
        for org in orgs:
            qs = LLMModelConfig.queryset_user_managed(
                LLMModelConfig.objects.filter(is_active=True, organization=org)
            )
            flagged: list[tuple[LLMModelConfig, str]] = []
            for model in qs:
                reasons: list[str] = []
                if is_reserved_inference_model_name(model.model_name, model.model_id):
                    reasons.append("reserved_bedrock_foundation_id")
                if model.encrypted_api_key and not model.has_usable_api_key():
                    reasons.append("undecryptable_api_key")
                if reasons:
                    flagged.append((model, ",".join(reasons)))

            if not flagged:
                continue

            self.stdout.write(self.style.WARNING(f"org={org.slug} flagged={len(flagged)}"))
            for model, reason in flagged:
                total_flagged += 1
                self.stdout.write(
                    f"  - id={model.id} name={model.model_name!r} provider={model.provider} reason={reason}"
                )
                if fix:
                    model.is_active = False
                    model.save(update_fields=["is_active", "updated_at"])
                    self.stdout.write(self.style.SUCCESS(f"    deactivated {model.model_name!r}"))

        if total_flagged == 0:
            self.stdout.write(self.style.SUCCESS("No non-routable active inference models found."))
        elif fix:
            self.stdout.write(self.style.SUCCESS(f"Deactivated {total_flagged} model(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {total_flagged} non-routable active model(s). Re-run with --fix to deactivate."
                )
            )
