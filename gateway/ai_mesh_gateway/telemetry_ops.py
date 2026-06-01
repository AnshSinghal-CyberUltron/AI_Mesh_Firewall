"""
Operational telemetry chokepoint for gateway-emitted events.

Phase 0 D-CC-2-v3: every operational event (HMAC failure, breaker state
change, misconfig warning, etc.) flows through ``emit_operational_event``.
The helper exists so that:

  * the event-schema contract is enforced in one place (event_class,
    org_slug, ts, metadata).
  * the hot path NEVER awaits the sink. Callers MUST wrap the call in
    ``asyncio.create_task(emit_operational_event(...))`` and discard the
    task handle. Awaiting it inside ``sync_pre_llm`` would put a Mongo
    round-trip on every gated request.
  * future G13 work (event-stream encryption) has a single chokepoint
    to wrap.

The helper itself ``await``s ``record_enforcement_event`` so background
exceptions are at least logged via the Mongo sink's own ``LOG.warning``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from .telemetry_mongo import record_enforcement_event
except ImportError:  # pragma: no cover - script-mode fallback
    from telemetry_mongo import record_enforcement_event  # type: ignore

LOG = logging.getLogger("gateway.telemetry_ops")

# Recognised operational event_class values. Kept in sync with the Postgres
# choices in control/ai_mesh_control/policy/models.py:EnforcementEvent.
EVENT_CLASS_ENFORCEMENT = "enforcement"
EVENT_CLASS_POLICY_HMAC_FAILURE = "policy_hmac_failure"
EVENT_CLASS_POLICY_HMAC_MISCONFIG = "policy_hmac_misconfig"
EVENT_CLASS_TIER2_BREAKER_STATE_CHANGE = "tier2_breaker_state_change"
EVENT_CLASS_TIER2_DEGRADED_PASS = "tier2_degraded_pass"
# Phase 1 D_G5 — Query Audit Telemetry (rewrite/block/downgrade)
EVENT_CLASS_QUERY_REWRITTEN = "query_rewritten"
EVENT_CLASS_QUERY_BLOCKED = "query_blocked"
EVENT_CLASS_QUERY_DOWNGRADED = "query_downgraded"
# Phase 1 D_G10 — Semantic Hallucination Grounding
EVENT_CLASS_HALLUCINATION_DETECTED = "hallucination_detected"

KNOWN_EVENT_CLASSES = frozenset(
    {
        EVENT_CLASS_ENFORCEMENT,
        EVENT_CLASS_POLICY_HMAC_FAILURE,
        EVENT_CLASS_POLICY_HMAC_MISCONFIG,
        EVENT_CLASS_TIER2_BREAKER_STATE_CHANGE,
        EVENT_CLASS_TIER2_DEGRADED_PASS,
        EVENT_CLASS_QUERY_REWRITTEN,
        EVENT_CLASS_QUERY_BLOCKED,
        EVENT_CLASS_QUERY_DOWNGRADED,
        EVENT_CLASS_HALLUCINATION_DETECTED,
    }
)


async def emit_operational_event(
    event_class: str,
    *,
    org_slug: str | None = None,
    severity: str = "warning",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort emit of an operational event to the Mongo sink.

    NEVER raises. Callers MUST schedule with ``asyncio.create_task(...)`` —
    never ``await`` directly on the request hot path.

    Parameters
    ----------
    event_class : str
        One of ``KNOWN_EVENT_CLASSES``. Unknown values are accepted but
        logged once at INFO so the catalogue can be extended deliberately.
    org_slug : str | None
        Organization slug for tenant correlation. ``None`` for startup /
        gateway-global events.
    severity : str
        ``info`` | ``warning`` | ``critical``. Operational alerts that
        require pager attention should use ``critical``.
    metadata : dict | None
        Arbitrary structured context. Avoid PII; this collection is
        operator-readable. Common keys: ``site`` (e.g. ``initial_load`` |
        ``refresh``), ``model_id``, ``from_state`` / ``to_state``,
        ``breaker_kind``, ``reason``.
    """
    if event_class not in KNOWN_EVENT_CLASSES:
        LOG.info("emit_operational_event: unknown event_class=%r", event_class)
    event = {
        "event_class": event_class,
        "org_slug": org_slug,
        "severity": severity,
        "ts": time.time(),
        "metadata": dict(metadata or {}),
    }
    try:
        await record_enforcement_event(event)
    except Exception:  # noqa: BLE001 - helper must never raise
        LOG.exception("emit_operational_event: sink raised; swallowing")


# --------------------------------------------------------------------------- #
# Phase 1 D_G5 — Query Audit Telemetry helper                                 #
# --------------------------------------------------------------------------- #
_QUERY_AUDIT_DECISION_TO_EVENT_CLASS = {
    "rewrite": EVENT_CLASS_QUERY_REWRITTEN,
    "block": EVENT_CLASS_QUERY_BLOCKED,
    "downgrade": EVENT_CLASS_QUERY_DOWNGRADED,
}
_QUERY_AUDIT_VALID_DECISIONS = frozenset(
    {"allow", "rewrite", "block", "downgrade"}
)


def _log_task_exception(task: Any) -> None:
    """``add_done_callback`` handler that surfaces background-task errors.

    Use as::

        t = asyncio.create_task(emit_query_audit_event(...))
        t.add_done_callback(_log_task_exception)

    Prevents the "Task exception was never retrieved" warnings without
    propagating onto the request hot path.
    """
    try:
        exc = task.exception()
    except Exception:  # noqa: BLE001 - cancelled/loop-closed; nothing useful
        return
    if exc is not None:
        LOG.warning("background telemetry task failed: %r", exc)


async def emit_query_audit_event(
    *,
    org_slug: str,
    decision: str,
    rule_code: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Emit a D_G5 query-audit event (rewrite / block / downgrade).

    ``decision == "allow"`` is an explicit no-op so the hot path can call
    this helper unconditionally without branching.

    NEVER raises. Schedule with ``asyncio.create_task`` and attach
    ``_log_task_exception`` as the done callback.

    Parameters
    ----------
    org_slug : str
        Tenant slug (required for audit attribution).
    decision : str
        One of ``allow`` | ``rewrite`` | ``block`` | ``downgrade``.
    rule_code : str
        Stable identifier of the policy rule / detector that fired
        (e.g. ``"pii_email"``, ``"secret_aws_key"``, ``"toxicity_high"``).
    metadata : dict | None
        Structured context. Recommended keys: ``input_bytes`` (use
        ``len(text.encode("utf-8"))``), ``model_id``, ``score``, ``rag_collection``.
    """
    if decision == "allow":
        return
    if decision not in _QUERY_AUDIT_VALID_DECISIONS:
        LOG.info("emit_query_audit_event: unknown decision=%r", decision)
        return
    event_class = _QUERY_AUDIT_DECISION_TO_EVENT_CLASS[decision]
    payload = {"rule_code": rule_code, **(metadata or {})}
    await emit_operational_event(
        event_class,
        org_slug=org_slug,
        severity="warning" if decision != "downgrade" else "info",
        metadata=payload,
    )
