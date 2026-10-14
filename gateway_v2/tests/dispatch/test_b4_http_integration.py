# Feature: provider-egress-gw-b4, Property 2: Terminal final-flag is set exactly on the last event  # noqa: E501
# Validates: Requirements 1.4
"""B4 §5.2 (Property 2, PBT) + §5.3 (integration examples) for ``HttpProviderClient``.

Drives the shipped real :class:`~gateway_v2.dispatch.http_provider.HttpProviderClient`
end-to-end against the DONE :mod:`tests.dispatch._b4_fakes` harness — the
``FakeUpstream`` speaks the ``Generation_Provider``'s OpenAI SSE shape over an
``httpx.MockTransport`` (no socket), the client drains it through httpx's genuine
``aiter_bytes`` / ``aclose`` machinery, and the REAL ``edge/wire/sse.py SSEDecoder``
is injected via :func:`real_decoder_factory`. The whole module
``pytest.importorskip``s ``httpx`` (an optional ``conformance``-extra dependency),
so a tree without the extra skips the module cleanly rather than erroring
collection. ``openai`` is NOT needed.

Two kinds of test live here:

* **§5.2 Property 2 (PBT)** — a seeded ``random.Random``, NO hypothesis,
  >= 10,000 iterations: the terminal ``final`` flag is set on and ONLY on the
  last event, and ``[DONE]`` produces no extra content event (event count == n).
  See :func:`test_property_2_terminal_final_flag` for the iteration-count / per-
  iteration-shape rationale.
* **§5.3 integration examples** (async, driven by pytest-asyncio) — open/stream/
  final with request-echo assertions (R1.2/R6.1), mid-stream error_frame
  (R1.3/R11), malformed fail-closed (R2.3), abort cuts the in-flight read and a
  second abort is a no-op (R3/R6.4), and the ``isinstance(client, ProviderClient)``
  protocol contract (R1.1).

Async marking. The repo runs ``pytest-asyncio`` in its default strict mode (no
``asyncio_mode`` is set in ``pyproject.toml``), so each async test needs an
explicit marker — this module sets ``pytestmark = pytest.mark.asyncio`` exactly
like ``tests/openai_conformance/test_gw15_chat_surface_v2.py``. The Property-2
test is a plain SYNC ``def`` that drives its >= 10,000 iterations through ONE
``asyncio.run`` (an async driver loop), so pytest-asyncio does not collect it as
a coroutine and the single loop amortizes setup over all iterations.

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.dispatch`` and the ``_b4_fakes`` helper (which imports
``edge/wire/sse.py``) here is allowed.

_Design: Correctness Properties → Property 2; Testing Strategy (integration);
Error Handling table._
"""

from __future__ import annotations

import asyncio
import random

import pytest

httpx = pytest.importorskip("httpx")  # optional conformance-extra dep (R2.2)

from gateway_v2.dispatch.http_provider import (  # noqa: E402  (after importorskip)
    STREAM_MALFORMED_UPSTREAM,
    HttpProviderClient,
)
from gateway_v2.dispatch.provider import (  # noqa: E402
    ProviderClient,
    UpstreamEvent,
    UpstreamRequest,
)
from gateway_v2.dispatch.transform import (  # noqa: E402
    BODY_DELTAS_KEY,
    BODY_MODEL_KEY,
    GatewayRequest,
    Transform,
)

from ._b4_fakes import (  # noqa: E402
    DEFAULT_DESTINATION,
    DeterministicClock,
    FakeUpstream,
    FaultKind,
    ScriptFrame,
    error_sse_frame,
    real_decoder_factory,
    sse_content_frame,
)

# --------------------------------------------------------------------------- #
# Shared wiring helpers.
# --------------------------------------------------------------------------- #

_MODEL = "fake-model"


def _make_client(fake: FakeUpstream) -> HttpProviderClient:
    """Wire an ``HttpProviderClient`` against ``fake`` with the real decoder.

    Uses exactly the harness public API: the fake's BOUND ``client_factory``
    method, the REAL ``edge/wire/sse.py SSEDecoder`` via ``real_decoder_factory``,
    and a ``DeterministicClock`` (retained by the client for parity; never read).
    """
    return HttpProviderClient(
        client_factory=fake.client_factory,
        decoder_factory=real_decoder_factory(),
        clock=DeterministicClock(),
    )


def _upstream_request(
    deltas: tuple[tuple[str, str], ...],
    *,
    destination: str = DEFAULT_DESTINATION,
) -> UpstreamRequest:
    """Build the ``UpstreamRequest`` body via the shipped ``Transform`` (R1.5).

    Shapes a ``GatewayRequest`` through ``Transform.to_provider`` so the body
    carries ``BODY_MODEL_KEY`` / ``BODY_DELTAS_KEY`` exactly as the real pack
    does — no hand-rolled wire strings. The destination is carried through
    unchanged.
    """
    gateway_request = GatewayRequest(
        destination=destination, model=_MODEL, text_deltas=deltas
    )
    return Transform().to_provider(gateway_request)


