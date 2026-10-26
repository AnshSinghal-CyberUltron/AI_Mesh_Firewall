"""v2-enforcement-leaks Group D: exactly-once completion over seeded lifecycle schedules.

Property 9 (design, Bug Condition D): for a schedule of chat and embeddings requests served one
after another against the REAL :class:`~gateway_v2.admit.admission.AdmissionController`, every
request the controller ADMITS has ``complete`` called for it exactly once (2.9), every request
refused BEFORE admission (pre-check, parse, identity, plan) or shed by a ``ShedVerdict`` is never
completed (2.10), and after every served request the exported request-queue depth equals the
number of admitted-but-unfinished requests (2.12).

This is the 10,000-iteration seeded generalisation of the example-based exploration test
``tests/edge/test_v2_leaks_d_lifecycle.py`` (task 7, now passing). It reuses that test's harness
unchanged -- the ``_Case`` exit-path matrix, the ``_app`` default-boot wiring, the scripted
``_Provider``, the injected ``_Clock``, and the ``_serve`` ASGI driver -- rather than inventing new
fixtures, so the exit paths it exercises are exactly the ones the exploration test proved reachable:
success, ``[DONE]``, error frame, budget refusal, input BLOCK, scan failure, ``input_unmaskable``,
output BLOCK, a raised exception, cancellation, a kill-switch cut and a stream timeout, over chat
(streaming and non-streaming) and embeddings.

**The real controller, a counting wrapper.** Each schedule runs against ONE real
:class:`AdmissionController` (so the request queue, CoDel admission and in-flight pruning are the
real product). ``_CountingController`` is a thin pass-through that DELEGATES ``admit`` and
``complete`` to that real controller while recording, per request id, how many times each was
called. It reads depth straight off the real controller's :meth:`queue_report`, so the invariant
after each step is asserted against real queue depth, not a double.

**The exits that must complete vs the exits that must not.** ``_ADMITTED`` is the exploration test's
matrix of paths that DO reach an ``Admitted`` verdict; every one of them must drive exactly one
``complete`` (the slot the controller handed out is released on the way out, on every exit path).
``_NOT_ADMITTED`` is the matrix of pre-admission refusals and sheds; none of them completes
anything, and a shed is admitted-at-the-door-then-refused so it must also leave the queue untouched.

**The request-queue-depth invariant.** Because each request is served to completion before the next
begins, and an admitted request is completed on exit, the admitted-but-unfinished count returns to 0
after every step -- so real request depth must read 0 after every step. The wrapper's running
``len(admitted_ids) - total_completed`` tally is asserted to equal that depth after every step,
which is the 2.12 "depth equals the admitted, unfinished count" invariant made concrete on a
sequential schedule.

**Bounded, non-shedding schedules.** The controller is built with a request depth comfortably
above the steps per schedule, and only one request is in flight at a time, so a healthy run never
sheds ``overload_shed`` -- a leaked slot would show as a non-zero depth after a step, which is
exactly what this property forbids. The run also asserts (the counters below) that the schedule
really exercised both surfaces and a broad set of the exit paths, so the property is not vacuous.

**Integration (end of file).** Three example-level checks pin the clauses the schedule covers only
statistically: a mid-stream cancellation completes exactly once; a kill-switch cut completes exactly
once; and an embeddings request completed AND refunded on each of input BLOCK, scan failure and
``input_unmaskable``.

Seeded ``random.Random`` (no hypothesis), at least 10,000 iterations, ``asyncio.run`` for async; the
seed is in every assertion message.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Sequence
from typing import cast

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.posture import STREAM_KILLED
from gateway_v2.edge.app import ASGIApplication, build_app
from gateway_v2.edge.chat_deps import unresolved_snapshot_source
from gateway_v2.edge.openai_chat import (
    REFUSE_INPUT_BLOCKED,
    REFUSE_INPUT_SCAN_FAILURE,
    REFUSE_INPUT_UNMASKABLE,
)
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol

# Reuse the exploration test's harness verbatim -- same exit-path matrix, same default-boot wiring,
# same scripted provider / clock / ASGI driver -- so the property generalises exactly those paths.
from tests.edge._chat_lifecycle_fixtures import warm_snapshot_source
from tests.edge.test_v2_leaks_d_lifecycle import (
    _ADMITTED,
    _NOT_ADMITTED,
    CARD,
    OUTPUT_CANARY,
    ResolvingIdentity,
    _app,
    _Budget,
    _Case,
    _Clock,
    _contract,
    _Control,
    _Metrics,
    _NoIdentity,
    _Provider,
    _scanner,
    _serve,
)

#: The one deterministic seed the whole run is driven from (in every assertion message).
_SEED = 0xD00D_1EAC
#: At least 10,000 iterations (the spec's property idiom: seeded ``random.Random``, no hypothesis).
_ITERATIONS = 10_000
#: Steps per schedule. A short run of sequential requests against one shared real controller.
_MIN_STEPS = 1
_MAX_STEPS = 4
#: The real controller's per-queue depth. Comfortably above ``_MAX_STEPS`` (only one request is in
#: flight at a time on a sequential schedule), so a healthy run never sheds ``overload_shed``; a
#: leaked slot would be caught as a non-zero depth long before the bound is reached.
_DEPTH = 16
#: The content planted in every chat/embeddings body: the BLOCK canary, so a ``detector="block"``
#: case resolves ``input_blocked`` and a clean detector lets the request through.
_CONTENT = f"pay with {CARD} please"

#: The admitted exit paths (keys of the exploration matrix): each must complete exactly once.
_ADMITTED_NAMES: tuple[str, ...] = tuple(_ADMITTED)
#: The pre-admission refusal exit paths the property schedule draws from: pre-check, parse,
#: identity and plan. The exploration matrix's ``*-shed`` cases are EXCLUDED here because they
#: force a shed through a shedding admission DOUBLE -- the real :class:`AdmissionController` does
#: not shed at the low, one-in-flight-at-a-time load a sequential schedule puts on it (that is the
#: correct non-shedding behaviour, 3.10). A genuine real-controller ``overload_shed`` is driven
#: deterministically in :func:`test_real_shed_is_never_completed` by saturating the request queue,
#: where the 2.10 "a shed is never completed" clause is asserted directly.
_NOT_ADMITTED_NAMES: tuple[str, ...] = tuple(
    name for name, case in _NOT_ADMITTED.items() if not case.shed
)
#: Every schedule step is drawn from the union, so admitted and unadmitted exits interleave.
_ALL_NAMES: tuple[str, ...] = _ADMITTED_NAMES + _NOT_ADMITTED_NAMES


def _case_for(name: str) -> _Case:
    """The exploration ``_Case`` behind a schedule-step name (admitted or not)."""
    return _ADMITTED[name] if name in _ADMITTED else _NOT_ADMITTED[name]


class _CountingController:
    """A pass-through over the REAL controller that counts ``admit``/``complete`` per request id.

    It satisfies the ``AdmissionDecider`` protocol structurally (async ``admit`` returning
    ``Admitted | ShedVerdict``; synchronous ``complete``). Every call is delegated to the wrapped
    real :class:`AdmissionController`, so the request queue, CoDel admission and in-flight pruning
    are the real product; the wrapper only records what the lifecycle did. ``admitted_ids`` is the
    ordered list of request ids that got an ``Admitted`` verdict (a shed is NOT recorded there);
    ``completed`` counts ``complete`` calls per id.
    """

    __slots__ = ("inner", "admitted_ids", "completed")

    def __init__(self, inner: AdmissionController) -> None:
        self.inner = inner
        self.admitted_ids: list[str] = []
        self.completed: dict[str, int] = {}

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        verdict = await self.inner.admit(owner_id, request_id, enqueued_at)
        if isinstance(verdict, Admitted):
            self.admitted_ids.append(request_id)
        return verdict

    def complete(self, request_id: str) -> None:
        self.completed[request_id] = self.completed.get(request_id, 0) + 1
        self.inner.complete(request_id)

    def request_depth(self) -> int:
        """The real controller's current request-queue depth."""
        return self.inner.queue_report(now=0.0).depth("request")

    @property
    def total_completed(self) -> int:
        """Total ``complete`` calls across all ids (admitted exits should each add exactly one)."""
        return sum(self.completed.values())

    @property
    def max_completed_per_id(self) -> int:
        """The largest ``complete`` count any single id received (must never exceed 1 here)."""
        return max(self.completed.values(), default=0)


