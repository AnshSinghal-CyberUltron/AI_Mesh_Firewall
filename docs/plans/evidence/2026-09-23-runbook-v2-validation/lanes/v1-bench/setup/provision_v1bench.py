"""v1-bench tenant provisioning. Run inside the control container:

    docker compose ... exec -T -e V1B_PROV_API_BASE=http://<synthprov>:<port>/v1 \
        -e V1B_MODEL_NAME=<name> control python /tmp/provision_v1bench.py

Derived from staging/t02/provision.py (BYOK custom provider) and
scripts/perf/e2e/bootstrap_org.py (policy seeding, rate-limit CEILING raise).
Writes the gateway API key plaintext to /tmp/v1bench.key (mode 0600) and prints
only its prefix. The upstream "API key" is a dummy: synthprov ignores auth.
"""
from __future__ import annotations

import json
import os
import stat

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import call_command

from auth.models import Organization
from core.models import FirewallConfig, GatewayAPIKey, LLMModelConfig

ORG_SLUG = "v1bench"
KEY_NAME = "v1bench-harness-key"
KEY_PATH = "/tmp/v1bench.key"
API_BASE = os.environ["V1B_PROV_API_BASE"].strip()
MODEL_NAME = os.environ.get("V1B_MODEL_NAME", "synth-1").strip()
BUILTIN_PACKS = os.environ.get("V1B_BUILTIN_PACKS", "off").strip().lower()  # on|off
OUTPUT_CRED_ACTION = os.environ.get("V1B_OUTPUT_CRED_ACTION", "redact").strip()


def out(msg):
    print(f"[provision] {msg}", flush=True)


def main():
    org, _ = Organization.objects.get_or_create(slug=ORG_SLUG, defaults={"name": "v1 bench", "is_active": True})
    if not org.is_active:
        org.is_active = True
        org.save(update_fields=["is_active"])
    out(f"org {org.slug} id={org.id}")

    User = get_user_model()
    owner = User.objects.filter(is_superuser=True).order_by("id").first()
    if owner is None:
        owner = User.objects.create_superuser(username="v1bench", email="v1bench@example.invalid",
                                              password="v1bench-not-a-secret")
        out("created superuser v1bench")

    # ── firewall posture: block enforcement, PII + content filtering + output guard ON,
    #    Tier-2/semantic OFF. Rate-limit CEILING raised (stage keeps its Redis work).
    cfg = FirewallConfig.load(organization=org)
    cfg.firewall_enabled = True
    cfg.enforcement_mode = "block"
    cfg.pii_detection_enabled = True
    cfg.content_filtering_enabled = True
    cfg.jailbreak_detection_enabled = True
    cfg.semantic_analysis_enabled = False
    cfg.tier2_enabled = False
    cfg.response_filtering_enabled = True
    cfg.output_pii_enabled = True
    cfg.output_pii_action = "redact"
    cfg.output_credential_enabled = True
    cfg.output_credential_action = OUTPUT_CRED_ACTION
    cfg.output_policy_enabled = True
    cfg.rate_limit_enabled = True
    cfg.requests_per_minute = 100_000_000
    cfg.burst_limit = 1_000_000
    cfg.audit_logging_enabled = True
    cfg.save()
    out("firewall config saved: " + json.dumps({k: getattr(cfg, k) for k in (
        "firewall_enabled", "enforcement_mode", "pii_detection_enabled", "content_filtering_enabled",
        "jailbreak_detection_enabled", "semantic_analysis_enabled", "tier2_enabled",
        "response_filtering_enabled", "output_pii_enabled", "output_pii_action",
        "output_credential_enabled", "output_credential_action", "output_policy_enabled",
        "rate_limit_enabled", "requests_per_minute", "burst_limit", "org_tpm_limit")}))

    # ── gateway API key (org lives on the KEY — bootstrap_org.py RETRACTION lesson)
    GatewayAPIKey.objects.filter(name=KEY_NAME).delete()
    key, plaintext = GatewayAPIKey.generate_key(
        name=KEY_NAME, owner=owner, project_id="v1bench", allowed_models=[],
        rate_limit_tokens_per_minute=100_000_000,
    )
    if key.organization_id != org.id:
        key.organization = org
        key.save(update_fields=["organization"])
    tmp = KEY_PATH + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(plaintext)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, KEY_PATH)
    out(f"key prefix={key.prefix} org={key.organization.slug} tpm={key.rate_limit_tokens_per_minute}")

    # ── BYOK custom OpenAI-compatible provider = synthprov (only active model of the org)
    LLMModelConfig.objects.filter(organization=org).exclude(model_name=MODEL_NAME).update(is_active=False)
    obj, created = LLMModelConfig.objects.update_or_create(
        organization=org, model_name=MODEL_NAME,
        defaults={"provider": "custom", "model_id": MODEL_NAME, "api_key_env_var": "",
                  "api_base": API_BASE, "is_active": True, "data_sensitivity_level": "restricted",
                  "latency_sla_ms": 30000, "routing_priority": 10, "risk_score": 0.0},
    )
    obj.set_api_key("sk-synthprov-dummy-not-a-secret")
    obj.save()
    out(f"model {obj.model_name} provider={obj.provider} api_base={obj.api_base} created={created}")
    call_command("ensure_model_states", org=ORG_SLUG)

    # ── Tier-1 deterministic detection: policy package + PII package (+ built-in packs)
    call_command("seed_policy_package", org_slug=ORG_SLUG, no_compile=True)
    call_command("seed_pii_policy_package", org_slug=ORG_SLUG, no_compile=True)
    call_command("seed_builtin_packs", org_slug=ORG_SLUG, no_compile=True)
    from policy.models import Policy
    builtin = Policy.objects.filter(organization=org, code__icontains="builtin")
    n = builtin.update(enabled=(BUILTIN_PACKS == "on"))
    out(f"builtin packs enabled={BUILTIN_PACKS == 'on'} ({n} policies)")
    # Org-scoped compile+push (policies:compiled:v1bench). The seeders' debounced
    # recompilation runs in a thread that dies with this process, and the global
    # compile_policies command writes policies:compiled:default only.
    from policy.policy_package.seed import compile_organization_policies
    compile_organization_policies(org)
    pol = Policy.objects.filter(organization=org)
    out("policies org=%d enabled=%d pipeline_enabled=%d" % (
        pol.count(), pol.filter(enabled=True).count(),
        pol.filter(enabled=True, policy_domain="pipeline").count()))


main()
