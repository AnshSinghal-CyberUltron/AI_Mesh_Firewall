"""Seed a credentialed org inference model for P0.0 (run via manage.py shell).

The loadtest stub never dials OpenAI; the gateway still 422s
``no_provider_configured`` unless Redis ``llm:model_configs:{org}`` has a
non-internal row with a usable key. Guard models (provider=internal) are
excluded from that Redis payload by design.
"""
from auth.models import Organization
from core.models import LLMModelConfig

ORG_SLUG = "aimfp0"
MODEL_NAME = "gpt-4o-mini"
# Not a real provider key. Stub intercepts before LiteLLM.
DUMMY_KEY = "sk-p0-harness-not-a-real-openai-key"

org = Organization.objects.get(slug=ORG_SLUG)
obj, _created = LLMModelConfig.objects.update_or_create(
    organization=org,
    model_name=MODEL_NAME,
    defaults={
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "is_active": True,
        "routing_priority": 50,
        "data_sensitivity_level": "public",
    },
)
obj.set_api_key(DUMMY_KEY)
obj.is_active = True
obj.save()
print(
    "seeded_inference",
    obj.model_name,
    "usable",
    obj.has_usable_api_key(),
    "org",
    org.slug,
)
