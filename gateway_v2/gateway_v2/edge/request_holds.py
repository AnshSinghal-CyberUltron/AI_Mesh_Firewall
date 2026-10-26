"""Per-request hold ledger that releases the admission slot + the budget spend exactly once.

The §10.4 request lifecycle acquires two bounded resources on the way in — a GW19 admission SLOT
(``AdmissionController.admit``, R5) and a GW06 budget SPEND (``QuotaComponent.evaluate``, R6) — and
until this module existed it released NEITHER on the way out. Two leaks followed:

* **Group D — the admission-slot leak.** The GW15 lifecycle admitted through ``admit`` but never
  called ``complete`` on ANY exit path (success, ``[DONE]``, error frame, a refusal after
  admission, a raised exception, cancellation, a kill-switch cut, a stream timeout). The
  request-queue slot therefore stayed occupied forever and the in-flight record was never pruned, so
  exported queue depth only ever grew and a later drain waited on requests that had already finished
  (bugfix.md 1.9–1.12). An admitted request MUST have ``complete`` called for it exactly once on
  every exit path (2.9); a request shed or refused BEFORE admission must NOT (2.10).

* **Group F — the uncharged refund.** A request that spent budget and was then refused with ZERO
  provider calls (an input BLOCK, an input scan failure, an ``input_unmaskable`` REDACT-verification
  failure between the spend and dispatch) kept its charge against the lease (bugfix.md 1.17). C29
  requires the spend be returned "when nothing is dispatched" — so the spend must be refunded iff
  the provider was never opened. Once the provider HAS been opened the spend stands: the provider
  saw the request, and this phase does not reconcile against actual usage (that true-up is Phase 2).

:class:`RequestHolds` is the per-request answer. It is a plain ``__slots__`` object, constructed
once per request and NEVER shared across requests (it carries one request's ``request_id``, ``cost``
and its acquire/dispatch flags — sharing it would release the wrong request's slot). The lifecycle
records what this one request acquired — :meth:`admitted` after an ``Admitted`` verdict,
:meth:`spent` after the budget phase, :meth:`mark_dispatched` immediately before ``provider.open`` —
and the handler calls :meth:`release` in a single ``finally`` that covers every exit.

**Release ordering (refund then complete).** On its first call :meth:`release`:

1. **refunds** the spend first — but ONLY if budget was spent AND the provider was never dispatched
   (``spent and not dispatched``). If the request was dispatched, the spend stands and nothing is
   refunded. This is the C29 "refund when nothing is dispatched" direction and the gate on
   ``not dispatched`` is what makes a charge-with-provider-call keep its charge.
2. **completes** the admission slot second — if the request was admitted — regardless of dispatch,
   because an admitted request always holds a slot that must be released on completion.

**Runs at most once, behind a latch.** :meth:`release` sits in a ``finally`` and can legitimately be
reached twice (a normal ``finally`` plus an exception unwinding the same frame). A ``_released``
bool latch makes the first call do the work and every later call a no-op, so a double release never
refunds twice or completes twice (which would pop a foreign request's slot, 2.11).

**Never awaits, never raises.** Both ``refund`` and ``complete`` are SYNCHRONOUS on their protocols,
so :meth:`release` is synchronous — it never ``await``s inside a ``finally``. And because it runs in
a ``finally`` while a real response or exception is already in flight, a raise from either call
would MASK that real outcome; so each call is wrapped best-effort and isolated from the other. A
lost refund or a lost ``complete`` is the conservative direction (a bounded resource held a little
longer), never a crash that hides the response. Even if ``refund`` raises, ``complete`` is still
attempted — and vice versa.

``edge`` is the top layer, so importing the :class:`~gateway_v2.edge.chat_deps.AdmissionDecider` /
:class:`~gateway_v2.edge.chat_deps.BudgetDecider` protocols from the ``chat_deps`` sibling is an
allowed intra-layer edge→edge import. There is no module-level mutable and no capacity literal here:
every bit of state lives on the per-request ``__slots__`` instance.
"""

from __future__ import annotations

from gateway_v2.domain.decision import Decision
from gateway_v2.edge.chat_deps import AdmissionDecider, BudgetDecider

__all__ = ("RequestHolds",)


class RequestHolds:
    """The per-request ledger of acquired holds, released exactly once in a ``finally``.

    Constructed once per request and never shared. The lifecycle records each hold as it is
    acquired (:meth:`admitted`, :meth:`spent`, :meth:`mark_dispatched`) and :meth:`release` returns
    them — refund first (iff spent and not dispatched), then complete (iff admitted) — at most once
    behind the ``_released`` latch, synchronously and without ever raising.

    ``__slots__`` so the instance is a fixed cell with no ``__dict__``: a request id, a cost, the
    three acquire/dispatch flags and the one-way release latch, nothing more.
    """

    __slots__ = (
        "_admitted",
        "_cost",
        "_dispatched",
        "_input_decision",
        "_org_id",
        "_output_decision",
        "_released",
        "_request_id",
        "_spent",
    )

    def __init__(self) -> None:
        self._admitted: bool = False
        self._spent: bool = False
        self._dispatched: bool = False
        self._released: bool = False
        self._request_id: str = ""
        self._cost: int = 0
        self._input_decision: Decision | None = None
        self._output_decision: Decision | None = None
        self._org_id: str = ""

    def admitted(self, request_id: str) -> None:
        """Record that admission granted a slot for ``request_id`` (``complete``-d on exit)."""
        self._admitted = True
        self._request_id = request_id

    def spent(self, cost: int, request_id: str) -> None:
        """Record the budget phase spent ``cost`` for ``request_id`` (refundable to dispatch)."""
        self._spent = True
        self._cost = cost
        self._request_id = request_id

    def note_input(self, decision: Decision) -> None:
        """Record the input decision that was enforced for this request."""
        self._input_decision = decision

    def note_output(self, decision: Decision) -> None:
        """Record the output decision. Streaming may leave this unset."""
        self._output_decision = decision

    def note_tenant(self, org_id: str) -> None:
        """Record the tenant the audit row must be attributed to."""
        self._org_id = org_id

    def note_request(self, request_id: str) -> None:
        """Record the minted id before admission, so a shed still has a row key."""
        self._request_id = request_id

    @property
    def input_decision(self) -> Decision | None:
        return self._input_decision

    @property
    def output_decision(self) -> Decision | None:
        return self._output_decision

    @property
    def org_id(self) -> str:
        return self._org_id

    @property
    def request_id(self) -> str:
        return self._request_id

    @property
    def dispatched(self) -> bool:
        return self._dispatched

    def mark_dispatched(self) -> None:
        """Record that the provider was opened; after this the spend stands and is not refunded."""
        self._dispatched = True

    def release(self, admission: AdmissionDecider, quota: BudgetDecider) -> None:
        """Release the held resources once: refund (iff spent and not dispatched) then complete.

        Idempotent behind the ``_released`` latch (a second call is a no-op), synchronous (never
        ``await``s) and never raises — each underlying call is wrapped best-effort and isolated, so
        a failure of one neither masks the in-flight response nor prevents the other from running.
        """
        if self._released:
            return
        self._released = True

        if self._spent and not self._dispatched:
            try:
                quota.refund(self._cost, request_id=self._request_id)
            except Exception:  # noqa: BLE001 - finally-path: a lost refund must never mask the response.
                pass

        if self._admitted:
            try:
                admission.complete(self._request_id)
            except Exception:  # noqa: BLE001 - finally-path: a lost complete must never mask the response.
                pass
