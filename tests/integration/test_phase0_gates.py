"""Phase-0 release-gate integration tests.

Maps 1:1 to ``AI_Mesh_Firewall/runs/verification/PHASE0_DECISION_v3.md`` §2.

Markers (target individual gates with ``pytest -m g2_per_org`` etc.):
    g1_hmac           — HMAC failure & misconfig signalling
    g2_per_org        — per-org tier2_enabled tri-state resolution
    g3_breaker        — Bedrock Tier-2 circuit breaker FSM + strict mode 451
    cc1_index         — Postgres ev_org_evclass_ts composite index used
    cc2_resilience    — gateway survives Mongo sink down

Run (requires the docker-compose stack up with mongo, redis, postgres):

    GATEWAY_MONGO_TELEMETRY_ENABLED=true \
        pytest AI_Mesh_Firewall/tests/integration/test_phase0_gates.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import pytest

# pytest-asyncio is required for the async branches.
pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

POLL_DEADLINE_S = 15
POLL_INTERVAL_S = 0.25


def _poll_mongo_count(mongo_db, query: dict, baseline: int, deadline_s: float = POLL_DEADLINE_S) -> int:
    """Wait until ``count_documents(query) > baseline`` or fail.

    Replaces ``sleep N`` (PHASE0_DECISION_v3 Rigourist defect #1).
    """
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        current = mongo_db.enforcement_events.count_documents(query)
        if current > baseline:
            return current
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(
        f"no new Mongo doc within {deadline_s}s for query={query!r} (baseline={baseline})"
    )


def _tampered_bundle(version: int = 99) -> dict:
    """A bundle whose ``_sig`` cannot possibly verify against any key."""
    return {
        "version": version,
        "policies": [],
        "policy_count": 0,
        "_sig": "deadbeef" * 8,
        "_sig_alg": "HMAC-SHA256",
    }


def _signed_bundle(signing_key: str, version: int = 1) -> dict:
    """A bundle correctly HMAC-signed with ``signing_key``."""
    payload = {"version": version, "policies": [], "policy_count": 0}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(signing_key.encode(), canonical, hashlib.sha256).hexdigest()
    return {**payload, "_sig": sig, "_sig_alg": "HMAC-SHA256"}


# ===========================================================================
# G1 — HMAC failure observability + misconfig 503
# ===========================================================================

@pytest.mark.g1_hmac
@pytest.mark.asyncio
async def test_g1_hmac_failure_emits_event_on_refresh(redis_client, mongo_db, monkeypatch):
    """Tampered bundle on refresh → ``policy_hmac_failure`` (site=refresh)."""
    monkeypatch.setenv("GATEWAY_POLICY_SIGNING_REQUIRED", "true")
    monkeypatch.setenv("POLICY_SIGNING_KEY", "test-signing-key-g1-refresh")

    from ai_mesh_gateway.policy_sync import PolicySync, REDIS_KEY_COMPILED

    org_slug = "phase0-test-refresh"
    redis_key = f"{REDIS_KEY_COMPILED}:{org_slug}"
    redis_client.set(redis_key, json.dumps(_tampered_bundle()))

    query = {
        "org_slug": org_slug,
        "event_class": "policy_hmac_failure",
        "metadata.site": "refresh",
    }
    baseline = mongo_db.enforcement_events.count_documents(query)

    sync = PolicySync(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    import redis.asyncio as aioredis  # type: ignore

    client = aioredis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    try:
        await sync._refresh_cache(client, org_slug)
    finally:
        await client.aclose()

    # _refresh_cache schedules emit via asyncio.create_task; await the loop
    # so the background task gets a chance to flush.
    await asyncio.sleep(0.5)
    _poll_mongo_count(mongo_db, query, baseline)

    # Tampered bundle must NOT have been admitted to cache (fail-closed).
    assert org_slug not in sync._org_caches

    redis_client.delete(redis_key)


@pytest.mark.g1_hmac
@pytest.mark.asyncio
async def test_g1_hmac_failure_emits_event_on_initial_load(redis_client, mongo_db, monkeypatch):
    """Tampered bundle on cold start → ``policy_hmac_failure`` (site=initial_load)."""
    monkeypatch.setenv("GATEWAY_POLICY_SIGNING_REQUIRED", "true")
    monkeypatch.setenv("POLICY_SIGNING_KEY", "test-signing-key-g1-init")

    from ai_mesh_gateway.policy_sync import PolicySync, REDIS_KEY_COMPILED

    org_slug = "phase0-test-initial"
    redis_key = f"{REDIS_KEY_COMPILED}:{org_slug}"
    redis_client.set(redis_key, json.dumps(_tampered_bundle()))

    query = {
        "org_slug": org_slug,
        "event_class": "policy_hmac_failure",
        "metadata.site": "initial_load",
    }
    baseline = mongo_db.enforcement_events.count_documents(query)

    sync = PolicySync(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    await sync._load_initial_bundle()

    await asyncio.sleep(0.5)
    _poll_mongo_count(mongo_db, query, baseline)
    assert org_slug not in sync._org_caches

    redis_client.delete(redis_key)


@pytest.mark.g1_hmac
def test_g1_misconfig_health_returns_503(monkeypatch):
    """``signing_enforced and key is None`` → /health 503 + structured reason."""
    from ai_mesh_gateway import main as gw_main
    from ai_mesh_gateway import policy_signing

    monkeypatch.setattr(policy_signing, "signing_enforced", lambda: True)
    monkeypatch.setattr(policy_signing, "_get_signing_key", lambda: None)
    # The health handler imports these names directly into main's namespace:
    monkeypatch.setattr(gw_main, "signing_enforced", lambda: True, raising=False)
    monkeypatch.setattr(gw_main, "_get_signing_key", lambda: None, raising=False)

    response = asyncio.get_event_loop().run_until_complete(gw_main.health())
    assert response.status_code == 503, (
        f"expected 503 when POLICY_SIGNING_KEY missing under enforcement; got {response.status_code}"
    )
    body = json.loads(response.body.decode())
    assert body["status"] == "degraded"
    assert body["reason"] == "policy_signing_key_missing"


# ===========================================================================
# G2 — Per-org tier2_enabled tri-state resolution
# ===========================================================================

@pytest.mark.g2_per_org
@pytest.mark.parametrize(
    "env_tier2_enabled, org_override, expect_tier2_invoked",
    [
        # env on, no override → tier2 runs
        ("true", None, True),
        # env off, no override → tier2 skipped
        ("false", None, False),
        # env on, override=False → tier2 skipped (per-org disable wins)
        ("true", False, False),
        # env off, override=True → tier2 runs (per-org enable wins)
        ("false", True, True),
    ],
    ids=["env_on_no_override", "env_off_no_override", "override_disable", "override_enable"],
)
def test_g2_per_org_tier2_tri_state(monkeypatch, env_tier2_enabled, org_override, expect_tier2_invoked):
    """tri-state resolution: identity check ``is None``, not truthiness."""
    monkeypatch.setenv("ENABLE_TIER2", env_tier2_enabled)

    # Re-import after env change so the Scanner picks up the new ENABLE_TIER2.
    import importlib
    from ai_mesh_gateway import scanner as scanner_mod

    importlib.reload(scanner_mod)

    invoked = {"bedrock": False}

    def _fake_bedrock_scan(text, context=None):
        invoked["bedrock"] = True
        return {
            "meta": {"recommended_action": "allow", "raw_findings": []},
            "llm_guard": {"score": 0.0},
        }

    inst = scanner_mod.InputScanner()

    # Inject a fake Bedrock scanner so the override=True case still has a
    # scanner to dispatch to even when ENABLE_TIER2 was false at construction.
    class _FakeBedrock:
        model = "fake-model"

        def scan(self, text, context=None):
            return _fake_bedrock_scan(text, context=context)

    inst._bedrock_scanner = _FakeBedrock()

    benign_prompt = "What is the weather today?"

    async def _run():
        return await inst.scan_prompt_with_tier2(
            benign_prompt,
            org_slug="phase0-g2",
            org_tier2_override=org_override,
        )

    asyncio.get_event_loop().run_until_complete(_run())
    assert invoked["bedrock"] is expect_tier2_invoked, (
        f"env={env_tier2_enabled} override={org_override}: "
        f"expected bedrock_invoked={expect_tier2_invoked}, got {invoked['bedrock']}"
    )


# ===========================================================================
# G3 — Bedrock Tier-2 circuit breaker FSM
# ===========================================================================

@pytest.mark.g3_breaker
@pytest.mark.asyncio
async def test_g3_breaker_trips_open_then_half_open_then_closed(monkeypatch, mongo_db):
    """Failures > threshold → OPEN. Cooldown → HALF_OPEN. Success → CLOSED.

    Asserts all three state transitions emit ``tier2_breaker_state_change``
    Mongo events (Rigourist defect #5: HALF_OPEN must appear, not skipped).

    Must run under @pytest.mark.asyncio: the breaker's telemetry emit uses
    ``loop.create_task`` only when ``loop.is_running()``. A sync test has no
    running loop, so the emit is silently skipped — masking the test oracle.
    """
    monkeypatch.setenv("TIER2_BREAKER_MIN_CALLS", "4")
    monkeypatch.setenv("TIER2_BREAKER_FAILURE_THRESHOLD", "0.5")
    monkeypatch.setenv("TIER2_BREAKER_COOLDOWN_SECONDS", "1")  # determinism

    import importlib
    from ai_mesh_gateway import bedrock_tier2_breaker as br_mod

    importlib.reload(br_mod)
    breaker = br_mod.BedrockTier2Breaker()
    org, model = "phase0-g3-fsm", "anthropic.claude-3-haiku"

    base_open = mongo_db.enforcement_events.count_documents(
        {"event_class": "tier2_breaker_state_change", "org_slug": org,
         "metadata.to_state": "open"}
    )

    # 4 calls, all failures → 4/4 = 1.0 ≥ 0.5 → trips OPEN
    for _ in range(4):
        assert breaker.allow(org, model, strict=False) is True
        breaker.record_result(org, model, failure=True)
    assert breaker.state_of(org, model) == "open"

    # OPEN, not yet cooled, non-strict → False, no exception
    assert breaker.allow(org, model, strict=False) is False

    # Wait past cooldown, next allow() transitions to HALF_OPEN
    await asyncio.sleep(1.2)
    base_half = mongo_db.enforcement_events.count_documents(
        {"event_class": "tier2_breaker_state_change", "org_slug": org,
         "metadata.to_state": "half_open"}
    )
    assert breaker.allow(org, model, strict=False) is True
    assert breaker.state_of(org, model) == "half_open"

    # Probe succeeds → CLOSED
    base_closed = mongo_db.enforcement_events.count_documents(
        {"event_class": "tier2_breaker_state_change", "org_slug": org,
         "metadata.to_state": "closed"}
    )
    breaker.record_result(org, model, failure=False)
    assert breaker.state_of(org, model) == "closed"

    # Yield so the fire-and-forget create_task() coroutines run, then poll.
    await asyncio.sleep(0.5)
    for to_state, baseline in [("open", base_open), ("half_open", base_half), ("closed", base_closed)]:
        _poll_mongo_count(
            mongo_db,
            {"event_class": "tier2_breaker_state_change", "org_slug": org,
             "metadata.to_state": to_state},
            baseline,
        )


@pytest.mark.g3_breaker
def test_g3_breaker_strict_mode_raises_tier2_unavailable(monkeypatch):
    """OPEN + strict=True → ``Tier2UnavailableStrict`` with ``retry_after_seconds``.

    Maps to HTTP 451 surface (caller in main.py translates the exception).
    """
    monkeypatch.setenv("TIER2_BREAKER_MIN_CALLS", "2")
    monkeypatch.setenv("TIER2_BREAKER_FAILURE_THRESHOLD", "0.5")
    monkeypatch.setenv("TIER2_BREAKER_COOLDOWN_SECONDS", "30")

    import importlib
    from ai_mesh_gateway import bedrock_tier2_breaker as br_mod

    importlib.reload(br_mod)
    breaker = br_mod.BedrockTier2Breaker()
    org, model = "phase0-g3-strict", "anthropic.claude-3-haiku"

    # Trip the breaker.
    for _ in range(2):
        breaker.allow(org, model, strict=False)
        breaker.record_result(org, model, failure=True)
    assert breaker.state_of(org, model) == "open"

    with pytest.raises(br_mod.Tier2UnavailableStrict) as exc_info:
        breaker.allow(org, model, strict=True)

    err = exc_info.value
    assert err.org_slug == org
    assert err.model_id == model
    assert err.retry_after_seconds >= 1
    assert err.retry_after_seconds <= 30


# ===========================================================================
# CC-1 — Postgres composite index ev_org_evclass_ts_idx is used
# ===========================================================================

@pytest.mark.cc1_index
def test_cc1_event_class_index_is_used(pg_conn):
    """EXPLAIN ANALYZE must show Index Scan over the composite index,
    not a Seq Scan (Rigourist missing oracle #1)."""
    cur = pg_conn.cursor()

    # Sanity-check the column + index exist before asserting on the plan.
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'policy_enforcementevent' AND column_name = 'event_class'
        """
    )
    assert cur.fetchone() is not None, (
        "policy_enforcementevent.event_class column missing — "
        "run `python manage.py migrate policy 0022` before this test"
    )

    cur.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'policy_enforcementevent'
          AND indexname = 'ev_org_evclass_ts_idx'
        """
    )
    assert cur.fetchone() is not None, (
        "ev_org_evclass_ts_idx missing — migration 0022 was not applied"
    )

    cur.execute(
        """
        EXPLAIN (FORMAT JSON, ANALYZE FALSE)
        SELECT id FROM policy_enforcementevent
        WHERE event_class = %s AND organization_id = %s
        ORDER BY created_at DESC
        LIMIT 50
        """,
        ("policy_hmac_failure", 1),
    )
    plan_text = json.dumps(cur.fetchone()[0])
    assert "Seq Scan" not in plan_text, (
        f"planner chose Seq Scan over policy_enforcementevent (composite index unused):\n{plan_text}"
    )
    assert "ev_org_evclass_ts_idx" in plan_text, (
        f"expected ev_org_evclass_ts_idx in plan, got:\n{plan_text}"
    )


# ===========================================================================
# CC-2 — Gateway survives Mongo sink down
# ===========================================================================

@pytest.mark.cc2_resilience
@pytest.mark.asyncio
async def test_cc2_sink_failure_does_not_crash_gateway(monkeypatch, caplog):
    """Sink raising ``ServerSelectionTimeoutError`` must not propagate.

    Triage Agent C: simulate the *real* exception path, not a None return.
    Property under test: ``emit_operational_event`` is best-effort + logs
    the failure at exception level; the gateway hot path is unaffected.
    """
    from pymongo.errors import ServerSelectionTimeoutError  # type: ignore

    from ai_mesh_gateway import telemetry_ops

    async def _broken_sink(_event):
        raise ServerSelectionTimeoutError("simulated: no mongo servers available")

    monkeypatch.setattr(telemetry_ops, "record_enforcement_event", _broken_sink)

    caplog.set_level(logging.ERROR, logger="gateway.telemetry_ops")

    # Must complete without raising — this is the actual property under test.
    await telemetry_ops.emit_operational_event(
        telemetry_ops.EVENT_CLASS_POLICY_HMAC_MISCONFIG,
        org_slug="phase0-cc2",
        severity="critical",
        metadata={"site": "test"},
    )

    # And the failure must have been logged so on-call can see it.
    sink_errors = [
        r for r in caplog.records
        if r.name == "gateway.telemetry_ops" and r.levelno >= logging.ERROR
    ]
    assert sink_errors, "expected an ERROR/EXCEPTION log when sink raises"
