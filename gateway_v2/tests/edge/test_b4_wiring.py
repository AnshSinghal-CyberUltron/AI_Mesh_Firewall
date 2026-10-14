# Feature: provider-egress-gw-b4
# Validates: Requirements 5.1, 5.2, 5.4, 7.1, 7.2, 7.3
"""B4 §6.2 — boot/wiring INTEGRATION tests for the provider injection seam (GW17-18).

Proves the real :class:`~gateway_v2.dispatch.http_provider.HttpProviderClient` is a
DROP-IN through the shipped :func:`gateway_v2.edge.app.build_app` ``provider=`` seam
WITHOUT any signature change (R7.1), that the shipped
:func:`gateway_v2.edge.serve.build_boot_dependencies` keeps the socket-free
:class:`~gateway_v2.dispatch.provider.StubProviderClient` as the honest-boot default
(R7.2), that a well-formed request's GENERATION egress actually flows THROUGH the
injected ``HttpProviderClient`` to the ``Fake_Upstream`` (R7.1 / R7.3), and that the
C43 :class:`~gateway_v2.dispatch.guard_routing.GuardRouter` guard-egress seam is
exercisable alongside the generation seam with the owner-queue bound taken from the
contract and NO detector present (R5.1 / R5.2 / R5.4).

These are INTEGRATION tests (the design's PBT prework classifies ``build_app`` wiring as
integration, not property): a handful of representative examples each, driven END TO END
against the shipped harness. The whole module ``pytest.importorskip``s ``httpx`` (an
optional ``conformance``-extra dep the real client + the in-process transport need) so a
tree without the extra skips cleanly rather than erroring collection; the full stock-SDK
drive additionally ``importorskip``s ``openai``.

How the generation seam is driven THROUGH the real client (not faked). The injected
``HttpProviderClient`` is wired exactly as the §5.3 integration tests wire it —
``client_factory=fake.client_factory`` (an ``httpx.MockTransport`` over the
``Fake_Upstream``, socket-free), ``decoder_factory=real_decoder_factory()`` (the REAL
``edge/wire/sse.py SSEDecoder``), ``clock=DeterministicClock()`` — and injected into the
GENUINE app assembled by ``build_app`` with the PASSING §10.4 lifecycle deps the GW15/B3
edge tests share (:class:`ResolvingIdentity`, :func:`warm_snapshot_source`,
:class:`AdmittingAdmission`, :class:`AdmittingBudget`), so a well-formed request reaches
the provider. The ``Fake_Upstream`` records every request it saw (``requests_seen``), so
the test asserts the generation egress genuinely went through ``HttpProviderClient`` to
the ``Fake_Upstream`` and the scripted completion streamed back. Nothing is mocked at the
seam — the real client drains the real decoder over httpx's genuine streaming machinery.

Async marking. The repo runs ``pytest-asyncio`` in its default strict mode (no
``asyncio_mode`` is set), so each async test carries an explicit marker — this module sets
``pytestmark = pytest.mark.asyncio`` exactly like ``test_gw15_chat_surface_v2.py``.

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.dispatch`` / ``gateway_v2.edge`` and the ``tests.dispatch._b4_fakes`` /
``tests.edge._chat_lifecycle_fixtures`` helpers here is allowed.

_Design: Testing Strategy (integration — build_app wiring); Components §C5 (generation-vs-
guard split), §C6 (``build_app`` injection seam)._
"""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")  # real client + in-process transport (R2.2)

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.dispatch.guard_routing import (  # noqa: E402
    GuardLoad,
    GuardRouter,
    LoadSnapshot,
)
from gateway_v2.dispatch.http_provider import HttpProviderClient  # noqa: E402
from gateway_v2.dispatch.provider import (  # noqa: E402
    ProviderClient,
    StubProviderClient,
)
from gateway_v2.edge import serve  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.kinds import HardwareSignals  # noqa: E402
from gateway_v2.runtime.resources import ResourceContract, from_signals  # noqa: E402
from tests.dispatch._b4_fakes import (  # noqa: E402
    DeterministicClock,
    FakeGuardPool,
    FakeUpstream,
    real_decoder_factory,
    sse_content_frame,
)
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    ResolvingIdentity,
    warm_snapshot_source,
)

pytestmark = pytest.mark.asyncio

