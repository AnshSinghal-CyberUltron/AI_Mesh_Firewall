"""v2-enforcement-leaks Group D (edge): every admitted request completes exactly once.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 7; design Property 1 (D) and
Property 9). Bug condition ``C_D``: an ``Admitted(request_id)`` event exits by any path. The
expected behaviour (2.9, 2.10, 2.12):

* an admitted chat (streaming and non-streaming) or embeddings request calls
  ``admission.complete(request_id)`` exactly once, on every exit path;
* a request refused before admission (pre-check, parse, identity, plan) or shed by a
  ``ShedVerdict`` is never completed;
* under the real :class:`AdmissionController` the exported request-queue depth returns to 0 after
  each of ``3 x request_depth`` sequential requests, and none is shed ``overload_shed``.

Everything is driven through the default boot path (:func:`gateway_v2.edge.app.build_app`), not
the dev profile, whose ASGI wrapper (``DevServedApp``) completes on response return and would
hide the bug. The admission seam is a counting double that IMPLEMENTS ``complete``: today the
``AdmissionDecider`` protocol has no ``complete`` and nothing calls it, so it counts 0.

**How each exit path is reached** (the nearest existing harness seam; nothing is monkeypatched):

* success / ``[DONE]`` -- a clean echo provider.
* error frame -- the provider emits an ``error_frame``. Non-streaming folds it into the body and
  answers 200; streaming withholds it fail-closed (the error-frame scanner raises) and ends in a
  terminal ``scan_failure`` ``Error_Frame``; embeddings refuses it as ``scan_failure``.
* budget refusal -- a budget double returning a ``BudgetVerdict``.
* input BLOCK -- an input detector emitting a ``card`` finding (BLOCK under
  ``MinimalOutputResolver``).
* scan failure -- an input detector that raises.
* output BLOCK -- non-streaming: the output scanner emits a ``card`` finding over the collected
  content. Streaming: the pass-through plan has no enforcing output rule, so the nearest seam is
  a provider error frame whose scan resolves BLOCK (terminal ``output_blocked``). Embeddings has
  no output scan, so it has no output-BLOCK exit.
* raised exception -- the client ``send`` raises ``ConnectionResetError`` on
  ``http.response.start`` (the response cannot be written), which escapes ``handle``.
* cancellation -- the provider signals it was opened and then never yields; the handler task is
  cancelled (``CancelledError`` on disconnect).
* kill-switch cut -- the in-flight control source reports the kill switch engaged; the stream is
  cut before its first chunk and ends in ``stream_killed`` (streaming only).
* stream timeout -- the C27 Idle_Timeout, driven by the injected clock: the provider advances the
  route clock past ``idle_timeout_s`` between two chunks, so the second write is cut with
  ``stream_idle_timeout`` (streaming only). The wall-clock Inter_Chunk_Timeout is the same terminal
  site (``_run_attempts`` swallows ``StreamTimeout``) and is not driven here, to keep the test
  off the wall clock; task 30.1's property covers it.

Each admitted case is split in two: an unmarked test asserting the exit path is really reached
(it holds today), and a strict-xfail test asserting exactly one ``complete``. The split keeps the
xfail honest -- it can only fail on the completion count, never on a mis-wired path.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict, budget_verdict
from gateway_v2.admit.grant import shed_verdict as make_shed_verdict
from gateway_v2.admit.metrics import AdmissionMetrics, ShedReason
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.posture import OVERLOAD_SHED, STREAM_IDLE_TIMEOUT, STREAM_KILLED
from gateway_v2.edge.app import ASGIApplication, build_app
from gateway_v2.edge.cancel import InFlightSnapshot
from gateway_v2.edge.chat_deps import AdmissionDecider, unresolved_snapshot_source
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_chat import (
    REFUSE_BUDGET_UNAVAILABLE,
    REFUSE_IDENTITY_UNRESOLVED,
    REFUSE_INPUT_BLOCKED,
    REFUSE_INPUT_SCAN_FAILURE,
    REFUSE_OUTPUT_BLOCKED,
    REFUSE_PLAN_UNAVAILABLE,
    REFUSE_PRE_CHECK,
)
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    ResolvingIdentity,
    chat_body,
    warm_snapshot_source,
)

#: An input value the input detector flags with a ``card`` finding (BLOCK).
CARD = "4111111111111111"
#: An output value the output scanner flags with a ``card`` finding (BLOCK).
OUTPUT_CANARY = "OUTPUT-CANARY-7731"
#: Clock jump between two chunks: past ``idle_timeout_s`` (3 s), well under
#: ``max_stream_duration_s`` (300 s), so only the Idle_Timeout fires.
_IDLE_JUMP_S = 10.0
#: Request-queue depth for the real-controller run (kept small; the run is 3x this).
_REAL_DEPTH = 4


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


# --------------------------------------------------------------------------- #
# Doubles.
# --------------------------------------------------------------------------- #


class _Clock:
    """The injected route clock. Fixed unless a provider advances it (the idle-timeout path)."""

    __slots__ = ("t",)

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _CountingAdmission:
    """An admission double that admits (or sheds) and COUNTS ``complete`` per request id."""

    __slots__ = ("admitted", "completed", "_shed")

    def __init__(self, *, shed: bool = False) -> None:
        self.admitted: list[str] = []
        self.completed: list[str] = []
        self._shed = shed

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        self.admitted.append(request_id)
        if self._shed:
            return make_shed_verdict(random.Random(0), request_id)
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)

    def complete(self, request_id: str) -> None:
        self.completed.append(request_id)


class _Budget:
    __slots__ = ("_refuse",)

    def __init__(self, *, refuse: bool) -> None:
        self._refuse = refuse

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        if self._refuse:
            return budget_verdict(request_id)
        return Admitted(owner_id=DEFAULT_ORG_ID, request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _NoIdentity:
    __slots__ = ()

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return None


class _Control:
    __slots__ = ("_kill",)

    def __init__(self, *, kill: bool) -> None:
        self._kill = kill

    def snapshot(self) -> InFlightSnapshot:
        return InFlightSnapshot(
            available=True,
            age_s=0.0,
            kill_switch_engaged=self._kill,
            key_revoked=False,
            plan_changed=False,
        )


def _card_findings(text: str, needle: str) -> tuple[Finding, ...]:
    if needle not in text:
        return ()
    start = text.index(needle)
    return (
        Finding(
            detector="card",
            detector_version="v2-leaks-d",
            category=Category.CREDENTIAL,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=start, end=start + len(needle)),),
            evidence=None,
        ),
    )


def _scanner(mode: str, needle: str) -> Callable[[str], Sequence[Finding]]:
    """``clean`` finds nothing; ``block`` flags ``needle`` as ``card``; ``raise`` raises."""

    def _scan(text: str) -> Sequence[Finding]:
        if mode == "raise":
            raise RuntimeError("v2-leaks-d: scanner unavailable")
        if mode == "block":
            return _card_findings(text, needle)
        return ()

    return _scan


class _Provider:
    """A scripted provider; ``opened`` is set the moment a body is first read."""

    def __init__(self, kind: str, clock: _Clock) -> None:
        self.kind = kind
        self.clock = clock
        self.opened = asyncio.Event()

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.opened.set()
        raw = request.body.get(BODY_DELTAS_KEY, ())
        deltas = tuple(raw) if isinstance(raw, tuple) else ()
        if self.kind == "hang":
            await asyncio.Event().wait()
        if self.kind == "error_frame":
            yield UpstreamEvent(text_deltas=(), final=True, error_frame="upstream exploded")
            return
        if self.kind == "output_canary_frame":
            yield UpstreamEvent(text_deltas=(), final=True, error_frame=f"see {OUTPUT_CANARY}")
            return
        if self.kind == "vectors":
            for index in range(len(deltas)):
                vector = json.dumps([float(index), 0.5])
                yield UpstreamEvent(
                    text_deltas=(("embeddings_input", vector),), final=index == len(deltas) - 1
                )
            return
        if self.kind == "output_canary":
            yield UpstreamEvent(text_deltas=(("content", f"here: {OUTPUT_CANARY}"),), final=True)
            return
        if self.kind == "idle_stall":
            yield UpstreamEvent(text_deltas=(("content", "first "),), final=False)
            self.clock.t += _IDLE_JUMP_S
            yield UpstreamEvent(text_deltas=(("content", "second"),), final=True)
            return
        yield UpstreamEvent(text_deltas=(("content", "hello there"),), final=True)

    async def abort(self) -> None:
        return None


class _Metrics:
    __slots__ = ()

    def exposition(self) -> str:
        return "# v2-leaks-d\n"


# --------------------------------------------------------------------------- #
# Scenarios.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Case:
    """One exit path. ``expect`` is the observed outcome token the path must produce."""

    expect: str
    surface: str = "chat"
    stream: bool = False
    body: str = "ok"  # ok | malformed | oversized
    identity_ok: bool = True
    plan_ok: bool = True
    shed: bool = False
    budget_refuse: bool = False
    detector: str = "clean"  # input detector: clean | block | raise
    provider: str = "echo"
    scanner: str = "clean"  # output / error-frame scanner: clean | block | raise
    kill: bool = False
    send_raises: bool = False
    cancel: bool = False


# Admitted exit paths: each must complete exactly once (Property 9).
_ADMITTED: dict[str, _Case] = {
    "chat-json-success": _Case(expect="ok"),
    "chat-json-error_frame": _Case(expect="ok", provider="error_frame"),
    "chat-json-budget_refusal": _Case(expect=REFUSE_BUDGET_UNAVAILABLE, budget_refuse=True),
    "chat-json-input_block": _Case(expect=REFUSE_INPUT_BLOCKED, detector="block"),
    "chat-json-scan_failure": _Case(expect=REFUSE_INPUT_SCAN_FAILURE, detector="raise"),
    "chat-json-output_block": _Case(
        expect=REFUSE_OUTPUT_BLOCKED, provider="output_canary", scanner="block"
    ),
    "chat-json-raised_exception": _Case(expect="raised:ConnectionResetError", send_raises=True),
    "chat-json-cancellation": _Case(expect="cancelled", provider="hang", cancel=True),
    "chat-stream-done": _Case(expect="done", stream=True),
    "chat-stream-error_frame": _Case(
        expect=REFUSE_INPUT_SCAN_FAILURE, stream=True, provider="error_frame", scanner="raise"
    ),
    "chat-stream-budget_refusal": _Case(
        expect=REFUSE_BUDGET_UNAVAILABLE, stream=True, budget_refuse=True
    ),
    "chat-stream-input_block": _Case(expect=REFUSE_INPUT_BLOCKED, stream=True, detector="block"),
    "chat-stream-scan_failure": _Case(
        expect=REFUSE_INPUT_SCAN_FAILURE, stream=True, detector="raise"
    ),
    "chat-stream-output_block": _Case(
        expect=REFUSE_OUTPUT_BLOCKED, stream=True, provider="output_canary_frame", scanner="block"
    ),
    "chat-stream-raised_exception": _Case(
        expect="raised:ConnectionResetError", stream=True, send_raises=True
    ),
    "chat-stream-cancellation": _Case(
        expect="cancelled", stream=True, provider="hang", cancel=True
    ),
    "chat-stream-kill_switch": _Case(expect=STREAM_KILLED, stream=True, kill=True),
    "chat-stream-timeout": _Case(expect=STREAM_IDLE_TIMEOUT, stream=True, provider="idle_stall"),
    "embeddings-success": _Case(expect="ok", surface="embeddings", provider="vectors"),
    "embeddings-error_frame": _Case(
        expect=REFUSE_INPUT_SCAN_FAILURE, surface="embeddings", provider="error_frame"
    ),
    "embeddings-budget_refusal": _Case(
        expect=REFUSE_BUDGET_UNAVAILABLE, surface="embeddings", budget_refuse=True
    ),
    "embeddings-input_block": _Case(
        expect=REFUSE_INPUT_BLOCKED, surface="embeddings", detector="block"
    ),
    "embeddings-scan_failure": _Case(
        expect=REFUSE_INPUT_SCAN_FAILURE, surface="embeddings", detector="raise"
    ),
    "embeddings-raised_exception": _Case(
        expect="raised:ConnectionResetError",
        surface="embeddings",
        provider="vectors",
        send_raises=True,
    ),
    "embeddings-cancellation": _Case(
        expect="cancelled", surface="embeddings", provider="hang", cancel=True
    ),
}

# Refused before admission, or shed by a ShedVerdict: must never complete (2.10).
_NOT_ADMITTED: dict[str, _Case] = {
    "chat-json-pre_check": _Case(expect=REFUSE_PRE_CHECK, body="oversized"),
    "chat-json-parse": _Case(expect="malformed", body="malformed"),
    "chat-json-identity": _Case(expect=REFUSE_IDENTITY_UNRESOLVED, identity_ok=False),
    "chat-json-plan": _Case(expect=REFUSE_PLAN_UNAVAILABLE, plan_ok=False),
    "chat-json-shed": _Case(expect=OVERLOAD_SHED, shed=True),
    "chat-stream-identity": _Case(
        expect=REFUSE_IDENTITY_UNRESOLVED, stream=True, identity_ok=False
    ),
    "chat-stream-plan": _Case(expect=REFUSE_PLAN_UNAVAILABLE, stream=True, plan_ok=False),
    "chat-stream-shed": _Case(expect=OVERLOAD_SHED, stream=True, shed=True),
    "embeddings-parse": _Case(expect="malformed", surface="embeddings", body="malformed"),
    "embeddings-identity": _Case(
        expect=REFUSE_IDENTITY_UNRESOLVED, surface="embeddings", identity_ok=False
    ),
    "embeddings-plan": _Case(expect=REFUSE_PLAN_UNAVAILABLE, surface="embeddings", plan_ok=False),
    "embeddings-shed": _Case(expect=OVERLOAD_SHED, surface="embeddings", shed=True),
}


def _body(case: _Case, content: str) -> bytes:
    if case.body == "malformed":
        return b"{not json"
    if case.body == "oversized":
        return b" " * (_contract().max_request_bytes() + 1)
    if case.surface == "embeddings":
        return json.dumps({"model": "echo-1", "input": [content]}).encode()
    return chat_body(content=content, stream=case.stream)


def _path(case: _Case) -> str:
    return "/v1/embeddings" if case.surface == "embeddings" else "/v1/chat/completions"


def _app(
    case: _Case,
    *,
    admission: AdmissionDecider,
    provider: _Provider,
    clock: _Clock,
) -> ASGIApplication:
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=clock,
        control=_Control(kill=case.kill),
        error_scanner=_scanner(case.scanner, OUTPUT_CANARY),
        input_detector=_scanner(case.detector, CARD),
        identity=ResolvingIdentity() if case.identity_ok else _NoIdentity(),
        snapshot_source=warm_snapshot_source() if case.plan_ok else unresolved_snapshot_source,
        admission=admission,
        quota=_Budget(refuse=case.budget_refuse),
    )


def _outcome(case: _Case, sent: Sequence[Mapping[str, object]]) -> str:
    """Reduce the recorded ASGI sends to one token: ok | done | <error code> | malformed."""
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        bytes(chunk)
        for m in sent
        if m["type"] == "http.response.body"
        and isinstance(chunk := m.get("body", b""), (bytes, bytearray))
    )
    if case.stream and case.body == "ok":
        if b"data: [DONE]" in body:
            return "done"
        for line in body.decode().splitlines():
            if line.startswith("data: {"):
                error = json.loads(line[len("data: ") :]).get("error")
                if isinstance(error, dict):
                    return str(error.get("code"))
        return f"stream-without-terminal:{body!r}"
    if status == 200:
        return "ok"
    error = json.loads(body).get("error", {})
    code = str(error.get("code"))
    return "malformed" if code.startswith("malformed") else code


async def _serve(
    case: _Case,
    app: ASGIApplication,
    provider: _Provider,
    content: str,
) -> str:
    sent: list[Mapping[str, object]] = []
    incoming: list[Mapping[str, object]] = [
        {"type": "http.request", "body": _body(case, content), "more_body": False},
    ]
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": _path(case),
        "headers": [(b"content-type", b"application/json")],
    }

    async def _receive() -> Mapping[str, object]:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def _send(message: Mapping[str, object]) -> None:
        if case.send_raises and message["type"] == "http.response.start":
            raise ConnectionResetError("v2-leaks-d: client went away")
        sent.append(dict(message))

    task = asyncio.ensure_future(app(scope, _receive, _send))
    if case.cancel:
        await asyncio.wait_for(provider.opened.wait(), timeout=5.0)
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return "cancelled"
    except Exception as exc:  # noqa: BLE001 -- the raised-exception exit path is observed here.
        return f"raised:{type(exc).__name__}"
    return _outcome(case, sent)


def _drive(case: _Case) -> tuple[str, _CountingAdmission]:
    async def _run() -> tuple[str, _CountingAdmission]:
        clock = _Clock()
        admission = _CountingAdmission(shed=case.shed)
        provider = _Provider(case.provider, clock)
        app = _app(case, admission=admission, provider=provider, clock=clock)
        outcome = await _serve(case, app, provider, f"pay with {CARD} please")
        return outcome, admission

    return asyncio.run(_run())


# --------------------------------------------------------------------------- #
# Admitted exit paths (Property 9).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(_ADMITTED))
def test_admitted_exit_path_is_reached(name: str) -> None:
    """Harness check: each admitted case is admitted once and exits by its named path.

    Holds on the unfixed tree. It keeps the strict xfail below honest: that test can then only
    fail on the completion count, never because the scenario took a different path.
    """
    case = _ADMITTED[name]
    outcome, admission = _drive(case)
    assert outcome == case.expect, f"{name}: expected exit {case.expect!r}, observed {outcome!r}"
    assert len(admission.admitted) == 1, f"{name}: admitted {admission.admitted!r}"


@pytest.mark.parametrize("name", list(_ADMITTED))
def test_admitted_request_completes_exactly_once(name: str) -> None:
    """Every admitted request calls ``complete`` exactly once, for its own id (Property 9).

    **Validates: Requirements 2.9**
    """
    case = _ADMITTED[name]
    _outcome_token, admission = _drive(case)
    assert len(admission.admitted) == 1
    request_id = admission.admitted[0]
    assert admission.completed == [request_id], (
        f"{name}: admitted {request_id!r}, exit {case.expect!r}; "
        f"complete calls {admission.completed!r} (expected exactly one)"
    )


# --------------------------------------------------------------------------- #
# Pre-admission refusals and sheds (2.10) -- hold today, unmarked.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(_NOT_ADMITTED))
def test_unadmitted_request_is_never_completed(name: str) -> None:
    """A request refused before admission, or shed, is never completed (Property 9).

    **Validates: Requirements 2.10**
    """
    case = _NOT_ADMITTED[name]
    outcome, admission = _drive(case)
    assert outcome == case.expect, f"{name}: expected exit {case.expect!r}, observed {outcome!r}"
    assert len(admission.admitted) == (1 if case.shed else 0), f"{name}: {admission.admitted!r}"
    assert admission.completed == [], f"{name}: complete called {admission.completed!r}"


# --------------------------------------------------------------------------- #
# Real AdmissionController: depth returns to 0, no overload_shed (2.12).
# --------------------------------------------------------------------------- #

_STEADY_CYCLE = (
    _Case(expect="ok"),
    _Case(expect="done", stream=True),
    _Case(expect="ok", surface="embeddings", provider="vectors"),
)


def test_real_controller_depth_returns_to_zero_under_steady_load() -> None:
    """Sequential chat (json + stream) and embeddings requests never accumulate slots.

    ``3 x request_depth`` requests run one after another; after each one the request-queue depth
    is 0 (no admitted request is unfinished) and nothing is shed.

    **Validates: Requirements 2.9, 2.12**
    """
    metrics = AdmissionMetrics()
    controller = AdmissionController(
        bounds=AdmissionBounds(
            concurrency=_REAL_DEPTH,
            request_depth=_REAL_DEPTH,
            guard_depth=_REAL_DEPTH,
            dispatch_depth=_REAL_DEPTH,
            egress_depth=_REAL_DEPTH,
            audit_depth=_REAL_DEPTH,
        ),
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=metrics,
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )

    async def _run() -> list[tuple[int, str, str, int]]:
        trace: list[tuple[int, str, str, int]] = []
        for index in range(3 * _REAL_DEPTH):
            case = _STEADY_CYCLE[index % len(_STEADY_CYCLE)]
            clock = _Clock()
            provider = _Provider(case.provider, clock)
            app = _app(case, admission=controller, provider=provider, clock=clock)
            outcome = await _serve(case, app, provider, f"steady request {index}")
            depth = controller.queue_report(now=0.0).depth("request")
            trace.append((index + 1, case.surface, outcome, depth))
        return trace

    trace = asyncio.run(_run())
    for number, surface, outcome, depth in trace:
        expected = _STEADY_CYCLE[(number - 1) % len(_STEADY_CYCLE)].expect
        assert outcome == expected, f"request {number} ({surface}) exit {outcome!r}: {trace}"
        assert depth == 0, f"request {number} ({surface}) left request depth {depth}: {trace}"
    reading = metrics.snapshot()
    assert reading.shed_for(ShedReason.QUEUE_FULL) == 0, f"overload_shed occurred: {trace}"
    assert len(controller._in_flight) == 0, f"in-flight records left: {trace}"
