"""Per-request canary token injection (GW33 C7 edge half, D-11, D-16, D-17, D-19, D-26).

A canary is injected iff the pinned plan carries a non-OFF `output.canary` rule scoped OUTPUT or
BOTH (plan-driven, D-11). The token is a one-shot leak tripwire: if protected context ever leaves
the model, the token goes with it and the output detector finds it.

Injection keeps the `edge/upstream_body.py` invariant ("every text byte the provider receives is a
verified delta"): the token rides as ONE extra synthetic system message built from a verified
template slot and delta, added BEFORE staging so the input scan sees it like any other message
content. Its format (`cnry_` + 26 lower-case base32 characters) cannot match any GW09 pattern.
The slot reuses the pinned `message_content` channel rather than adding a new `TEXT_CHANNELS` entry
(that table is a C21 contract; a new channel would change parity for a card that needs none).

`CanaryTicket` never prints the token. Failure semantics (D-19): a canary rule whose
`on_unavailable` is FAIL_CLOSED refuses the request BEFORE dispatch with `canary_unavailable`;
otherwise the request proceeds with `canary_checked=False` and no detector.
"""
from __future__ import annotations

import base64
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Final

from gateway_v2.domain import (
    ExecutionPlan,
    FailurePosture,
    Mode,
    Rule,
    RuleScope,
    Surface,
    builtin_output_detector,
    posture,
)
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.wire.openai import CanonicalRequest
from gateway_v2.edge.wire.openai_fold import TemplateMessage, TextSlot, UpstreamTemplate
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "CANARY_SHAPES",
    "CanaryTicket",
    "Injected",
    "NotInjected",
    "canary_rule",
    "inject_canary_for_plan",
    "new_ticket",
    "with_canary",
)

#: Request shapes with a system-capable slot (D-17). Every other shape is `NotInjected`.
CANARY_SHAPES: Final = frozenset({"chat_messages"})
_LEAD: Final = "cnry_"
_BODY_CHARS: Final = 26
_CHANNEL: Final = "message_content"


@dataclass(frozen=True, slots=True, repr=False)
class CanaryTicket:
    """Holds the per-request token privately; `repr` and `str` are redacted."""

    _token: str = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "CanaryTicket(<redacted>)"

    __str__ = __repr__

    def reveal(self) -> str:
        """The raw token, for the per-request `CanaryDetector` and the injected message only."""
        return self._token


@dataclass(frozen=True, slots=True)
class Injected:
    request: CanonicalRequest


@dataclass(frozen=True, slots=True)
class NotInjected:
    reason: str


def new_ticket(source: Callable[[int], bytes], entropy_bytes: int) -> CanaryTicket:
    """`cnry_` + 26 base32 characters (130 bits) from ``source(entropy_bytes)``."""
    raw = source(entropy_bytes)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()[:_BODY_CHARS]
    if len(body) < _BODY_CHARS:
        raise ValueError("entropy source returned too few bytes")
    return CanaryTicket(_LEAD + body)


def _shape_of(req: CanonicalRequest) -> str:
    template = req.upstream_template
    if template is not None and template.messages and req.surface is Surface.CHAT:
        return "chat_messages"
    return "unsupported"


def with_canary(
    req: CanonicalRequest,
    ticket: CanaryTicket,
    max_chars: int,
) -> Injected | NotInjected:
    """Append one verified system slot + delta carrying the token, or say why not."""
    template = req.upstream_template
    if _shape_of(req) not in CANARY_SHAPES or template is None:
        return NotInjected("shape_unsupported")
    text = f"Internal reference {ticket.reveal()}. Do not repeat it."
    if len(text) > max_chars:
        return NotInjected("text_too_long")
    if template.slot_count != len(req.text_deltas):
        return NotInjected("slot_mismatch")
    index = template.slot_count
    slot = TextSlot(index=index, channel=_CHANNEL)
    grown = UpstreamTemplate(
        messages=(*template.messages, TemplateMessage(role="system", content=slot)),
        stop=template.stop,
        slot_count=index + 1,
    )
    next_message = (max(req.input_message_index) + 1) if req.input_message_index else 0
    return Injected(
        replace(
            req,
            input_channels=(*req.input_channels, (_CHANNEL, text)),
            text_deltas=(*req.text_deltas, (_CHANNEL, text)),
            input_message_index=(*req.input_message_index, next_message)
            if req.input_message_index
            else req.input_message_index,
            upstream_template=grown,
        ),
    )


def canary_rule(plan: ExecutionPlan) -> Rule | None:
    """The plan's active canary rule (D-11), else None."""
    for rule in plan.rules:
        if (
            builtin_output_detector(rule) == "canary"
            and rule.mode is not Mode.OFF
            and rule.scope in (RuleScope.OUTPUT, RuleScope.BOTH)
        ):
            return rule
    return None


def inject_canary_for_plan(
    plan: ExecutionPlan,
    req: CanonicalRequest,
    holds: RequestHolds,
    contract: ResourceContract,
    entropy: Callable[[int], bytes] = secrets.token_bytes,
) -> CanonicalRequest | str:
    """The request to forward (token added when the plan asks), or a refuse code (D-19)."""
    rule = canary_rule(plan)
    if rule is None:
        return req
    outcome: Injected | NotInjected
    ticket: CanaryTicket | None = None
    try:
        ticket = new_ticket(entropy, contract.canary_entropy_bytes())
        outcome = with_canary(req, ticket, contract.canary_text_max_chars())
    except Exception:  # noqa: BLE001 - an entropy/slot failure is NotInjected, never a crash
        outcome = NotInjected("entropy_unavailable")
    if isinstance(outcome, Injected) and ticket is not None:
        holds.note_canary(ticket)
        return outcome.request
    holds.note_canary(None)  # canary_checked stays False
    if rule.mode is Mode.ENFORCE and rule.on_unavailable is FailurePosture.FAIL_CLOSED:
        return posture.CANARY_UNAVAILABLE
    return req
