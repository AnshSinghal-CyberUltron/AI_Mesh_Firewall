"""Chat §10.4 lifecycle value-codes + the ``LifecycleOutcome`` result shape.

Split out of ``edge/openai_chat.py`` so the chat-surface module stays under the
800-line module gate (the same one-directional split that kept ``edge/routes.py``
small via ``edge/chat_deps.py``): this sibling owns the DECLARED fail-closed
refuse-codes, the immutable :class:`LifecycleOutcome` result value, and its one
constructor :func:`_refused`. ``edge/openai_chat.py`` imports them back and
re-exports them, so every existing ``from gateway_v2.edge.openai_chat import
REFUSE_* / LifecycleOutcome`` keeps resolving unchanged.

The split is one-directional: ``openai_chat`` imports from ``chat_lifecycle``;
``chat_lifecycle`` imports nothing from ``openai_chat`` (no cycle). Everything here
is ``edge`` (the top layer), so it may import every layer below (``domain`` + the
sibling ``edge/error_frame_scan`` spelling). No capacity literal, no module-level
mutable: the refuse-codes are plain strings and :class:`LifecycleOutcome` is a
frozen, slotted value type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE
from gateway_v2.edge.error_frame_scan import SCAN_ERROR_CODE

__all__ = (
    "REFUSE_BUDGET_UNAVAILABLE",
    "REFUSE_IDENTITY_UNRESOLVED",
    "REFUSE_INPUT_BLOCKED",
    "REFUSE_INPUT_SCAN_FAILURE",
    "REFUSE_LIFECYCLE_INCOMPLETE",
    "REFUSE_OUTPUT_BLOCKED",
    "REFUSE_PLAN_UNAVAILABLE",
    "REFUSE_PRE_CHECK",
    "REFUSE_UNSUPPORTED_SURFACE",
    "LifecycleOutcome",
    "_refused",
)


# --------------------------------------------------------------------------- #
# Declared refuse-codes (fail-closed) — plain strings, not framework errors.
# --------------------------------------------------------------------------- #
#
# A phase that cannot forward sets ONE of these declared value-codes on the
# :class:`LifecycleOutcome`; the handler renders it through the single ``edge/errors``
# ``Error_Envelope`` (an unmapped code renders a declared generic error there). They
# are codes (the shared value-code discipline, like ``domain/posture.py``), NOT HTTP
# objects and NOT capacity-position literals. Each reuses the pre-existing shared
# spelling where one exists so the two sides of the pipeline never disagree (C37).

#: A Pre_Identity_Clock pre-check bound was exceeded (size / expansion / part count).
REFUSE_PRE_CHECK = "request_pre_check_exceeded"

#: Tenant identity unresolved, or its freshness stamp stale / unavailable (R4.2/R4.3).
#: Fail CLOSED — ``shared_state_unavailable`` (a 503, never a 401: the key may be valid).
REFUSE_IDENTITY_UNRESOLVED = "shared_state_unavailable"

#: The plan could not be resolved on the served snapshot (R2.4).
REFUSE_PLAN_UNAVAILABLE = "plan_unavailable"

#: An input-channel finding resolved to a BLOCK disposition (C22). ``stage_upstream``
#: mints no authorization for a BLOCK, so the request refuses BEFORE ``provider.open``
#: (R7.2 — zero provider calls). The shipped ``output_blocked``-family input spelling.
REFUSE_INPUT_BLOCKED = "input_blocked"

#: The budget lease is exhausted (R6.2). The shared ``posture.BUDGET_UNAVAILABLE``
#: spelling (a 503, narrower than ``shared_state_unavailable``: it refuses only quota).
REFUSE_BUDGET_UNAVAILABLE = BUDGET_UNAVAILABLE

#: An input-channel scan RAISED (R7, R8.4). Fail CLOSED — the request is withheld.
#: Reuses the shipped ``scan_failure`` spelling so an input-side and an output-side
#: scan failure render identically (C37); the non-streaming OUTPUT path reuses it too.
REFUSE_INPUT_SCAN_FAILURE = SCAN_ERROR_CODE

#: An OUTPUT-channel finding resolved to a BLOCK on the non-streaming path (R8.3/R9.3).
#: The whole response body is WITHHELD. Reuses the shipped ``output_blocked`` spelling.
REFUSE_OUTPUT_BLOCKED = "output_blocked"

#: The request reached the chat lifecycle on a path that maps to no chat ``Surface``
#: (R1.4, defensive) — unreachable through the real router; the parse phase fails CLOSED
#: here rather than guessing a surface. Rendered as the declared generic error.
REFUSE_UNSUPPORTED_SURFACE = "unsupported_surface"

#: Fail-closed default a parse that produced neither a request nor a code returns, so
#: the lifecycle refuses rather than forwarding an un-parsed request (never reached on
#: a filled phase).
REFUSE_LIFECYCLE_INCOMPLETE = "lifecycle_incomplete"


# --------------------------------------------------------------------------- #
# Value types (Data Models).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """The result of the pre-provider §10.4 lifecycle: proceed, or refuse (R2–R7).

    Exactly one of two shapes:

    * **Proceed** — ``refuse_code is None`` and the three provider inputs are set:
      ``plan`` (the resolved :class:`~gateway_v2.domain.plan.ExecutionPlan` driving
      output enforcement), ``destination`` (``DispatchRouter.select(plan)``), and
      ``body`` (the ``Transform.to_provider`` upstream request body). The handler
      opens the provider and runs the streaming / non-streaming path.
    * **Refuse** — ``refuse_code`` is a declared value-code and the three provider
      inputs are ``None``. A non-None ``refuse_code`` means *render + stop*: the
      handler renders the code through the single ``edge/errors`` ``Error_Envelope``
      and makes ZERO provider calls. Every phase can only reduce the request to this
      shape (fail closed), never forward on uncertainty.

    Frozen + slotted: an immutable value the lifecycle returns once. The ``body``
    is a read-only ``Mapping`` (the shipped ``UpstreamRequest.body`` shape), so the
    value type carries no mutable state.
    """

    plan: ExecutionPlan | None
    destination: str | None
    body: Mapping[str, object] | None
    refuse_code: str | None
    #: The gateway-minted per-request id (:meth:`ChatSurface._request_id`) set on a refusal
    #: that occurs AFTER identity resolution (admit / budget / input-block), so the rendered
    #: ``Error_Envelope`` body + the ``x-request-id`` header echo the SAME id the admission /
    #: budget verdicts carry. ``None`` for a pre-identity refusal (pre-checks / parse /
    #: identity / plan) — that render site has no id, so the envelope renders ``request_id: null``.
    request_id: str | None = None
    #: The offending request field for a field-specific parse refusal (Group F): ``"max_tokens"``
    #: for :data:`~gateway_v2.edge.wire.openai.INVALID_MAX_TOKENS` /
    #: ``"max_completion_tokens"`` for the completion-token boundary, carried from
    #: ``MalformedRequest.param``. The render site threads it into ``errors.render_http(param=...)``
    #: so the OpenAI envelope echoes ``error.param``; ``None`` for every non-field-specific code.
    refuse_param: str | None = None


def _refused(
    code: str, *, request_id: str | None = None, param: str | None = None
) -> LifecycleOutcome:
    """Build a fail-closed refusal outcome carrying ``code`` (zero provider calls).

    The one constructor for the refuse shape: ``refuse_code`` set, every provider
    input ``None``, so a refusing phase cannot accidentally leave a half-populated
    outcome that a later step might forward. ``request_id`` is threaded when the
    refusing phase ran after identity resolution (``None`` for a pre-identity
    refusal); ``param`` names the offending field for a boundary parse refusal
    (``None`` otherwise). Pure.
    """
    return LifecycleOutcome(
        plan=None,
        destination=None,
        body=None,
        refuse_code=code,
        request_id=request_id,
        refuse_param=param,
    )