_BASE_URL = "http://b4-wiring.test/v1"
_API_KEY = "zs-b4-wiring-key"
_MODEL = "gpt-4o-mini"


# --------------------------------------------------------------------------- #
# Shared fixtures: contract + the honest in-process support deps.
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (the shared ``from_signals`` fixture shape the edge tests use)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


class _StubMetrics:
    """A minimal metrics registry: a fixed text exposition (the chat path never reads it)."""

    __slots__ = ()

    def exposition(self) -> str:
        return "# gateway_v2 b4-wiring metrics\n"


class _Clock:
    """A monotonic in-process clock (seconds), advanced on each read, never real."""

    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


def _http_provider(fake: FakeUpstream) -> HttpProviderClient:
    """Wire the REAL ``HttpProviderClient`` against ``fake`` with the real decoder.

    Byte-for-byte the §5.3 integration wiring: the fake's BOUND ``client_factory``
    (an ``httpx.MockTransport`` over the ``Fake_Upstream``, socket-free), the REAL
    ``edge/wire/sse.py SSEDecoder`` via ``real_decoder_factory``, and a
    ``DeterministicClock`` (retained by the client for parity; never read). Nothing
    is mocked AT the seam — the real client drains the real decoder.
    """
    return HttpProviderClient(
        client_factory=fake.client_factory,
        decoder_factory=real_decoder_factory(),
        clock=DeterministicClock(),
    )


def _build_app_with_provider(provider: ProviderClient) -> object:
    """Assemble the GENUINE ASGI app with PASSING lifecycle deps + the injected ``provider``.

    The identity / plan-snapshot / admission / budget deps all PASS (so a well-formed
    request reaches the provider), exactly the shape ``test_gw15_chat_surface_v2.py``
    uses — only the ``provider`` differs: here it is the REAL ``HttpProviderClient``
    (or whatever the caller injects) through the SAME ``build_app(provider=...)`` seam,
    with NO change to ``build_app``'s signature (R7.1).
    """
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=_StubMetrics(),
        clock=_Clock(),
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
    )


# --------------------------------------------------------------------------- #
# R7.1 — drop-in: build_app(provider=HttpProviderClient(...)) assembles a callable
#         ASGI app with NO build_app signature change.
# --------------------------------------------------------------------------- #


async def test_http_provider_is_drop_in_through_build_app_seam() -> None:
    """``build_app(provider=HttpProviderClient(...))`` assembles a callable ASGI 3 app (R7.1).

    The real client is injected through the SAME ``provider=`` keyword the stub uses, with
    the shipped ``edge/wire/sse.py SSEDecoder`` as its ``decoder_factory``. ``build_app``'s
    signature is unchanged — the real client drops in behind the shipped
    ``@runtime_checkable`` ``ProviderClient`` protocol — and the result is a genuine ASGI 3
    callable (``async def app(scope, receive, send)``).
    """
    fake = FakeUpstream((sse_content_frame(("content", "x")),))
    provider = _http_provider(fake)

    # The real client is a drop-in behind the shipped protocol (no seam change).
    assert isinstance(provider, ProviderClient)

    app = _build_app_with_provider(provider)

    # An ASGI 3 application: a 3-arg ``async def app(scope, receive, send)`` callable.
    assert callable(app)
    import inspect

    assert inspect.iscoroutinefunction(app), "build_app did not return an ASGI 3 coroutine callable"
    sig = inspect.signature(app)
    assert len(sig.parameters) == 3, f"ASGI 3 callable must take (scope, receive, send): {sig}"


# --------------------------------------------------------------------------- #
# R7.1 / R7.3 — generation egress streams THROUGH the real client to the Fake_Upstream.
# --------------------------------------------------------------------------- #


def _stock_client(app: object) -> object:
    """The UNMODIFIED stock ``openai.AsyncOpenAI`` — only ``base_url`` + key + transport swapped.

    Mirrors ``test_gw15_chat_surface_v2.py::_stock_client`` exactly: the SDK is pointed at
    the in-process app over httpx's own ``ASGITransport`` (no socket), with no
    request/response shimming. Imported lazily so this helper is only reached once ``openai``
    is confirmed present by the caller's ``importorskip``.
    """
    import openai

    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://b4-wiring.test")
    return openai.AsyncOpenAI(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        http_client=http_client,
        max_retries=0,
    )


