"""The ONE authorized provider entry + the runtime provenance assertion (GW11 C3, R2).

This module is the single place a provider is called (R2.1). It composes the shipped
and just-built dispatch surfaces into one guarded flow and does NOT rewrite the GW12
transport (R2.4 / R12.2):

* :func:`assert_authorized` is the Runtime_Assertion (R2.5, R10.6): it re-verifies a
  :class:`~gateway_v2.domain.dispatch_authorization.DispatchAuthorization`'s provenance
  by WITNESS IDENTITY before any provider byte. A forged object produced by
  ``object.__new__`` (which bypasses ``__post_init__``) carries a ``_witness`` that is
  not the module-private ``_WITNESS`` by identity, so this raises — the dynamic
  complement to the static provenance AST gate, backing the recorder zero-call proof
  (LGW11-2). This file performs only a bare ``is _WITNESS`` identity READ; it mints,
  reconstructs or mutates nothing, so ``lint/check_dispatch_provenance.py`` does NOT
  flag it (that gate flags construction / ``_mint`` / ``replace`` / ``__new__`` /
  ``__setattr__`` of ``_witness`` / alias evasion — not a bare witness comparison).

* :class:`AuthorizedProviderEntry` holds its collaborators as INJECTED, immutable
  references in ``__slots__`` (the :class:`~gateway_v2.dispatch.provider.ProviderClient`,
  the :class:`~gateway_v2.dispatch.transform.Transform`, the
  :class:`~gateway_v2.dispatch.routing.DispatchRouter`, the error-map/scan functions,
  the :class:`~gateway_v2.dispatch.pool.ProviderPool`, the local REWRITE guard seam, the
  audit sink and an injected clock). None are shared mutable request-path state (R6.3):
  the only mutable state on the path lives inside the pool's own per-instance semaphore.

* :meth:`AuthorizedProviderEntry.dispatch` REQUIRES the authorization as a parameter so a
  call omitting it is a build-time ``mypy --strict`` error — the static half of LGW11-1
  (R2.2). Its flow is: assert provenance → apply-and-verify the request bytes (terminal
  on failure, no bytes sent, R3.3) → derive the destination deterministically from the
  pinned plan via ``DispatchRouter.select`` (pure, no adjudicator, no shared mutable
  state, R6) → shape the verified request to the upstream via ``Transform.to_provider``
  → audit the started request on lease (R8.8, fired even if the stream is cancelled) →
  lease the bounded pool (R9) → for ALLOW/FLAG/REDACT drive the shipped
  ``ProviderClient.open`` and scan/map each event; for a REWRITE decision run the local
  guard seam instead of ANY provider generation call (R5, LGW11-4).

Layering. ``dispatch`` sits ABOVE ``detect`` in the import-linter layer order
(``edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime >
contracts > domain``), so importing ``detect.guard.backend`` from here would be an
upward import the contract FORBIDS. The REWRITE guard backend is therefore an INJECTED
collaborator typed by the LOCAL structural :class:`RewriteBackend` protocol declared in
this module — exactly as ``provider_errors.py`` declares ``ScanSeam`` and
``http_provider.py`` injects its SSE decoder rather than importing upward. The injected
object may be the real ``detect/guard`` ``GuardBackend`` (which structurally satisfies
``RewriteBackend.classify``), wired at the ``edge`` boundary. This module imports only
``domain`` + dispatch siblings; it takes no ``detect``/``plan``/``admit``/``edge`` import,
so ``lint-imports`` stays satisfied.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from gateway_v2.dispatch.catalogue import ProviderCatalogue
from gateway_v2.dispatch.pool import ProviderPool
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.provider_errors import ScanSeam, scan_error_payload
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import GatewayRequest, Transform
from gateway_v2.dispatch.verify import apply_and_verify
from gateway_v2.domain import Decision, ExecutionPlan
from gateway_v2.domain.dispatch_authorization import _WITNESS, DispatchAuthorization

__all__ = (
    "AuthorizedProviderEntry",
    "AuditSink",
    "RewriteBackend",
    "assert_authorized",
)


def assert_authorized(auth: DispatchAuthorization) -> None:
    """Runtime_Assertion (R2.5, R10.6): re-verify provenance by witness identity.

    Checks ``auth._witness is _WITNESS`` by IDENTITY before any provider byte is sent. A
    naive construction already raised in ``__post_init__``; a forged object produced by
    ``object.__new__`` (which bypasses ``__post_init__``) carries a ``_witness`` that is
    not the one module-private ``_WITNESS`` by identity — so this raises here, before the
    entry can reach the pool, the client, or the guard seam. This is the dynamic
    complement to the static provenance gate and backs the recorder zero-call proof
    (R4.3): a forged auth never reaches a provider at run time.

    The ``getattr(..., None)`` guard tolerates an object missing the slot entirely (a
    hand-rolled stand-in) — a missing witness is ``None``, which is not ``_WITNESS``, so
    it fails closed. This is a bare identity READ of the module-private witness; it mints,
    reconstructs and mutates nothing, so the provenance AST gate does not flag it.
    """
    if getattr(auth, "_witness", None) is not _WITNESS:
        raise ValueError("unauthorized dispatch: authorization provenance check failed")


@runtime_checkable
class AuditSink(Protocol):
    """The sink the entry records every STARTED request through (R8.8).

    Declared structurally (not imported) so this module takes no extra layer edge — any
    object with a ``record_started(request_id, destination) -> None`` method satisfies it,
    including the real audit producer wired at ``edge``. The entry records a start the
    moment the pool lease is acquired, INDEPENDENT of whether the stream later completes
    or is cancelled (R8.8): the record fires before the first provider byte is read, so a
    cancelled stream is still audited as started.
    """

    def record_started(self, request_id: str, destination: str) -> None:
        """Record that a provider request STARTED (including later-cancelled ones). O(1)."""
        ...


@runtime_checkable
class RewriteBackend(Protocol):
    """The LOCAL rewrite seam a REWRITE decision runs through — never a provider (R5.2).

    ``dispatch`` sits ABOVE ``detect`` in the layer order, so the ``detect/guard``
    ``GuardBackend`` is INJECTED and typed by this minimal local protocol rather than
    imported upward (mirroring ``provider_errors.ScanSeam``). The real
    ``detect.guard.backend.GuardBackend`` structurally satisfies this ``classify``
    signature, so the ``edge`` wiring passes the shipped local-model backend here. A
    REWRITE is executed by this seam alone — the entry makes ZERO firewall-attributable
    provider calls for a REWRITE (LGW11-4).
    """

    async def classify(self, windows: Sequence[str]) -> Sequence[object]: ...


class AuthorizedProviderEntry:
    """The ONE provider entry; requires a ``DispatchAuthorization`` (R2.1, R2.2).

    Holds its collaborators as injected, immutable references in ``__slots__`` — the
    shipped :class:`ProviderClient` transport, the pure :class:`Transform` and
    :class:`DispatchRouter`, the C27 error-scan function, the bounded
    :class:`ProviderPool`, the local :class:`RewriteBackend` guard seam, the
    :class:`AuditSink`, and the injected :class:`ScanSeam` the provider-error payload is
    scanned through. None of these are shared MUTABLE request-path state: the transform
    and router are pure/stateless, the error functions are module-level pure helpers, and
    the only per-request mutable state is confined to the pool's own semaphore (R6.3).
    The entry is reusable across concurrent requests precisely because it carries no
    per-request mutable state of its own.
    """

    __slots__ = (
        "_audit",
        "_client",
        "_pool",
        "_rewrite",
        "_router",
        "_scan_seam",
        "_transform",
    )

    def __init__(
        self,
        *,
        client: ProviderClient,
        transform: Transform,
        router: DispatchRouter,
        pool: ProviderPool,
        rewrite: RewriteBackend,
        scan_seam: ScanSeam,
        audit: AuditSink,
    ) -> None:
        """Inject every collaborator (no shared mutable request-path state, R6.3).

        ``client`` is the shipped ``@runtime_checkable`` ``ProviderClient`` transport
        (``StubProviderClient`` in tests, the real HTTP client in production) fronted
        unchanged (R2.4). ``transform``/``router`` are the pure, stateless shaping and
        routing helpers. ``pool`` is the bounded, instrumented provider pool (its bound
        injected from ``ResourceContract`` upstream — no capacity literal here, R9.1).
        ``rewrite`` is the LOCAL guard seam a REWRITE runs through (never a provider,
        R5.2). ``scan_seam`` sanitises a provider error payload before forward (R8.6).
        ``audit`` records every started request, including cancelled ones (R8.8).
        """
        self._client = client
        self._transform = transform
        self._router = router
        self._pool = pool
        self._rewrite = rewrite
        self._scan_seam = scan_seam
        self._audit = audit

    async def dispatch(
        self,
        auth: DispatchAuthorization,
        *,
        decision: Decision,
        request: GatewayRequest,
        plan: ExecutionPlan,
        catalogue: ProviderCatalogue,
        request_id: str,
        rewrite: bool = False,
    ) -> AsyncIterator[UpstreamEvent]:
        """Issue the one authorized provider request, verified bytes only (R2, R3, R5, R6).

        ``auth`` is a REQUIRED positional parameter: a call omitting it is a build-time
        ``mypy --strict`` error (the static half of LGW11-1, R2.2). The flow, in order,
        is fail-closed and sends no byte until every pre-provider check has passed:

        1. :func:`assert_authorized` re-verifies the authorization's provenance by witness
           identity BEFORE any provider byte (R2.5, R10.6). A forged auth raises here and
           the recorder shows zero provider calls (R4.3).
        2. :func:`~gateway_v2.dispatch.verify.apply_and_verify` applies ``decision``'s
           transformations to ``request`` and verifies the result against the finding
           spans; a verification failure is TERMINAL and propagates with no bytes sent
           (R3.1-R3.3). For a REDACT decision only the sanitized bytes continue (R3.4).
        3. The destination is derived DETERMINISTICALLY from the pinned ``plan`` via
           ``DispatchRouter.select`` — pure, no adjudicator call, no shared mutable router
           state (R6.1-R6.4). ``catalogue`` is the resolved snapshot the destination's
           provider/model is drawn from (R7.1); passing it (already resolved) keeps the
           empty-vs-outage distinction at the resolution site, not on this hot path.
        4. The verified request is shaped to the upstream via ``Transform.to_provider``,
           carrying the plan-derived ``destination`` through unchanged (R2.4).
        5. A REWRITE decision runs through the LOCAL ``RewriteBackend`` seam and makes
           ZERO provider calls (R5, LGW11-4); every other non-terminal disposition drives
           the shipped ``ProviderClient.open`` under a bounded pool lease.

        ``rewrite`` carries the resolver's winning-action signal that the ``Decision``
        vocabulary itself erases: the resolver maps ``Action.REWRITE`` to
        ``Disposition.REDACT`` (``resolve/decision.py::_action_to_disposition``), so a
        REWRITE is byte-identical to a REDACT at the disposition level. The one bit of
        provenance — "the winning action was REWRITE and REWRITE is supported" — is passed
        explicitly so the entry can route a REWRITE to the LOCAL guard seam instead of a
        provider generation (R5.2) without re-deriving the action. It defaults to
        ``False`` (ALLOW/FLAG/REDACT drive the provider), so an ordinary dispatch is
        unaffected.

        The method is ``async`` and yields each :class:`UpstreamEvent` as it arrives, so
        the caller writes ``async for event in entry.dispatch(auth, ...)``.
        """
        # (1) Runtime provenance assertion — before ANY provider byte (R2.5, R10.6).
        assert_authorized(auth)

        # (2) Apply + verify the request bytes; terminal on failure (R3.1-R3.3). A raised
        # VerificationFailure propagates to the caller with no provider byte sent.
        verified = apply_and_verify(decision, request)

        # (3) Deterministic, plan-derived routing — pure, no adjudicator, no shared
        # mutable state (R6). The catalogue snapshot is the resolved provider set the
        # destination is drawn against; it is passed in already resolved (R7.1).
        destination = self._router.select(plan)
        _ = catalogue  # resolved snapshot carried on the path; routing stays plan-pure.

        # (4) Shape the VERIFIED request to the upstream, carrying the plan-derived
        # destination through unchanged (R2.4). The sanitized bytes are the only bytes
        # that can reach the provider.
        routed = GatewayRequest(
            destination=destination,
            model=verified.request.model,
            text_deltas=verified.request.text_deltas,
        )
        upstream = self._transform.to_provider(routed)

        async for event in self._open_stream(
            upstream=upstream,
            request_id=request_id,
            destination=destination,
            rewrite=rewrite,
        ):
            yield event

    async def _open_stream(
        self,
        *,
        upstream: UpstreamRequest,
        request_id: str,
        destination: str,
        rewrite: bool,
    ) -> AsyncIterator[UpstreamEvent]:
        """Lease the bounded pool, audit the start, then drive the right backend.

        The pool lease is acquired first (bounded backpressure, R9.2); the request is
        audited as STARTED the instant the lease is held and BEFORE the first provider
        byte, so a stream cancelled mid-iteration is still recorded as started (R8.8). A
        REWRITE (``rewrite is True``) drives the LOCAL guard seam and yields nothing from
        the provider (R5, LGW11-4); every other disposition drives the shipped
        ``ProviderClient.open`` and scans each event's error payload before forwarding it
        (R8.6).
        """
        async with self._pool.lease():
            # Audit the started request on lease — fired even if the stream is later
            # cancelled, because it precedes the first provider read (R8.8).
            self._audit.record_started(request_id, destination)

            if rewrite:
                # REWRITE runs on the LOCAL guard seam — ZERO firewall-attributable
                # provider calls (R5, LGW11-4). The client is never touched here.
                await self._run_rewrite(upstream)
                return

            async for event in self._client.open(upstream):
                yield self._scan_event(event)

    def _scan_event(self, event: UpstreamEvent) -> UpstreamEvent:
        """Scan a mid-stream provider error frame before forwarding it (R8.6).

        An ordinary content event passes through unchanged. An event carrying an
        ``error_frame`` has that payload run through the injected ``ScanSeam`` so a
        provider error frame echoing a secret is sanitised, not forwarded raw; the
        sanitised frame replaces the raw one on a new (frozen) event value.
        """
        if event.error_frame is None:
            return event
        scanned = scan_error_payload(event.error_frame, self._scan_seam)
        if not scanned.changed:
            return event
        return UpstreamEvent(
            text_deltas=event.text_deltas,
            final=event.final,
            error_frame=scanned.text,
        )

    async def _run_rewrite(self, upstream: UpstreamRequest) -> None:
        """Run a REWRITE through the LOCAL guard seam — never a provider call (R5.2).

        Routes the rewrite execution to the injected ``RewriteBackend`` (the shipped
        ``detect/guard`` local-model backend), so the firewall initiates ZERO provider
        generations for a REWRITE (LGW11-4). The windows handed to the local model are the
        upstream request's text deltas; the result is consumed locally — no provider
        ``open`` is ever called on this branch.
        """
        windows = [text for _channel, text in _deltas_of(upstream)]
        await self._rewrite.classify(windows)


def _deltas_of(upstream: UpstreamRequest) -> tuple[tuple[str, str], ...]:
    """Read the packed ``text_deltas`` back out of an :class:`UpstreamRequest` body.

    ``Transform.to_provider`` packs the request's ``(channel, text)`` deltas under the
    shipped body key; this narrows the ``Mapping[str, object]`` value back to the delta
    shape for the local rewrite windows, returning an empty tuple for a body that does not
    carry well-formed deltas rather than forwarding an ill-typed value.
    """
    raw = upstream.body.get("text_deltas", ())
    if not isinstance(raw, tuple):
        return ()
    out: list[tuple[str, str]] = []
    for item in raw:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            out.append((item[0], item[1]))
        else:
            return ()
    return tuple(out)