def _controller() -> AdmissionController:
    """A real controller over a fixed clock (sojourn 0, so CoDel admits below the queue bound)."""
    bounds = AdmissionBounds(
        concurrency=_DEPTH,
        request_depth=_DEPTH,
        guard_depth=_DEPTH,
        dispatch_depth=_DEPTH,
        egress_depth=_DEPTH,
        audit_depth=_DEPTH,
    )
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )


async def _run_step(controller: _CountingController, case: _Case, content: str) -> str:
    """Serve one request through the default-boot ASGI lifecycle against the shared controller."""
    clock = _Clock()
    provider = _Provider(case.provider, clock)
    app = _app(case, admission=controller, provider=provider, clock=clock)
    return await _serve(case, app, provider, content)


def test_exactly_once_completion_over_seeded_schedules() -> None:
    """Property 9: every admitted request completes exactly once; depth == unfinished count (D).

    For 10,000 seeded schedules of chat and embeddings requests served sequentially against the
    real :class:`AdmissionController`, after every step:

    * the number of ``complete`` calls equals the number of admitted requests so far (each admitted
      request completed exactly once, and nothing that was not admitted was completed);
    * no admitted id was completed more than once;
    * the real request-queue depth equals the admitted-but-unfinished count (0 on a sequential
      schedule, since each request is completed before the next begins).

    **Validates: Requirements 2.9, 2.10, 2.12**
    """
    rng = random.Random(_SEED)

    admitted_names_seen: set[str] = set()
    unadmitted_names_seen: set[str] = set()
    surfaces_seen: set[str] = set()

    for index in range(_ITERATIONS):
        controller = _CountingController(_controller())
        steps = rng.randint(_MIN_STEPS, _MAX_STEPS)

        for step in range(steps):
            name = rng.choice(_ALL_NAMES)
            case = _case_for(name)
            surfaces_seen.add(case.surface)
            (admitted_names_seen if name in _ADMITTED else unadmitted_names_seen).add(name)

            # Unique content per step so distinct requests get distinct (content-hashed) request
            # ids. Then "completed exactly once per id" is a meaningful per-id invariant -- reusing
            # one body across steps would collide ids and legitimately complete the shared id once
            # per admit, which is exactly-once but not once-per-id. The CARD canary is always
            # present so a ``detector="block"`` step still resolves ``input_blocked``.
            content = f"{_CONTENT} :: schedule {index} step {step}"
            outcome = asyncio.run(_run_step(controller, case, content))

            where = (
                f"seed={_SEED:#x} i={index} step={step} name={name!r} "
                f"surface={case.surface} outcome={outcome!r}"
            )

            # The step took its named exit path. This keeps the completion assertions honest: a
            # mis-routed request could otherwise mask a missing or an extra complete.
            assert outcome == case.expect, f"{where}: expected exit {case.expect!r}"

            # Exactly-once completion (2.9) and no-completion-before-admission (2.10): the number of
            # complete calls equals the number of admitted verdicts granted, and no id is completed
            # twice. Pre-admission refusals and sheds never appear in admitted_ids, so they can
            # never contribute a complete.
            admitted_so_far = len(controller.admitted_ids)
            assert controller.total_completed == admitted_so_far, (
                f"{where}: {controller.total_completed} complete call(s) for "
                f"{admitted_so_far} admitted request(s); each admitted request must complete "
                f"exactly once and no unadmitted request may complete"
            )
            assert controller.max_completed_per_id <= 1, (
                f"{where}: an id was completed {controller.max_completed_per_id} times "
                f"(completed={controller.completed!r}); completion must be exactly once"
            )

            # Request-queue depth equals the admitted-but-unfinished count (2.12). Each request is
            # served to completion before the next, so an admitted request is already completed and
            # the unfinished count -- and the real depth -- is 0.
            unfinished = admitted_so_far - controller.total_completed
            assert unfinished == 0, f"{where}: {unfinished} admitted request(s) left unfinished"
            depth = controller.request_depth()
            assert depth == unfinished, (
                f"{where}: request-queue depth {depth} != admitted-but-unfinished count "
                f"{unfinished}; a leaked or foreign slot was released"
            )

    # Coverage (asserted non-trivial): both surfaces, and a broad set of admitted and unadmitted
    # exit paths, were actually exercised across the run -- the property is not vacuous.
    assert surfaces_seen == {"chat", "embeddings"}, (
        f"seed={_SEED:#x}: both surfaces must be exercised, saw {sorted(surfaces_seen)}"
    )
    assert admitted_names_seen == set(_ADMITTED_NAMES), (
        f"seed={_SEED:#x}: not every admitted exit path was exercised, "
        f"missing {sorted(set(_ADMITTED_NAMES) - admitted_names_seen)}"
    )
    assert unadmitted_names_seen == set(_NOT_ADMITTED_NAMES), (
        f"seed={_SEED:#x}: not every pre-admission/shed exit path was exercised, "
        f"missing {sorted(set(_NOT_ADMITTED_NAMES) - unadmitted_names_seen)}"
    )