async def _collect_stream_content(stream: object) -> str:
    """Concatenate the ``choices[].delta.content`` of every chunk the SDK parsed."""
    content = ""
    async for chunk in stream:  # type: ignore[attr-defined]
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            content += chunk.choices[0].delta.content
    return content


async def test_generation_egress_streams_through_http_provider_to_fake_upstream() -> None:
    """A streaming chat request reaches ``Fake_Upstream`` via ``HttpProviderClient`` (R7.1/R7.3).

    Drives the UNMODIFIED stock OpenAI SDK (base-url swap only) end to end over an in-process
    ``ASGITransport`` against the genuine ``build_app`` output, with the REAL
    ``HttpProviderClient`` injected as the provider and a ``Fake_Upstream`` scripting the
    completion. The generation egress genuinely flows through the real client to the fake
    (``fake.requests_seen >= 1``) and the scripted content streams back to the SDK — proving
    the provider seam is wired AND reached, not faked. ``openai`` is importorskipped here (the
    full stock-SDK path needs it; a tree without it skips this one test cleanly).
    """
    pytest.importorskip("openai")

    frames = (
        sse_content_frame(("content", "Hello, ")),
        sse_content_frame(("content", "wörld ")),
        sse_content_frame(("content", "🚀!")),
    )
    fake = FakeUpstream(frames)
    provider = _http_provider(fake)
    client = _stock_client(_build_app_with_provider(provider))
    try:
        stream = await client.chat.completions.create(  # type: ignore[attr-defined]
            model=_MODEL,
            messages=[{"role": "user", "content": "Stream a greeting."}],
            stream=True,
        )
        content = await _collect_stream_content(stream)
    finally:
        await client.close()  # type: ignore[attr-defined]

    # The generation egress genuinely went THROUGH HttpProviderClient to the Fake_Upstream.
    # The destination is the one the shipped ``DispatchRouter`` derived from the plan (an
    # ``amf://upstream/...`` routing selection), carried into ``UpstreamRequest.destination`` and
    # opened by the real client — not a test constant; the ``MockTransport`` answers it regardless
    # of host (socket-free), so reaching the fake is the proof the egress went through the client.
    assert fake.requests_seen >= 1, "generation egress did not reach the Fake_Upstream"
    assert isinstance(fake.last_request_url, str) and fake.last_request_url, (
        "the real client opened no stream to UpstreamRequest.destination"
    )
    # The scripted completion streamed back through the real client + real decoder to the SDK.
    assert content == "Hello, wörld 🚀!", f"streamed completion mismatch: {content!r}"


# --------------------------------------------------------------------------- #
# R7.2 — the shipped serve default is StubProviderClient (no real socket built).
# --------------------------------------------------------------------------- #


async def test_serve_default_provider_is_stub_with_no_real_socket() -> None:
    """``build_boot_dependencies`` keeps the socket-free ``StubProviderClient`` default (R7.2).

    The honest-boot default is the in-process stub — a deployment that wants real generation
    injects ``HttpProviderClient`` explicitly through the SAME seam (proven above), but the
    shipped boot never constructs a real socket. The stub's only state is the scripted replay
    + the abort latch (no socket-bearing field), so no real client was built at boot.
    """
    deps, _logs = serve.build_boot_dependencies(contract=_contract())
    provider = deps["provider"]

    assert isinstance(provider, StubProviderClient), "serve default is not the in-process stub"
    assert not isinstance(provider, HttpProviderClient), (
        "serve default is the real HTTP client; it must stay the socket-free stub (R7.2)"
    )
    assert isinstance(provider, ProviderClient), "the stub does not satisfy ProviderClient"
    # No real socket: the stub carries only the scripted replay + the abort latch.
    assert set(StubProviderClient.__slots__) == {"_aborted", "rng", "script"}, (
        "StubProviderClient grew socket-bearing state (a real client would)"
    )


# --------------------------------------------------------------------------- #
# R5.1 / R5.2 / R5.4 — the guard-egress seam is exercisable alongside the generation
#         seam, with the owner-queue bound taken FROM the contract and no detector.
# --------------------------------------------------------------------------- #


def _min_tie_break(candidates: tuple[str, ...]) -> str:
    """A deterministic choice-among-equals (lexicographic min); unused when waits are distinct."""
    return min(candidates)