async def _collect(client: HttpProviderClient, request: UpstreamRequest) -> list[UpstreamEvent]:
    """Drive ``client.open(request)`` to exhaustion and collect its events."""
    return [event async for event in client.open(request)]


# --------------------------------------------------------------------------- #
# §5.2 — Property 2 (PBT): terminal final-flag is set exactly on the last event.
# --------------------------------------------------------------------------- #

_SEED = 0xB4_0002
_ITERATIONS = 10_000

#: Per-iteration stream length cap. Rationale (reported with the gate): a full
#: httpx ``MockTransport`` round-trip per iteration is the honest shape (it drives
#: the real client's open/stream path), but 10,000 round-trips must stay under a
#: few seconds, so each iteration builds a SMALL stream (n in ``[1, _MAX_N]``). The
#: property under test — ``final`` true on and only on the last event, and
#: ``[DONE]`` adding no event — is a per-event boundary invariant that a short
#: stream exercises exactly as a long one, so the >= 10,000-iteration budget is
#: spent on seed/length/content variety rather than on long streams. All 10,000
#: iterations run inside ONE ``asyncio.run`` (an async driver loop) to amortize
#: event-loop setup.
_MAX_N = 5

_CHANNELS: tuple[str, ...] = ("content", "reasoning_content", "refusal", "ch-0")
_TEXTS: tuple[str, ...] = ("", "a", "Hello", "α", "日本語", "🚀x", '{"j":1}', "line\n")


def _ctx(i: int, note: str) -> str:
    """A reproducible failure context carrying the seed + iteration."""
    return f"seed={_SEED:#x} iter={i} {note}"


def _random_frame(rng: random.Random) -> ScriptFrame:
    """One content frame carrying 1–3 random ``(channel, text)`` deltas."""
    count = rng.randint(1, 3)
    deltas = tuple(
        (rng.choice(_CHANNELS), rng.choice(_TEXTS)) for _ in range(count)
    )
    return ScriptFrame(text_deltas=deltas)


async def _run_property_iterations() -> None:
    """Drive all ``_ITERATIONS`` streams on one loop; assert the final-flag law."""
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        n = rng.randint(1, _MAX_N)
        frames = tuple(_random_frame(rng) for _ in range(n))
        fake = FakeUpstream(frames)  # clean stream: n content frames then [DONE]
        client = _make_client(fake)
        events = await _collect(client, _upstream_request(frames[0].text_deltas))

        # [DONE] yields no extra content event: exactly n events for n frames.
        assert len(events) == n, _ctx(
            i, f"expected {n} events (one per frame, [DONE] adds none), got {len(events)}"
        )
        for pos, event in enumerate(events):
            is_last = pos == n - 1
            assert event.final is is_last, _ctx(
                i,
                f"event {pos}/{n}: final={event.final!r} expected {is_last!r} "
                f"(final must be True on and only on the last event)",
            )
            assert event.error_frame is None, _ctx(
                i, f"event {pos}/{n}: unexpected error_frame on a clean stream"
            )


def test_property_2_terminal_final_flag() -> None:
    """``final`` is set on and only on the last event; ``[DONE]`` adds no event.

    # Feature: provider-egress-gw-b4, Property 2: Terminal final-flag is set
    #   exactly on the last event
    # Validates: Requirements 1.4

    A SYNC test that runs its >= 10,000 iterations through ONE ``asyncio.run`` so
    pytest-asyncio does not treat it as a coroutine and the single event loop
    amortizes setup across every iteration.
    """
    asyncio.run(_run_property_iterations())


# --------------------------------------------------------------------------- #
# §5.3 — integration examples (async, 1–3 each).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_open_streams_final_and_connects_to_destination() -> None:
    """Clean 3-frame stream: byte-correct deltas, last ``final=True``, request echo.

    Validates R1.2 (streaming POST to ``UpstreamRequest.destination``), R1.4
    (terminal ``final``), and R6.1 (OpenAI-SSE shape over the fake) + that the
    ``Transform``-packed body carried the model + text_deltas + ``stream: true``.
    """
    frames = (
        sse_content_frame(("content", "Hello, ")),
        sse_content_frame(("content", "wörld ")),
        sse_content_frame(("content", "🚀!")),
    )
    fake = FakeUpstream(frames)
    client = _make_client(fake)

    events = await _collect(client, _upstream_request(frames[0].text_deltas))

    assert len(events) == 3
    assert [e.text_deltas for e in events] == [
        (("content", "Hello, "),),
        (("content", "wörld "),),
        (("content", "🚀!"),),
    ]
    assert [e.final for e in events] == [False, False, True]
    assert all(e.error_frame is None for e in events)

    # Connected to the named destination and sent the Transform-packed body.
    assert fake.requests_seen == 1
    assert fake.last_request_url == DEFAULT_DESTINATION
    body = fake.last_request_body
    assert isinstance(body, dict)
    assert body[BODY_MODEL_KEY] == _MODEL
    assert body["stream"] is True
    # text_deltas survive the JSON round trip as a list of [channel, text] pairs.
    assert body[BODY_DELTAS_KEY] == [["content", "Hello, "]]


