"""Bootstrap the perf stack's tenant: org, owner, API key, model, policies.

Task 1.3 prerequisite (auth) and task 1.8a prerequisite (policy seeding).

Run inside the control container:

    docker exec aimeshperf-control-1 sh -c \
      'cd /app/control && ./.venv/bin/python manage.py shell \
       < /app/scripts/perf/e2e/bootstrap_org.py'

Prints a single line `PERF_API_KEY=<plaintext>` — `generate_key` returns the
plaintext exactly once and never stores it, so it must be captured here.

Why policies are seeded: with zero compiled bundles `policy_engine.evaluate`
receives an empty rule list, Tier-1 costs ~0, and the gateway allows everything
while `/health` still reports ok. A baseline taken in that state measures a
pipeline that does no detection work (evidence finding F2).
"""
from __future__ import annotations

import sys
import traceback

ORG_SLUG = "perf-harness"
KEY_NAME = "perf-e2e-harness-key"
PROJECT_ID = "perf-e2e"


def out(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def main() -> int:
    from django.contrib.auth import get_user_model

    # ── Organization ────────────────────────────────────────────────────────
    from auth.models import Organization

    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        org = Organization.objects.filter(slug="default").first() or Organization.objects.first()
    if org is None:
        org = Organization.objects.create(name="Perf Harness", slug=ORG_SLUG)
        out(f"created org {org.slug}")
    else:
        out(f"using org {org.slug} (id={org.id})")

    # ── Owner ───────────────────────────────────────────────────────────────
    User = get_user_model()
    owner = User.objects.filter(is_superuser=True).first()
    if owner is None:
        owner = User.objects.create_superuser(
            username="perfharness", email="perf@example.invalid", password="perf-not-a-secret",
        )
        out("created superuser perfharness")
    for attr in ("organization", "org"):
        if hasattr(owner, attr) and getattr(owner, attr) is None:
            setattr(owner, attr, org)
            owner.save(update_fields=[attr])
            out(f"attached owner.{attr} -> {org.slug}")
            break
    out(f"owner={owner}")

    # ── Gateway API key ─────────────────────────────────────────────────────
    from core.models import GatewayAPIKey

    GatewayAPIKey.objects.filter(name=KEY_NAME).delete()   # idempotent re-run
    key, plaintext = GatewayAPIKey.generate_key(
        name=KEY_NAME,
        owner=owner,
        project_id=PROJECT_ID,
        allowed_models=[],                 # [] = no per-key model restriction
        rate_limit_tokens_per_minute=10_000_000,   # never the bottleneck under load
    )
    out(f"key prefix={key.prefix} tpm={key.rate_limit_tokens_per_minute}")

    # ── A routable model for the org ────────────────────────────────────────
    try:
        from core.simulator_seed import ensure_default_llm_model
        ensure_default_llm_model(org)
        out("ensured default LLM model")
    except Exception as exc:  # noqa: BLE001
        out(f"WARN ensure_default_llm_model: {type(exc).__name__}: {exc}")

    # ── Policies (finding F2) ───────────────────────────────────────────────
    from django.core.management import call_command

    for cmd in ("seed_policy_package", "seed_pii_policy_package", "compile_policies"):
        try:
            call_command(cmd)
            out(f"ran {cmd}")
        except Exception as exc:  # noqa: BLE001
            out(f"WARN {cmd}: {type(exc).__name__}: {exc}")

    try:
        from policy.models import Policy
        out(f"policies now: {Policy.objects.count()}")
    except Exception:
        pass

    # Last line, machine-readable — the harness greps for this.
    print(f"PERF_API_KEY={plaintext}", flush=True)
    return 0


try:
    raise SystemExit(main())
except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    sys.exit(1)
