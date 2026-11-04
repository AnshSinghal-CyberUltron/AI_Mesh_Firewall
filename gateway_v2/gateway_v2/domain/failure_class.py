"""ONE classification of a failure code: operational (``error``) or policy (``block``).

GW35. The console shows an upstream or internal fault as ``error`` and a policy refusal as
``block``. Before this module `egress` and `edge` each guessed, and every stream cut painted
``output_guardrail: block``. This table sits in `domain` because `egress` cannot import `edge`
and both need the same spelling.

Unknown codes are POLICY: the existing fail-closed direction. Codes only; no status, no message.
"""

from __future__ import annotations

from gateway_v2.domain import posture

__all__ = (
    "INPUT_SIDE",
    "NO_ACTION",
    "OPERATIONAL_CODES",
    "PREDISPATCH_OPERATIONAL",
    "is_input_side",
    "is_no_action",
    "is_operational_failure",
    "terminal_output_action",
)

#: Kill-switch terminations publish no action and no failure (pre-change console unchanged).
#: ``killed`` is the literal the pipelines use; ``stream_killed`` is how the edge renders it.
NO_ACTION = frozenset({"killed", posture.STREAM_KILLED})

#: Input-side policy refusals returned after the input decision was recorded.
INPUT_SIDE = frozenset({"input_blocked", "input_unmaskable"})

#: Internal or staging faults before the provider opened. ``plan_unavailable`` is here because an
#: in-stream plan fault kills the stream before ``mark_dispatched``. The vector/RAG codes cannot
#: occur on the chat path; they sit in the shared table and tests drive them through the builder.
PREDISPATCH_OPERATIONAL = frozenset(
    {
        "lifecycle_incomplete",
        posture.CREDENTIAL_UNAVAILABLE,
        posture.PROVIDER_UNREACHABLE,
        posture.STORE_NOT_CONFIGURED,
        posture.PLAN_UNAVAILABLE,
    },
)

_UPSTREAM = frozenset(
    {
        "upstream_auth_error",
        "upstream_auth_unsupported",
        "upstream_error",
        "upstream_invalid_request",
        "upstream_not_found",
        "upstream_rate_limited",
        "upstream_request_unbuildable",
        "upstream_timeout",
        "upstream_unavailable",
    },
)

_STREAM_OPERATIONAL = frozenset(
    {
        posture.STREAM_MALFORMED_UPSTREAM,
        posture.STREAM_INTER_CHUNK_TIMEOUT,
        posture.STREAM_IDLE_TIMEOUT,
        posture.STREAM_WRITE_TIMEOUT,
        posture.STREAM_MAX_DURATION,
        posture.STREAM_BUFFER_UNAVAILABLE,
    },
)

OPERATIONAL_CODES = _UPSTREAM | _STREAM_OPERATIONAL | PREDISPATCH_OPERATIONAL


def is_operational_failure(code: str) -> bool:
    """True when ``code`` is a fault (shown as ``error``); False for policy and unknown codes."""
    return code in OPERATIONAL_CODES


def is_no_action(code: str) -> bool:
    """True for the kill-switch spellings, which publish nothing."""
    return code in NO_ACTION


def is_input_side(code: str) -> bool:
    """True for an input-side policy refusal."""
    return code in INPUT_SIDE


def terminal_output_action(error: str) -> str:
    """The stats-only output action for a pipeline cut: ``error`` for a fault, else ``block``."""
    return "error" if is_operational_failure(error) else "block"
