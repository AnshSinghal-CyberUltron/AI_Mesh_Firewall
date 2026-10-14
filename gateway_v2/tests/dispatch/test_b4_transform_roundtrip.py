# Feature: provider-egress-gw-b4, Property 1: Transform byte-identity round-trip on provider content  # noqa: E501
# Validates: Requirements 1.3, 2.4, 6.3
"""Property 1 — Transform byte-identity round-trip on provider content (B4 / GW17-18).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >= 10,000 iterations.
The seed is a module constant interpolated into every failure message so a
counterexample is reproducible.

*For any* well-formed :class:`GatewayRequest` payload ``x``, shaping it to the
provider and reading it back preserves the text content byte-for-byte. Two
clearly separated halves are checked each iteration:

1. **The pure transform half (no socket).** ``from_provider(echo(to_provider(x)))``
   reproduces ``x``'s ``text_deltas`` byte-for-byte. ``echo`` (the shipped
   ``gateway_v2.dispatch.transform.echo``) models the provider reflecting the
   submitted request back as a single terminal ``UpstreamEvent`` — the only step
   a *pure* transform can stand in for the real network — so the full
   request → provider → event → downstream path is exercised without a socket.

2. **The reused-SSE-codec half.** The ``DownstreamFrame`` the transform produced
   (``from_provider(ev)``) is serialised by the shipped ``edge/wire/sse.py``
   :class:`SSEEncoder` content frame and fed straight back through
   :meth:`SSEDecoder.feed`; the decoded ``DownstreamFrame``'s ``text_deltas`` are
   asserted byte-identical to the content the transform produced. This is the
   codec the real ``HttpProviderClient`` reuses (R2.3): ``SSEEncoder`` escapes
   every non-ASCII code point as a JSON ``\\uXXXX`` escape (a surrogate PAIR for
   an astral code point), so an emoji in the corpus forces the decoder's
   split-surrogate buffering path.

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.dispatch`` and ``gateway_v2.edge.wire.sse`` here is allowed (the
real ``HttpProviderClient`` reaches the decoder by injection; a test may import
it directly). The ``SSEEncoder``/``SSEDecoder`` construction + feed pattern
mirrors the GW12/GW15 codec tests.

_Design: Correctness Properties → Property 1._
"""

from __future__ import annotations

import random

from gateway_v2.dispatch.transform import GatewayRequest, Transform, echo
from gateway_v2.edge.wire.sse import SSEDecoder, SSEEncoder

_SEED = 0xB4_0001
_ITERATIONS = 10_000

#: Alphabet for generated delta text. Spans ASCII, multi-byte BMP ranges, and an
#: astral range (surrogate-pair code points), so the byte-for-byte identity is
#: exercised over 1-, 2-, 3- and 4-byte UTF-8 encodings. The astral range forces
#: the SSE decoder's split-surrogate path because ``SSEEncoder`` escapes it as a
#: ``\\uXXXX`` high/low surrogate PAIR on the wire.
_CHAR_RANGES: tuple[tuple[int, int], ...] = (
    (0x20, 0x7E),       # printable ASCII (incl. JSON-significant " \ { } , : )
    (0x00A1, 0x024F),   # Latin-1 supplement + extended (2-byte UTF-8)
    (0x0400, 0x04FF),   # Cyrillic (2-byte)
    (0x3040, 0x30FF),   # Hiragana/Katakana (3-byte)
    (0x1F300, 0x1FAFF),  # emoji / symbols (4-byte, astral -> surrogate pair)
)

#: JSON-significant / whitespace fragments spliced into some texts so the codec's
#: ``ensure_ascii`` escaping and the decoder's JSON parse are stressed directly.
_SIGNIFICANT: tuple[str, ...] = (
    "",
    '"',
    "\\",
    "{}",
    "[]",
    ",",
    ":",
    "\n",
    "\t",
    "\r\n",
    "   ",
    '{"nested":"json"}',
)


def _ctx(i: int, note: str) -> str:
    """A reproducible failure context string carrying the seed + iteration."""
    return f"seed={_SEED:#x} iter={i} {note}"


