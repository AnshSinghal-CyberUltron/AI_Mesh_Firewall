"""SDK-retry client stratum for the load harness (B6 / R2-21).

Models the fraction of clients that behave like a retrying SDK under overload (Req 5): when a
request is shed or errors, a *retrying* client re-issues it, which **amplifies** the offered load
above the original arrival count. This stratum is what lets a capacity run represent that
amplification instead of pretending every client gives up on the first shed.

Two retry shares (Req 5.1, 5.3). A ``RetryPolicy`` carries a **base** retry share — the configured
fraction of clients that retry following ordinary SDK retry semantics — and an **aggressive** retry
share applied *in addition* to the base (an aggressive-retry mode that retries more of the
population). Whether a given modeled client retries is a single seeded draw against the combined
share, keyed by the injected ``random.Random`` (Req 13.1): no wall-clock, no module-global
``random.*``, so the decision is reproducible offline and property-testable.

Honoring R2-08 ``x-should-retry: false`` (Req 5.2). R2-08 stamps shed responses with
``x-should-retry: false`` precisely so a well-behaved SDK does **not** re-issue them and pile
amplification onto an already-overloaded gateway. This module models the shed response as a small
frozen value (``ShedResponse``) carrying only that ``should_retry`` flag, and ``should_reissue``
returns ``False`` whenever the flag is ``False`` — *regardless* of whether the client is a retrying
client. A retry-eligible client re-issues only a response that both is retryable (``should_retry``
is ``True``) and belongs to a client the policy drew as retrying. This is the single authority for
"may this request be re-issued"; the driver/ladder consult it rather than re-deciding.

Amplification (Req 5.4). ``amplification_ratio`` is the ratio of **total** offered requests
(original arrivals plus every re-issue) to **original** offered requests — the per-run number the
report shows so retry amplification is visible. ``RetryOutcome`` accumulates original + re-issue and
exposes that ratio; an empty run (zero original requests) reports a ratio of ``1.0`` (no
amplification) rather than dividing by zero.

Capacity-literal posture (Req 14.2). Shares and counts are **inputs** (from ``StrataConfig`` and
the arrival schedule), never capacity-position literals; this module declares none and holds no
module-level mutable. Both value types are frozen + slots (Req 14.3). The module imports nothing
from the ``gateway_v2`` serving tree — retry is pure client behavior reacting to shed responses —
and in particular does **not** import the in-flight ``arrivals.py`` (it is self-contained: the
composer layers this stratum onto an ``Arrival`` later in ``request_plan.py``).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = (
    "RetryPolicy",
    "RetryOutcome",
    "ShedResponse",
    "amplification_ratio",
    "client_retries",
    "retry_outcome",
    "should_reissue",
)


@dataclass(frozen=True, slots=True)
class ShedResponse:
    """A modeled shed/error response the harness reacts to, carrying only the retry flag.

    ``should_retry`` mirrors R2-08's ``x-should-retry`` header: ``True`` for a response the SDK is
    permitted to re-issue (e.g. a transient transport error) and ``False`` for a shed response R2-08
    stamped as non-retryable so an SDK does not amplify an overloaded gateway (Req 5.2). It is a
    small immutable value, not an HTTP object — this module never touches an HTTP type.
    """

    should_retry: bool


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """The configured SDK-retry client model: base + aggressive retry shares (Req 5.1, 5.3).

    ``base_share`` is the fraction of modeled clients that retry following ordinary SDK retry
    semantics; ``aggressive_share`` is the additional fraction applied on top in aggressive-retry
    mode. The combined retry share is ``base_share + aggressive_share`` clamped to ``[0.0, 1.0]`` (a
    population fraction can never exceed the whole population), computed by ``combined_share``. Both
    are configured inputs (from ``StrataConfig.retry_share`` / ``aggressive_retry_share``), never
    capacity literals. Frozen + slots (Req 14.3).
    """

    base_share: float
    aggressive_share: float

    @property
    def combined_share(self) -> float:
        """The total retrying-client fraction — ``base + aggressive`` clamped to ``[0, 1]``."""
        combined = self.base_share + self.aggressive_share
        if combined < 0.0:
            return 0.0
        if combined > 1.0:
            return 1.0
        return combined


@dataclass(frozen=True, slots=True)
class RetryOutcome:
    """The per-run retry tally used to report amplification (Req 5.4).

    ``original_requests`` is the number of original offered requests (one per arrival);
    ``reissued_requests`` is the number of re-issues the retrying clients produced.
    ``total_offered`` is their sum (original + re-issues) and ``amplification`` is
    ``total_offered / original_requests`` — the ratio the report surfaces. An empty run
    (``original_requests == 0``) reports an amplification of ``1.0`` rather than dividing by zero.
    Frozen + slots (Req 14.3).
    """

    original_requests: int
    reissued_requests: int

    @property
    def total_offered(self) -> int:
        """Total offered requests = original arrivals + every re-issue."""
        return self.original_requests + self.reissued_requests

    @property
    def amplification(self) -> float:
        """Ratio of total to original offered requests (``1.0`` when no original requests)."""
        return amplification_ratio(
            original_requests=self.original_requests,
            total_offered=self.total_offered,
        )


def client_retries(policy: RetryPolicy, rng: random.Random) -> bool:
    """Draw whether a single modeled client is a *retrying* client (Req 5.1, 5.3).

    A pure seeded draw against the policy's combined base + aggressive share, keyed by the injected
    ``rng`` (never module-global ``random.*`` — Req 13.1): the client retries iff
    ``rng.random() < policy.combined_share``. With ``combined_share == 0.0`` no client ever retries;
    with ``1.0`` every client does. Consumes exactly one draw so the sequence stays reproducible.
    """
    return rng.random() < policy.combined_share


def should_reissue(response: ShedResponse, *, client_is_retrying: bool) -> bool:
    """Return whether this request is re-issued, honoring ``x-should-retry: false`` (Req 5.2).

    The single authority for "may this request be re-issued". A request is re-issued iff the modeled
    client is a retrying client **and** the response is retryable
    (``response.should_retry is True``). A response carrying ``x-should-retry: false``
    (``should_retry is False``) is **never** re-issued — even by a retrying client — so the harness
    models R2-08's shed-response contract exactly: a non-retryable shed does not add amplification.
    """
    return client_is_retrying and response.should_retry


def amplification_ratio(*, original_requests: int, total_offered: int) -> float:
    """The retry-amplification ratio ``total_offered / original_requests`` (Req 5.4).

    Returns ``1.0`` (no amplification) when ``original_requests`` is zero or negative, so an empty
    run never divides by zero. Otherwise the ratio of total offered requests (original arrivals plus
    every re-issue) to original offered requests — the per-run number the report shows.
    """
    if original_requests <= 0:
        return 1.0
    return total_offered / original_requests


def retry_outcome(*, original_requests: int, reissued_requests: int) -> RetryOutcome:
    """Build a ``RetryOutcome`` from the original-request and re-issue counts (Req 5.4).

    A thin constructor that clamps a negative input to zero (counts are non-negative by nature) so
    a caller cannot report a nonsensical negative amplification; the ratio is then derived on the
    value type via ``RetryOutcome.amplification``.
    """
    return RetryOutcome(
        original_requests=max(original_requests, 0),
        reissued_requests=max(reissued_requests, 0),
    )