async def test_guard_router_uses_contract_bound_and_seam_dispatches_with_no_detector() -> None:
    """A ``GuardRouter`` on ``contract.owner_queue_bound_s()`` selects + dispatches (R5.1/5.2/5.4).

    Proves the GUARD egress seam is exercisable ALONGSIDE the generation seam at the
    wiring/contract-bound level: the owner-queue bound comes from
    ``contract.owner_queue_bound_s()`` (the ``edge`` wiring site passes it in — NO literal in
    ``dispatch``, R9.3), the ``GuardRouter`` selects the min-queue-wait member of a
    ``FakeGuardPool``, and the selected destination is dispatched back to the matching
    ``FakeGuard`` — the self-hosted guard tier reached without any GW07/GW08 detector model
    present (R5.4). The generation seam (``HttpProviderClient``) and the guard seam
    (``GuardRouter`` → ``FakeGuard``) are the two distinct upstreams of the split.
    """
    contract = _contract()
    bound_s = contract.owner_queue_bound_s()
    # The bound is a real, positive, contract-derived value in seconds (not a literal here).
    assert bound_s > 0.0, "contract.owner_queue_bound_s() must be a positive seconds bound"

    pool = FakeGuardPool(["guard-a", "guard-b"])
    router = GuardRouter(owner_queue_bound_s=bound_s, tie_break=_min_tie_break)
    # Both within the bound; guard-b's smaller owner-queue wait is the min-wait winner.
    snapshot = LoadSnapshot(
        guards=(
            GuardLoad(destination="guard-a", owner_queue_wait_s=bound_s * 0.75, queue_depth=4),
            GuardLoad(destination="guard-b", owner_queue_wait_s=bound_s * 0.25, queue_depth=1),
        ),
    )

    selected = router.select(snapshot)
    assert selected == "guard-b", "GuardRouter did not select the min-queue-wait guard"

    # The guard-egress seam reaches exactly the selected self-hosted guard, no detector.
    dispatched = pool.dispatch(selected)
    assert dispatched is pool.get("guard-b")
    assert pool.get("guard-b").hits == 1
    # Routing picked EXACTLY one guard — the loser was never hit.
    assert pool.get("guard-a").hits == 0


async def test_generation_and_guard_seams_coexist_in_one_wiring() -> None:
    """Both the generation seam and the guard seam are wired + exercisable together (R5.1/5.2/5.4).

    The generation upstream (the injected ``HttpProviderClient`` behind ``build_app``) and the
    guard upstream (the ``GuardRouter``-selected ``FakeGuard`` reached with the contract bound)
    are STRUCTURALLY DISTINCT and both exercisable in the same wiring — the generation-vs-guard
    split. The guard tier is a reachable self-hosted stand-in (never a platform-owned external
    AI), and the selection + dispatch happen with no detector model present.
    """
    contract = _contract()

    # Generation seam: the real client is a drop-in provider; the app assembles callable.
    fake = FakeUpstream((sse_content_frame(("content", "ok")),))
    provider = _http_provider(fake)
    app = _build_app_with_provider(provider)
    assert callable(app)
    assert isinstance(provider, ProviderClient)

    # Guard seam: a DISTINCT upstream, selected over the contract-derived owner-queue bound.
    pool = FakeGuardPool(["guard-x", "guard-y", "guard-z"])
    router = GuardRouter(
        owner_queue_bound_s=contract.owner_queue_bound_s(), tie_break=_min_tie_break
    )
    snapshot = LoadSnapshot(
        guards=(
            GuardLoad(destination="guard-x", owner_queue_wait_s=0.030, queue_depth=9),
            GuardLoad(destination="guard-y", owner_queue_wait_s=0.004, queue_depth=0),
            GuardLoad(destination="guard-z", owner_queue_wait_s=0.012, queue_depth=3),
        ),
    )
    selected = router.select(snapshot)
    assert selected == "guard-y", "guard-y has the min owner-queue wait within the bound"

    guard = pool.dispatch(selected, {"prompt": "scan me"})
    assert guard.hits == 1
    assert guard.last_payload == {"prompt": "scan me"}
    # The two seams are distinct: the generation fake was NOT hit by the guard dispatch.
    assert fake.requests_seen == 0, "the guard-egress seam must not touch the generation upstream"