# --------------------------------------------------------------------------- #
# Integration: the clauses the schedule covers only statistically, pinned as examples.
# --------------------------------------------------------------------------- #


def test_real_shed_is_never_completed() -> None:
    """A genuine ``overload_shed`` from the real controller completes nothing (2.10).

    The property schedule stays at one-in-flight-at-a-time, where the real controller never sheds.
    Here the request queue is saturated by filling every slot directly (``admit`` without a
    matching ``complete``), so the next lifecycle request sheds ``overload_shed`` at the door. A
    shed request was never admitted, so it must never be completed, and the pre-occupied depth is
    unchanged by the shed.

    **Validates: Requirements 2.10**
    """

    async def _run() -> tuple[str, _CountingController, int, int]:
        controller = _CountingController(_controller())
        # Fill every request slot directly (no completes), so the queue is at its bound.
        for slot in range(_DEPTH):
            verdict = await controller.admit("other-org", f"filler-{slot}", 0.0)
            assert isinstance(verdict, Admitted), f"seed={_SEED:#x}: filler {slot} was not admitted"
        depth_before = controller.request_depth()
        completed_before = controller.total_completed
        # One more request through the real lifecycle: the full queue sheds it at the door.
        case = _Case(expect="overload_shed")
        outcome = await _run_step(controller, case, f"{_CONTENT} :: shed probe")
        return outcome, controller, depth_before, completed_before

    outcome, controller, depth_before, completed_before = asyncio.run(_run())
    assert outcome == "overload_shed", (
        f"seed={_SEED:#x}: a saturated real controller must shed, got {outcome!r}"
    )
    assert controller.total_completed == completed_before, (
        f"seed={_SEED:#x}: a shed request was completed "
        f"(completed={controller.completed!r}); a shed is never completed"
    )
    assert controller.request_depth() == depth_before, (
        f"seed={_SEED:#x}: the shed changed request depth "
        f"{depth_before} -> {controller.request_depth()}; a shed must leave the queue untouched"
    )


