"""``ShedVerdict`` + Retry-After jitter + the ``ResourceGrant`` reservation (R2-07 / R2-08, GW19).

When the CoDel admission controller sheds a request at the door it must hand ``edge`` enough to
render an explicit HTTP 503 overload response, but **this layer never constructs HTTP objects**.
Per ``gateway_v2/gateway_v2/domain/posture.py`` ("``HTTPException`` and ``JSONResponse`` are
forbidden outside ``edge``/``resolve``") and the import-linter ``layers`` contract, ``admit`` sits
directly below ``edge`` and may import only ``runtime`` + ``domain``. So the component produces a
frozen **value** — a ``ShedVerdict`` carrying an overload *code* + ``retry_after_s`` +
``should_retry`` + ``request_id`` — and ``edge`` renders the 503, the ``Retry-After`` header, and
the ``x-should-retry`` header from it. This is the same code-vs-render split ``posture.py`` already
enforces for ``SHARED_STATE_UNAVAILABLE`` etc.

Retry-After floor + jitter (R2-08 / Property 5): in v1 and the prototype, sheds carried a 6-11 ms
Retry-After, which the OpenAI SDK honoured and retried almost immediately — the retries *became*
the load (≈2.6× amplification). The floor is reused from ``domain.posture.MIN_RETRY_AFTER_S``
(1.0 s; NOT redefined here) and jitter is drawn from an **injected** ``random.Random`` so the value
is deterministic under test. Because the floor is 1.0 s and the jitter is additive and
non-negative, ``shed_retry_after_s`` is always ``>= 1.0`` s and can therefore never land in the
6-11 ms band (Req 6.4).

Everything here is pure: frozen slotted dataclasses + a pure function of an injected RNG. There is
no module-level mutable state, and no HTTP object anywhere.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from gateway_v2.domain.posture import (
    BUDGET_UNAVAILABLE,
    MIN_RETRY_AFTER_S,
    OVERLOAD_SHED,
    gap_retry_after_s,
)

__all__ = (
    "Admitted",
    "BudgetVerdict",
    "ResourceGrant",
    "ShedVerdict",
    "budget_verdict",
    "shed_retry_after_s",
    "shed_verdict",
)


@dataclass(frozen=True, slots=True)
class ResourceGrant:
    """What an admitted request may consume — the card's GW03/GW19 reservation.

    A minimal frozen description of the resource slice admission grants a request. It is kept
    deliberately small: the full concurrency/credit accounting lives in ``admit/quota.py`` +
    ``admit/admission.py``; this type is the reserved shape ``grant.py`` owns per the card so the
    admit surface has a stable "what this request may consume" value. It is **not** an HTTP object
    and carries no capacity literal of its own.
    """

    owner_id: str
    request_id: str
    concurrency_slots: int = 1


@dataclass(frozen=True, slots=True)
class ShedVerdict:
    """The overload-response payload emitted when CoDel sheds a request at the door.

    This is a **code/value, not an HTTP object** — ``edge`` renders the HTTP 503, the
    ``Retry-After: ceil(retry_after_s)`` header, the ``x-should-retry: false`` header, and echoes
    ``request_id`` from this verdict. ``code`` is always ``posture.OVERLOAD_SHED`` (the one shared
    posture vocabulary, so ``admit`` never invents a second spelling).

    ``should_retry`` is **always ``False`` on a shed**: paired with the ≥ 1 s jittered
    ``retry_after_s`` it tells a retrying SDK client not to amplify load (R2-08 / Property 8).
    """

    code: str
    retry_after_s: float
    should_retry: bool
    request_id: str


@dataclass(frozen=True, slots=True)
class Admitted:
    """Frozen marker that a request passed the admission door (Req 2).

    An admitted request is answered late if necessary but is never abandoned after this marker is
    produced; shedding happens only *before* admission, never after.
    """

    owner_id: str
    request_id: str
    admitted_at: float


def shed_retry_after_s(
    rng: random.Random,
    *,
    floor_s: float = MIN_RETRY_AFTER_S,
    jitter_frac: float = 0.5,
) -> float:
    """Return a jittered Retry-After in seconds: ``floor_s + U(0, jitter_frac * floor_s)``.

    ``rng`` is an injected ``random.Random`` so the jitter is deterministic and property-testable.
    With the default ``floor_s = MIN_RETRY_AFTER_S`` (1.0 s) the result lies in
    ``[1.0, 1.0 + jitter_frac]`` s, so it is always ``>= 1.0`` s and can never fall in the
    6-11 ms band (Req 6.1 / 6.2 / 6.4, Property 5).
    """
    return floor_s + rng.random() * jitter_frac * floor_s


def shed_verdict(rng: random.Random, request_id: str) -> ShedVerdict:
    """Build a ``ShedVerdict`` for a shed request (convenience constructor).

    ``code`` is ``posture.OVERLOAD_SHED``, ``retry_after_s`` is the jittered floor from
    ``shed_retry_after_s``, and ``should_retry`` is ``False`` (always, on a shed). No HTTP object
    is constructed — ``edge`` renders the 503 from this value.
    """
    return ShedVerdict(
        code=OVERLOAD_SHED,
        retry_after_s=shed_retry_after_s(rng),
        should_retry=False,
        request_id=request_id,
    )


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    """The budget-refusal payload emitted when the org token budget is exhausted (R2-09, GW06).

    Mirrors ``ShedVerdict`` exactly: a **code/value, not an HTTP object**. ``edge`` renders the
    HTTP 503, the ``Retry-After: ceil(retry_after_s)`` header, the ``x-should-retry: false``
    header, and echoes ``request_id`` from this verdict. ``code`` is always
    ``posture.BUDGET_UNAVAILABLE`` (the one shared posture vocabulary, so ``admit`` never invents a
    second spelling — see ``domain/posture.py``).

    The budget posture is deliberately NARROWER than ``shared_state_unavailable``: it refuses only
    quota after the Remaining_Lease is spent, never the whole request path. ``should_retry`` is
    **always ``False`` on a budget refusal**: paired with the ≥ ``MIN_RETRY_AFTER_S`` jitter-free
    ``retry_after_s`` from ``gap_retry_after_s`` it tells a retrying SDK client not to amplify load
    (Req 5.6, R2-08). This component constructs **no** HTTP object.
    """

    code: str
    retry_after_s: float
    should_retry: bool
    request_id: str


def budget_verdict(
    request_id: str,
    *,
    rehydrate_period_ms: float = 0.0,
    refresh_ms: float = 0.0,
) -> BudgetVerdict:
    """Build a ``BudgetVerdict`` for a budget-exhausted request (convenience constructor).

    ``code`` is ``posture.BUDGET_UNAVAILABLE`` and ``should_retry`` is ``False`` (always, on a
    budget refusal). ``retry_after_s`` is ``gap_retry_after_s(rehydrate_period_ms=...,
    refresh_ms=...)``: one re-hydrator period plus one gateway refresh period, floored at
    ``MIN_RETRY_AFTER_S`` by the helper (Req 5.5, 5.6). The façade passes the periods it knows; the
    defaults of ``0.0`` collapse the gap to the ``MIN_RETRY_AFTER_S`` floor, so the result is always
    ``>= MIN_RETRY_AFTER_S`` and can never land in the sub-second band that amplified SDK retries.
    No HTTP object is constructed — ``edge`` renders the 503 from this value.
    """
    return BudgetVerdict(
        code=BUDGET_UNAVAILABLE,
        retry_after_s=gap_retry_after_s(
            rehydrate_period_ms=rehydrate_period_ms,
            refresh_ms=refresh_ms,
        ),
        should_retry=False,
        request_id=request_id,
    )
