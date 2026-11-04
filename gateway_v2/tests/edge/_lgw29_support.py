"""Shared GW29 test support: pass-through incident seams and a proceed ``LifecycleOutcome``.

``dev_gate`` / ``dev_recheck`` are ``dataclasses.field`` default factories for the hand-rolled
duck-typed ``ChatRoute`` stand-ins that predate GW29 (they only need the seams to proceed).
:func:`proceed_outcome` builds the proceed outcome that ``_stream_response`` and
``collect_and_render`` now require, with a binding so the in-flight source and the recheck know
the org and the routed model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from gateway_v2.domain.incident import GateVerdict, StreamBinding
from gateway_v2.edge.chat_lifecycle import LifecycleOutcome
from gateway_v2.edge.incident_passthrough import PassThroughIncidentGate

__all__ = ("dev_gate", "dev_recheck", "proceed_outcome")


def dev_gate() -> PassThroughIncidentGate:
    return PassThroughIncidentGate()


def dev_recheck() -> Callable[..., GateVerdict]:
    return PassThroughIncidentGate().recheck


def proceed_outcome(
    *,
    org_id: str = "org-test",
    model: str = "gpt-test",
    plan: Any = None,
    destination: str | None = "dest",
    body: Mapping[str, object] | None = None,
) -> LifecycleOutcome:
    """A proceed outcome bound to ``org_id`` / ``model`` (key hash and feed position are inert)."""
    return LifecycleOutcome(
        plan=plan,
        destination=destination,
        body={} if body is None else body,
        refuse_code=None,
        routed_model=model,
        binding=StreamBinding(org_id=org_id, model=model, key_hash="k", key_feed_seq=0),
    )
