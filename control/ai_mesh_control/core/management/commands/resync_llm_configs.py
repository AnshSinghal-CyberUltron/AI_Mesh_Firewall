"""Resync all active LLM model configs (including fallback_chains) to Redis."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.signals import _sync_all_llm_models


class Command(BaseCommand):
    help = "Push llm:model_configs:{org_slug} for every org with active LLMModelConfig rows."

    def handle(self, *args, **options):
        _sync_all_llm_models(None)
        self.stdout.write(self.style.SUCCESS("LLM model configs synced to Redis."))