@pytest.mark.asyncio
async def test_mid_stream_error_surfaces_terminal_error_frame() -> None:
    """A mid-stream ``{"error": ...}`` frame → a terminal ``error_frame``; no content after.

    Validates R1.3 / R11: the error is surfaced as a terminal
    ``UpstreamEvent.error_frame`` (the ``code`` the fake's ``error_sse_frame``
    carried, ``stream_killed``) so R11 scan-before-forward sees it, and nothing is
    yielded after it.
    """
    prefix = (sse_content_frame(("content", "partial")),)
    fake = FakeUpstream(prefix, fault=FaultKind.MID_STREAM_ERROR)
    client = _make_client(fake)

    events = await _collect(client, _upstream_request(prefix[0].text_deltas))

    assert len(events) >= 1
    terminal = events[-1]
    assert terminal.final is True
    assert terminal.error_frame == "stream_killed"
    assert terminal.text_deltas == ()
    # No content event follows the terminal error frame.
    assert all(e.error_frame is None for e in events[:-1])
    # The error_sse_frame harness builder keeps the code we asserted on.
    assert error_sse_frame("stream_killed").startswith(b"data: ")


@pytest.mark.asyncio
async def test_malformed_upstream_fails_closed() -> None:
    """Undecodable upstream SSE → the decoder's ``malformed()`` latch → fail closed.

    Validates R2.3: the terminal event carries ``error_frame ==
    STREAM_MALFORMED_UPSTREAM`` and the stream is never reported successful (no
    clean final content event).
    """
    prefix = (sse_content_frame(("content", "ok")),)
    fake = FakeUpstream(prefix, fault=FaultKind.MALFORMED)
    client = _make_client(fake)

    events = await _collect(client, _upstream_request(prefix[0].text_deltas))

    terminal = events[-1]
    assert terminal.final is True
    assert terminal.error_frame == STREAM_MALFORMED_UPSTREAM
    assert terminal.text_deltas == ()
    # Fail-closed: the terminal frame is an error, never a clean content final.
    assert not (terminal.error_frame is None and terminal.text_deltas)


@pytest.mark.asyncio
async def test_abort_cuts_in_flight_read_and_second_abort_is_noop() -> None:
    """``abort()`` cuts a stalled in-flight read; a second ``abort()`` is a no-op.

    Validates R3.1 / R3.4 / R6.4. Uses the §5.1-subagent abort pattern: a STALL
    fault with >= 2 prefix frames so at least one event arrives before the stall,
    then — from the DRIVING coroutine, NOT inside the ``async for`` body (that
    self-deadlocks) — ``abort()`` is awaited concurrently after the first event.
    The iteration runs in its OWN task (``asyncio.Event`` signals the first event);
    the driver aborts once that fires. The stream then stops (fewer events than a
    full clean stream, no hang), and a second ``abort()`` returns cleanly as a
    no-op. Timing-tolerant: the driver waits on the first-event event, not a sleep.
    """
    prefix = (
        sse_content_frame(("content", "one")),
        sse_content_frame(("content", "two")),
    )
    fake = FakeUpstream(prefix, fault=FaultKind.STALL)
    client = _make_client(fake)

    collected: list[UpstreamEvent] = []
    first_arrived = asyncio.Event()

    async def _iterate() -> None:
        # Iterate the stream in its own task. The STALL parks the body after the
        # prefix frames; ``abort`` → ``response.aclose`` releases that park, the
        # read ceases, and this ``async for`` ends — no hang.
        async for event in client.open(_upstream_request(prefix[0].text_deltas)):
            collected.append(event)
            first_arrived.set()

    task = asyncio.create_task(_iterate())
    # Wait until at least one event has been delivered (the read is in flight),
    # then abort from the driving coroutine while the body is parked on the stall.
    await asyncio.wait_for(first_arrived.wait(), timeout=5.0)
    await asyncio.wait_for(client.abort(), timeout=5.0)

    # ABORT CUT THE READ (R3.1). The STALL fault parks the body for ``_STALL_SECONDS``
    # (3600s); the ONLY way this ``await task`` returns inside the 5s bound is
    # ``abort`` → ``response.aclose`` releasing the parked read. A hang here (a
    # ``TimeoutError``) would be the failure. Prompt completion IS the proof the
    # in-flight read was cut rather than drained to its safety timeout.
    await asyncio.wait_for(task, timeout=5.0)

    # At least the first event arrived, and the aborted stream never produced more
    # than its content prefix (no fabricated post-abort content; the stall's own
    # terminal [DONE] never arrived — it was cut).
    assert 1 <= len(collected) <= len(prefix)

    # A second abort is a no-op: no raise, no hang.
    await asyncio.wait_for(client.abort(), timeout=5.0)


@pytest.mark.asyncio
async def test_client_satisfies_provider_protocol() -> None:
    """``isinstance(client, ProviderClient)`` holds (R1.1).

    The real client structurally satisfies the shipped ``@runtime_checkable``
    ``ProviderClient`` Protocol without the Protocol or the value types changing,
    so it is a drop-in through ``build_app(provider=...)``.
    """
    fake = FakeUpstream((sse_content_frame(("content", "x")),))
    client = _make_client(fake)
    assert isinstance(client, ProviderClient)