def _random_text(rng: random.Random) -> str:
    """A random, possibly-empty string drawn across the UTF-8 width spectrum.

    Sometimes an empty string, sometimes a JSON-significant / whitespace
    fragment, and otherwise a run of code points sampled across the width ranges
    (including astral emoji) with the significant fragments optionally spliced in.
    """
    roll = rng.randint(0, 9)
    if roll == 0:
        return ""
    if roll == 1:
        return rng.choice(_SIGNIFICANT)
    length = rng.randint(0, 24)
    out: list[str] = []
    for _ in range(length):
        if rng.random() < 0.15:
            out.append(rng.choice(_SIGNIFICANT))
            continue
        lo, hi = rng.choice(_CHAR_RANGES)
        out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def _random_channel(rng: random.Random) -> str:
    """A short channel-name token, including the default ``content`` channel.

    The encoder maps the ``content`` channel to the SDK ``content`` delta key and
    every other channel to a verbatim delta key, and the decoder inverts that, so
    both the default and non-default channels must round-trip.
    """
    return rng.choice(
        ("content", "reasoning_content", "refusal", "tool_arguments", "a", "ch-0")
    )


def _random_deltas(rng: random.Random) -> tuple[tuple[str, str], ...]:
    """A tuple of ``(channel, delta_text)`` pairs — the request's text payload.

    Includes the empty tuple (count 0) and multi-delta lists (up to 6) so the
    single-event multi-choice serialisation is exercised.
    """
    count = rng.randint(0, 6)
    return tuple((_random_channel(rng), _random_text(rng)) for _ in range(count))


def _random_request(rng: random.Random) -> GatewayRequest:
    """A well-formed ``GatewayRequest`` with random destination, model and deltas."""
    return GatewayRequest(
        destination=f"amf://upstream/org-{rng.getrandbits(32):08x}",
        model=rng.choice(("gpt-5.2", "north-mini", "nemotron", "claude-4")),
        text_deltas=_random_deltas(rng),
    )


def _encode(deltas: tuple[tuple[str, str], ...]) -> tuple[tuple[bytes, bytes], ...]:
    """UTF-8 encode each ``(channel, text)`` pair for an explicit byte comparison."""
    return tuple((ch.encode("utf-8"), txt.encode("utf-8")) for ch, txt in deltas)


def test_property_1_transform_byte_identity_round_trip() -> None:
    """Both round-trip halves preserve provider content byte-for-byte (R1.3/R2.4/R6.3).

    # Feature: provider-egress-gw-b4, Property 1: Transform byte-identity
    #   round-trip on provider content
    # Validates: Requirements 1.3, 2.4, 6.3
    """
    rng = random.Random(_SEED)
    transform = Transform()
    for i in range(_ITERATIONS):
        request = _random_request(rng)

        # --- Half 1: the pure transform round-trip (no socket). ------------------
        upstream_request = transform.to_provider(request)
        upstream_event = echo(upstream_request)
        downstream = transform.from_provider(upstream_event)

        assert downstream.error_code is None, _ctx(
            i, "unexpected error_code on well-formed transform round-trip"
        )
        assert downstream.text_deltas == request.text_deltas, _ctx(
            i,
            f"transform round-trip lost deltas: "
            f"{downstream.text_deltas!r} != {request.text_deltas!r}",
        )
        assert _encode(downstream.text_deltas) == _encode(request.text_deltas), _ctx(
            i, "transform round-trip altered UTF-8 bytes"
        )

        # --- Half 2: the reused SSE codec round-trip on the transform's content. --
        # Serialise the content the transform produced with the shipped encoder
        # (shipped stream-id/model defaults) and feed it straight back through the
        # shipped decoder — the exact codec the real HttpProviderClient reuses.
        encoder = SSEEncoder()
        decoder = SSEDecoder()
        wire = encoder.content(downstream)
        frames = decoder.feed(wire)

        assert not decoder.malformed(), _ctx(
            i, "decoder latched malformed on a well-formed content frame"
        )
        assert len(frames) == 1, _ctx(
            i, f"expected exactly one decoded content frame, got {len(frames)}"
        )
        decoded = frames[0]
        assert decoded.error_code is None, _ctx(
            i, "unexpected error_code on the decoded content frame"
        )
        assert decoded.text_deltas == downstream.text_deltas, _ctx(
            i,
            f"SSE codec round-trip lost deltas: "
            f"{decoded.text_deltas!r} != {downstream.text_deltas!r}",
        )
        assert _encode(decoded.text_deltas) == _encode(downstream.text_deltas), _ctx(
            i, "SSE codec round-trip altered UTF-8 bytes"
        )