def _drive_once(case: _Case, content: str) -> tuple[str, _CountingController]:
    """Serve one request against a fresh real controller; return the outcome and the wrapper."""

    async def _run() -> tuple[str, _CountingController]:
        controller = _CountingController(_controller())
        outcome = await _run_step(controller, case, content)
        return outcome, controller

    return asyncio.run(_run())


def test_cancellation_mid_stream_completes_exactly_once() -> None:
    """A streaming request cancelled mid-flight releases its admission slot exactly once.

    **Validates: Requirements 2.9**
    """
    case = _Case(expect="cancelled", stream=True, provider="hang", cancel=True)
    outcome, controller = _drive_once(case, _CONTENT)
    assert outcome == "cancelled", f"seed={_SEED:#x}: expected a cancellation, got {outcome!r}"
    assert len(controller.admitted_ids) == 1, (
        f"seed={_SEED:#x}: expected exactly one admitted request, "
        f"got {controller.admitted_ids!r}"
    )
    request_id = controller.admitted_ids[0]
    assert controller.completed == {request_id: 1}, (
        f"seed={_SEED:#x}: a cancelled stream must complete its slot exactly once, "
        f"got {controller.completed!r}"
    )
    assert controller.request_depth() == 0, (
        f"seed={_SEED:#x}: a cancelled stream left request depth {controller.request_depth()}"
    )


