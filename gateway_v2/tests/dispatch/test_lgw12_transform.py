"""LGW12 Transform round-trip property test (GW12, task 4.4).

# Feature: sse-egress-pipeline, Property 8: SSE codec round-trip
# Validates: Requirements 8.4

Property 8 (transform half): for a well-formed ``GatewayRequest`` ``x``,
``from_provider(echo(to_provider(x)))`` reproduces ``x``'s ``text_deltas``
byte-for-byte. ``echo`` (``gateway_v2.dispatch.transform.echo``) stands in for
the provider reflecting the submitted request back as a single terminal
``UpstreamEvent`` — the only step a *pure* transform can model — so the full
request → provider → event → downstream path is exercised without a socket.

The sweep is a seeded ``random.Random`` driven for >= 10,000 iterations (house
idiom; no ``hypothesis``). The seed is a module constant and is interpolated
into every failure message so a counterexample is reproducible.

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.dispatch`` and ``gateway_v2.egress`` here is allowed.

_Design: Correctness Properties → Property 8; Components §5._
"""

from __future__ import annotations

import random

from gateway_v2.dispatch.transform import GatewayRequest, Transform, echo

_ITERATIONS = 10_000

#: Alphabet for generated delta text. Spans ASCII, a multi-byte BMP range, and
#: an astral range (surrogate-pair code points), so the byte-for-byte identity
#: is exercised over 1-, 2-, 3- and 4-byte UTF-8 encodings — not just ASCII.
_CHAR_RANGES: tuple[tuple[int, int], ...] = (
    (0x20, 0x7E),      # printable ASCII
    (0x00A1, 0x024F),  # Latin-1 supplement + extended (2-byte UTF-8)
    (0x0400, 0x04FF),  # Cyrillic (2-byte)
    (0x3040, 0x30FF),  # Hiragana/Katakana (3-byte)
    (0x1F300, 0x1FAFF),  # emoji / symbols (4-byte, astral)
)


def _random_text(rng: random.Random) -> str:
    """A random, possibly-empty string drawn across the UTF-8 width spectrum."""
    length = rng.randint(0, 24)
    out: list[str] = []
    for _ in range(length):
        lo, hi = rng.choice(_CHAR_RANGES)
        out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def _random_channel(rng: random.Random) -> str:
    """A short channel name (e.g. ``message`` / ``reasoning``)-shaped token."""
    return rng.choice(("message", "reasoning", "tool", "annotations", "a", "ch-0"))


def _random_deltas(rng: random.Random) -> tuple[tuple[str, str], ...]:
    """A tuple of ``(channel, delta_text)`` pairs — the request's text payload."""
    count = rng.randint(0, 6)
    return tuple((_random_channel(rng), _random_text(rng)) for _ in range(count))


def _random_request(rng: random.Random) -> GatewayRequest:
    """A well-formed ``GatewayRequest`` with random destination, model and deltas."""
    return GatewayRequest(
        destination=f"amf://upstream/org-{rng.getrandbits(32):08x}",
        model=rng.choice(("gpt-5.2", "north-mini", "nemotron", "claude-4")),
        text_deltas=_random_deltas(rng),
    )


def test_property8_transform_round_trip_is_byte_identity() -> None:
    """from_provider(echo(to_provider(x))) reproduces x.text_deltas byte-for-byte (R8.4).

    # Feature: sse-egress-pipeline, Property 8: SSE codec round-trip
    # Validates: Requirements 8.4
    """
    seed = 0x120804
    rng = random.Random(seed)
    transform = Transform()
    for i in range(_ITERATIONS):
        request = _random_request(rng)

        upstream_request = transform.to_provider(request)
        upstream_event = echo(upstream_request)
        downstream = transform.from_provider(upstream_event)

        # A well-formed (content) round-trip never produces an error frame.
        assert downstream.error_code is None, (
            f"i={i} seed={seed:#x} unexpected error_code on well-formed round-trip"
        )
        # Byte-for-byte identity on the carried text payload.
        assert downstream.text_deltas == request.text_deltas, (
            f"i={i} seed={seed:#x} round-trip lost deltas: "
            f"{downstream.text_deltas!r} != {request.text_deltas!r}"
        )
        # The byte identity holds at the UTF-8 encoding level too, defending the
        # "byte-for-byte" claim against any silent normalisation.
        assert _encode(downstream.text_deltas) == _encode(request.text_deltas), (
            f"i={i} seed={seed:#x} round-trip altered UTF-8 bytes"
        )


def _encode(deltas: tuple[tuple[str, str], ...]) -> tuple[tuple[bytes, bytes], ...]:
    """UTF-8 encode each ``(channel, text)`` pair for an explicit byte comparison."""
    return tuple((ch.encode("utf-8"), txt.encode("utf-8")) for ch, txt in deltas)