def test_kill_switch_cut_completes_exactly_once() -> None:
    """A stream cut by the in-flight kill switch releases its admission slot exactly once.

    **Validates: Requirements 2.9**
    """
    case = _Case(expect=STREAM_KILLED, stream=True, kill=True)
    outcome, controller = _drive_once(case, _CONTENT)
    assert outcome == STREAM_KILLED, (
        f"seed={_SEED:#x}: expected a {STREAM_KILLED!r} cut, got {outcome!r}"
    )
    assert len(controller.admitted_ids) == 1, (
        f"seed={_SEED:#x}: expected exactly one admitted request, "
        f"got {controller.admitted_ids!r}"
    )
    request_id = controller.admitted_ids[0]
    assert controller.completed == {request_id: 1}, (
        f"seed={_SEED:#x}: a kill-switch cut must complete its slot exactly once, "
        f"got {controller.completed!r}"
    )
    assert controller.request_depth() == 0, (
        f"seed={_SEED:#x}: a kill-switch cut left request depth {controller.request_depth()}"
    )


#: An out-of-range REDACT finding the forcing detector reports whenever the probe marker is
#: present -- its span sits far beyond any request content, so the resolver's REDACT transformation
#: over it cannot be verified: ``apply_and_verify`` raises ``VerificationFailure`` and the request
#: fails closed to ``input_unmaskable`` AFTER it has been admitted and budget-spent. ``det`` is an
#: UNKNOWN detector so ``MinimalOutputResolver`` maps it to REDACT (a known class like ``card``
#: would map to BLOCK and give ``input_blocked`` instead). This mirrors the Group A fail-closed
#: test's forcing detector (``tests/edge/test_v2_leaks_a_failclosed_properties.py``).
_UNMASKABLE_PROBE = "forceunmaskable"
_FAR_START = 1_000_000
_FAR_END = _FAR_START + 11


def _unmaskable_detector(text: str) -> tuple[Finding, ...]:
    """One out-of-range REDACT finding when the probe marker is present, else nothing."""
    if _UNMASKABLE_PROBE not in text:
        return ()
    return (
        Finding(
            detector="det",
            detector_version="v2-leaks-d",
            category=Category.PII,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=_FAR_START, end=_FAR_END),),
            evidence=None,
        ),
    )


def test_embeddings_completed_and_refunded_on_zero_dispatch_refusals() -> None:
    """Embeddings is completed AND refunded on input BLOCK, scan failure and ``input_unmaskable``.

    Each refusal spends budget and then opens no provider, so the §10.4 lifecycle refunds the
    spend (C29, 2.17) and completes the admission slot (2.9), both exactly once. A refund-recording
    budget double observes the refund directly, and the counting controller observes the single
    ``complete``. ``input_unmaskable`` is reached through the real lifecycle by a forcing detector
    whose out-of-range REDACT finding cannot be verified against the outbound bytes.

    **Validates: Requirements 2.9, 2.17**
    """
    cases = {
        "input_block": (
            _Case(expect=REFUSE_INPUT_BLOCKED, surface="embeddings", detector="block"),
            _CONTENT,
        ),
        "scan_failure": (
            _Case(expect=REFUSE_INPUT_SCAN_FAILURE, surface="embeddings", detector="raise"),
            _CONTENT,
        ),
        "input_unmaskable": (
            _Case(expect=REFUSE_INPUT_UNMASKABLE, surface="embeddings"),
            f"please handle {_UNMASKABLE_PROBE} now",
        ),
    }
    for label, (case, content) in cases.items():
        refunds: list[int] = []
        detector = _unmaskable_detector if label == "input_unmaskable" else None

        async def _run(
            case: _Case = case,
            content: str = content,
            refunds: list[int] = refunds,
            detector: object = detector,
        ) -> tuple[str, _CountingController]:
            controller = _CountingController(_controller())
            clock = _Clock()
            provider = _Provider(case.provider, clock)
            budget = _RefundRecordingBudget(refunds=refunds)
            app = _app_with_budget(
                case,
                admission=controller,
                provider=provider,
                clock=clock,
                budget=budget,
                input_detector=detector,
            )
            outcome = await _serve(case, app, provider, content)
            return outcome, controller

        outcome, controller = asyncio.run(_run())
        where = f"seed={_SEED:#x} embeddings-{label} outcome={outcome!r}"
        assert outcome == case.expect, f"{where}: expected exit {case.expect!r}"
        assert len(controller.admitted_ids) == 1, f"{where}: admitted {controller.admitted_ids!r}"
        request_id = controller.admitted_ids[0]
        assert controller.completed == {request_id: 1}, (
            f"{where}: a zero-dispatch refusal must complete exactly once, "
            f"got {controller.completed!r}"
        )
        assert controller.request_depth() == 0, f"{where}: left request depth nonzero"
        assert refunds, f"{where}: budget spend was not refunded on a zero-dispatch refusal"


class _RefundRecordingBudget(_Budget):
    """A ``_Budget`` that records every refund so the zero-dispatch refund clause is observable."""

    def __init__(self, *, refunds: list[int]) -> None:
        super().__init__(refuse=False)
        self._refunds = refunds

    def refund(self, cost: int, *, request_id: str) -> None:
        del request_id
        self._refunds.append(cost)


def _app_with_budget(
    case: _Case,
    *,
    admission: _CountingController,
    provider: _Provider,
    clock: _Clock,
    budget: _RefundRecordingBudget,
    input_detector: Callable[[str], Sequence[Finding]] | None = None,
) -> ASGIApplication:
    """``_app`` with a refund-recording budget (and optionally a forcing input detector) injected.

    Mirrors the exploration test's ``_app`` exactly, but threads a budget that records refunds so
    the zero-dispatch refund clause (2.17) is asserted directly, and lets a caller override the
    input detector to drive the ``input_unmaskable`` fail-closed path.
    """
    detector = input_detector if input_detector is not None else _scanner(case.detector, CARD)
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=clock,
        control=_Control(kill=case.kill),
        error_scanner=_scanner(case.scanner, OUTPUT_CANARY),
        input_detector=detector,
        identity=ResolvingIdentity() if case.identity_ok else _NoIdentity(),
        snapshot_source=warm_snapshot_source() if case.plan_ok else unresolved_snapshot_source,
        admission=admission,
        quota=budget,
    )
