"""LGW12 SSE codec property tests (GW12, tasks 5.2 + 5.3).

# Feature: sse-egress-pipeline, Property 8: SSE codec round-trip
# Validates: Requirements 1.6, 8.4

This module hosts the ``edge/wire`` codec property tests. Task 5.2 (this file's
first author) owns the **codec round-trip** half of Property 8; task 5.3 adds the
**split-surrogate confluence** test (Property 9) as a SEPARATE ``test_*`` function
in this same file. To keep the two concurrent authors from clashing, this file's
module-level helpers for the round-trip property are uniquely prefixed ``_rt_``;
task 5.3 must add its own distinctly-named helpers alongside, not replace these.

Property 8 (codec half, R1.6): for a well-formed content ``DownstreamFrame``,
encoding it with :class:`SSEEncoder.content` and feeding the wire bytes back
through :class:`SSEDecoder.feed` reproduces an EQUIVALENT ``DownstreamFrame`` —
its ``text_deltas`` are byte-for-byte identical to the original and no
``error_code`` is set. The sweep also exercises the terminal ``done()`` marker
(the decoder yields NO frame and never latches ``malformed()``) and an
``error()`` frame (the decoder yields a single terminal ``DownstreamFrame``
carrying the originating code), so all three encoder outputs round-trip through
the decoder as the state machine declares.

The content frame's text is drawn across ASCII, the BMP, and astral (surrogate-
pair) code points so the ``ensure_ascii`` ``\\uXXXX`` escaping on encode and the
surrogate-reassembly on decode are both exercised — the byte-for-byte identity
defends against any silent normalisation across the escape/decode boundary.

The sweep is a seeded ``random.Random`` driven for >= 10,000 iterations (house
idiom; NO ``hypothesis``). The seed is a module constant interpolated into every
failure message so a counterexample is reproducible, and it is logged at test
start.

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.edge`` and ``gateway_v2.egress`` here is allowed.

_Design: Correctness Properties → Property 8._
"""

from __future__ import annotations

import logging
import random

from gateway_v2.edge.wire.sse import ERROR_SHAPES, SSEDecoder, SSEEncoder
from gateway_v2.egress.stream import DownstreamFrame

_LOG = logging.getLogger(__name__)

#: >= 10,000 iterations (house idiom, no hypothesis).
_RT_ITERATIONS = 10_000

#: Reproducible seed for the round-trip sweep; logged and interpolated into
#: every assertion message so a counterexample can be replayed.
_RT_SEED = 0x120502

#: Alphabet for generated delta text. Spans ASCII, multi-byte BMP ranges, and an
#: astral range (surrogate-pair code points) so the ``ensure_ascii`` escape path
#: (encode) and the surrogate reassembly (decode) are both exercised. The low
#: control range and the surrogate block ``U+D800..U+DFFF`` are excluded: a lone
#: surrogate is not a valid scalar value and cannot survive a UTF-8 round-trip,
#: and the encoder/decoder operate on well-formed scalar text (R1.6 is stated for
#: *well-formed* content frames).
_RT_CHAR_RANGES: tuple[tuple[int, int], ...] = (
    (0x20, 0x7E),      # printable ASCII (1-byte UTF-8)
    (0x00A1, 0x024F),  # Latin-1 supplement + extended (2-byte)
    (0x0400, 0x04FF),  # Cyrillic (2-byte)
    (0x3040, 0x30FF),  # Hiragana/Katakana (3-byte)
    (0x1F300, 0x1FAFF),  # emoji / symbols (4-byte, astral — surrogate pairs)
)

#: Channel names the encoder maps to SDK ``delta`` keys. ``content`` is the
#: default key; any other name is used verbatim and must round-trip as its own
#: channel (``SSEEncoder._choices_for`` / ``SSEDecoder._deltas_from_choices``).
_RT_CHANNELS: tuple[str, ...] = ("content", "reasoning_content", "tool", "annotations")


def _rt_random_text(rng: random.Random) -> str:
    """A random, NON-empty string across the UTF-8 width spectrum.

    Non-empty because the SDK ``delta`` object only carries a key when its text
    is a string, and the decoder emits one ``(channel, text)`` delta per
    string-valued delta key; an empty-text delta is still a valid string and is
    allowed, but the generator favours >= 1 char so most iterations carry real
    bytes. (Empty strings are covered: ``randint(0, ...)`` can return 0.)
    """
    length = rng.randint(0, 20)
    out: list[str] = []
    for _ in range(length):
        lo, hi = rng.choice(_RT_CHAR_RANGES)
        out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def _rt_random_deltas(rng: random.Random) -> tuple[tuple[str, str], ...]:
    """A tuple of ``(channel, text)`` deltas — the content frame's payload.

    Each delta picks a channel from :data:`_RT_CHANNELS` so the default-vs-named
    channel mapping is exercised, and random text across the width spectrum.
    """
    count = rng.randint(1, 6)
    return tuple(
        (rng.choice(_RT_CHANNELS), _rt_random_text(rng)) for _ in range(count)
    )


def _rt_decode_single(wire: bytes) -> tuple[DownstreamFrame, ...]:
    """Feed ``wire`` through a fresh decoder and return the frames it yields.

    Asserts the decoder did not latch ``malformed()`` — the encoder's own output
    is by construction well-formed, so a malformed latch here is a codec defect.
    """
    decoder = SSEDecoder()
    frames = decoder.feed(wire)
    assert not decoder.malformed(), (
        f"seed={_RT_SEED:#x} decoder latched malformed on well-formed encoder output"
    )
    return frames


def test_property8_sse_codec_round_trip() -> None:
    """decode(encode(frame)) reproduces an equivalent DownstreamFrame (R1.6).

    # Feature: sse-egress-pipeline, Property 8: SSE codec round-trip
    # Validates: Requirements 1.6, 8.4

    A seeded ``random.Random`` drives >= 10,000 iterations. Each iteration:

    * builds a well-formed content ``DownstreamFrame`` with random
      ``(channel, text)`` deltas across the UTF-8 width spectrum, encodes it via
      ``SSEEncoder.content``, feeds the wire bytes through ``SSEDecoder.feed``,
      and asserts the single decoded frame's ``text_deltas`` are byte-for-byte
      identical with no ``error_code`` (the round-trip property);
    * encodes ``done()`` and asserts the decoder yields NO frame and does not
      latch ``malformed()`` (the ``[DONE]`` terminal marker is clean termination,
      not content);
    * encodes an ``error(code)`` for a random declared ``STREAM_*`` code and
      asserts the decoder yields exactly one terminal ``DownstreamFrame`` whose
      ``error_code`` echoes the originating code and whose ``text_deltas`` are
      empty.
    """
    _LOG.info("Property 8 SSE codec round-trip seed=%#x", _RT_SEED)
    rng = random.Random(_RT_SEED)
    error_codes = tuple(ERROR_SHAPES.keys())
    assert error_codes, f"seed={_RT_SEED:#x} ERROR_SHAPES must declare at least one code"

    for i in range(_RT_ITERATIONS):
        deltas = _rt_random_deltas(rng)
        original = DownstreamFrame(text_deltas=deltas)

        # -- content round-trip (R1.6): decode(encode(frame)) ≡ frame --------- #
        # A FRESH encoder per iteration: once an encoder emits a terminal frame
        # it refuses further content, so content/done/error each use their own.
        content_wire = SSEEncoder().content(original)
        decoded = _rt_decode_single(content_wire)
        assert len(decoded) == 1, (
            f"i={i} seed={_RT_SEED:#x} content frame decoded to {len(decoded)} frames, "
            f"expected exactly 1 (wire={content_wire!r})"
        )
        round_tripped = decoded[0]
        assert round_tripped.error_code is None, (
            f"i={i} seed={_RT_SEED:#x} well-formed content round-trip set error_code="
            f"{round_tripped.error_code!r}"
        )
        # Byte-for-byte identity on the carried text payload.
        assert round_tripped.text_deltas == original.text_deltas, (
            f"i={i} seed={_RT_SEED:#x} round-trip lost deltas: "
            f"{round_tripped.text_deltas!r} != {original.text_deltas!r}"
        )
        # The identity holds at the UTF-8 byte level too, defending the
        # byte-for-byte claim against any silent normalisation across the
        # ensure_ascii-escape / surrogate-decode boundary.
        assert _rt_encode_bytes(round_tripped.text_deltas) == _rt_encode_bytes(
            original.text_deltas
        ), f"i={i} seed={_RT_SEED:#x} round-trip altered UTF-8 bytes"

        # -- done() marker: decoder yields NO frame, not malformed (R1.2) ----- #
        done_wire = SSEEncoder().done()
        done_frames = _rt_decode_single(done_wire)
        assert done_frames == (), (
            f"i={i} seed={_RT_SEED:#x} [DONE] marker decoded to {done_frames!r}, "
            f"expected no frame"
        )

        # -- error() frame: decoder yields ONE terminal frame carrying code --- #
        code = rng.choice(error_codes)
        error_wire = SSEEncoder().error(code)
        error_frames = _rt_decode_single(error_wire)
        assert len(error_frames) == 1, (
            f"i={i} seed={_RT_SEED:#x} error frame decoded to {len(error_frames)} frames, "
            f"expected exactly 1 (code={code!r} wire={error_wire!r})"
        )
        error_frame = error_frames[0]
        assert error_frame.error_code == code, (
            f"i={i} seed={_RT_SEED:#x} error round-trip lost the code: "
            f"{error_frame.error_code!r} != {code!r}"
        )
        assert error_frame.text_deltas == (), (
            f"i={i} seed={_RT_SEED:#x} terminal error frame carried content deltas: "
            f"{error_frame.text_deltas!r}"
        )


def _rt_encode_bytes(
    deltas: tuple[tuple[str, str], ...],
) -> tuple[tuple[bytes, bytes], ...]:
    """UTF-8 encode each ``(channel, text)`` pair for an explicit byte comparison."""
    return tuple((ch.encode("utf-8"), txt.encode("utf-8")) for ch, txt in deltas)


# =========================================================================== #
# Task 5.3 — Property 9: Split-surrogate confluence (R1.4, +R1.5 targeted).
#
# Distinct from task 5.2 above: its helpers are prefixed ``_sc_`` and it owns a
# separate seed constant ``_SC_SEED`` so the two concurrent authors never clash.
# 5.2's ``_rt_`` helpers and ``test_property8_sse_codec_round_trip`` are left
# unchanged.
# =========================================================================== #

#: >= 10,000 iterations (house idiom, no hypothesis).
_SC_ITERATIONS = 10_000

#: Reproducible seed for the split-surrogate confluence sweep; distinct from
#: ``_RT_SEED`` so the two sweeps are independent. Logged at test start and
#: interpolated into every assertion message so a counterexample can be replayed.
_SC_SEED = 0x9C0FFE

#: Alphabet for generated delta text, weighted toward astral code points. Astral
#: code points (U+10000..U+10FFFF) are what force a JSON ``\uXXXX`` SURROGATE
#: PAIR under ``ensure_ascii``, so the escape-splitting / surrogate-reassembly
#: path (R1.4) is the common case rather than a rare one. BMP ranges also emit a
#: single ``\uXXXX`` escape; ASCII stays literal. Lone surrogates
#: (U+D800..U+DFFF) are excluded — not valid scalars, cannot survive a UTF-8
#: round-trip, and R1.4 concerns well-formed upstream scalar text.
_SC_CHAR_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1FAFF),  # emoji / symbols (astral — JSON surrogate PAIR)
    (0x1D400, 0x1D7FF),  # mathematical alphanumerics (astral — surrogate PAIR)
    (0x20000, 0x2A6DF),  # CJK Ext. B (astral — surrogate PAIR)
    (0x3040, 0x30FF),    # Hiragana/Katakana (BMP, single \uXXXX escape)
    (0x0400, 0x04FF),    # Cyrillic (BMP, single \uXXXX escape)
    (0x20, 0x7E),        # printable ASCII (literal, no escape)
)

#: Channel names the encoder maps to SDK ``delta`` keys (same contract 5.2
#: exercises, re-declared under the ``_sc_`` namespace to stay independent).
_SC_CHANNELS: tuple[str, ...] = ("content", "reasoning_content", "tool", "annotations")


def _sc_random_text(rng: random.Random) -> str:
    """A random, mostly-astral string so ``ensure_ascii`` emits surrogate pairs.

    Length can be 0 (an empty delta string is still a valid content frame), but
    the generator favours >= 1 char so most iterations carry real escapes to
    split.
    """
    length = rng.randint(0, 24)
    out: list[str] = []
    for _ in range(length):
        lo, hi = rng.choice(_SC_CHAR_RANGES)
        out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def _sc_random_deltas(rng: random.Random) -> tuple[tuple[str, str], ...]:
    """A tuple of ``(channel, text)`` deltas — the content frame's payload."""
    count = rng.randint(1, 6)
    return tuple(
        (rng.choice(_SC_CHANNELS), _sc_random_text(rng)) for _ in range(count)
    )


def _sc_boundaries(rng: random.Random, n: int) -> list[int]:
    """A random, strictly-increasing set of cut points in ``range(1, n)``.

    Produces a partition of ``n`` wire bytes into >= 1 chunks whose boundaries
    fall at arbitrary byte offsets — crucially including offsets that land INSIDE
    a ``\\uXXXX`` escape and BETWEEN the high and low surrogate of a pair, which
    is where a naive decoder would decode a half-formed code point (R1.4). The
    count of cuts is itself random so a wide variety of partitions is swept.
    """
    if n <= 1:
        return []
    max_cuts = min(n - 1, rng.randint(0, 8))
    if max_cuts == 0:
        return []
    cuts = rng.sample(range(1, n), max_cuts)
    cuts.sort()
    return cuts


def _sc_chunks(wire: bytes, cuts: list[int]) -> list[bytes]:
    """Slice ``wire`` into chunks at ``cuts`` (strictly-increasing offsets)."""
    chunks: list[bytes] = []
    prev = 0
    for cut in cuts:
        chunks.append(wire[prev:cut])
        prev = cut
    chunks.append(wire[prev:])
    return chunks


def _sc_escape_split_boundaries(wire: bytes) -> list[list[int]]:
    """Partition offsets that deliberately split a ``\\uXXXX`` escape / pair.

    Scans ``wire`` for each ``\\uXXXX`` escape and, for every escape found,
    yields a two-chunk partition cutting at an offset INSIDE that escape (the
    ``\\u`` / hex-digit interior). When two escapes are adjacent (a high/low
    surrogate pair for an astral code point) it also yields a partition cutting
    exactly BETWEEN them — the high surrogate in one chunk, the low in the next.
    These are the adversarial boundaries R1.4 is specifically about; the sweep
    adds them to the random partitions so the surrogate-pair split is always hit
    for every frame that carries an astral code point, not just probabilistically.
    """
    partitions: list[list[int]] = []
    i = 0
    n = len(wire)
    while i < n - 1:
        if wire[i] == 0x5C and wire[i + 1] == 0x75:  # backslash, 'u'
            # Interior cut: between the 'u' and its first hex digit (and one
            # deeper, mid-hex) exercises splitting the escape body itself.
            interior = i + 3
            if 0 < interior < n:
                partitions.append([interior])
            # If a second \uXXXX escape immediately follows, this is a surrogate
            # PAIR; cut exactly between the high and low surrogate escapes.
            between = i + 6
            if between + 1 < n and wire[between] == 0x5C and wire[between + 1] == 0x75:
                partitions.append([between])
            i = i + 6
            continue
        i += 1
    return partitions


def _sc_decode_whole(wire: bytes) -> tuple[DownstreamFrame, ...]:
    """Decode ``wire`` in a SINGLE feed — the confluence reference result.

    Asserts the decoder does not latch ``malformed()`` on the encoder's own
    well-formed output; any malformed latch here is a codec defect, not a split
    artefact.
    """
    decoder = SSEDecoder()
    frames = decoder.feed(wire)
    assert not decoder.malformed(), (
        f"seed={_SC_SEED:#x} decoder latched malformed on a whole well-formed feed"
    )
    return frames


def _sc_decode_chunked(chunks: list[bytes]) -> tuple[DownstreamFrame, ...]:
    """Feed ``chunks`` to a FRESH decoder in order; return all frames in order.

    Asserts the decoder never latches ``malformed()`` across the chunked feed:
    the bytes are the encoder's own well-formed output, so no chunk boundary —
    however it splits an escape or a surrogate pair — may make the decoder report
    the stream undecodable (R1.4 buffers the partial escape instead).
    """
    decoder = SSEDecoder()
    frames: list[DownstreamFrame] = []
    for chunk in chunks:
        frames.extend(decoder.feed(chunk))
    assert not decoder.malformed(), (
        f"seed={_SC_SEED:#x} decoder latched malformed under a chunk partition "
        f"(a split escape must buffer, not fail): chunk_sizes="
        f"{[len(c) for c in chunks]!r}"
    )
    return tuple(frames)


def _sc_codepoints(
    frames: tuple[DownstreamFrame, ...],
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Flatten frames to ``(channel, code-point tuple)`` pairs for comparison.

    Comparing CODE POINTS (not just the equal strings) makes the confluence
    assertion explicit about R1.4's claim: the decoded scalar values are
    identical regardless of boundary placement, with no half-formed or
    normalised code point introduced where an escape or surrogate pair was split.
    """
    out: list[tuple[str, tuple[int, ...]]] = []
    for frame in frames:
        for channel, text in frame.text_deltas:
            out.append((channel, tuple(ord(ch) for ch in text)))
    return tuple(out)


def test_property9_split_surrogate_confluence() -> None:
    """Decoding the same bytes under any chunk partition yields the same code points.

    # Feature: sse-egress-pipeline, Property 9: Split-surrogate confluence
    # Validates: Requirements 1.4

    A seeded ``random.Random`` drives >= 10,000 iterations. Each iteration:

    * builds a well-formed content ``DownstreamFrame`` whose deltas are drawn
      heavily from astral code points, so ``SSEEncoder.content`` (which encodes
      with ``ensure_ascii``) emits ``\\uXXXX`` SURROGATE PAIRS on the wire;
    * decodes those exact wire bytes once in a single feed — the confluence
      REFERENCE — asserting the decoder matches the original frame's deltas and
      never latches ``malformed()``;
    * re-feeds the SAME bytes to fresh decoders under MANY partitions: several
      random boundary sets (which can fall anywhere, including inside an escape),
      every targeted partition that splits a ``\\uXXXX`` escape or cuts BETWEEN a
      high/low surrogate pair, and the single-byte-at-a-time partition;
    * asserts every partition decodes to code points IDENTICAL to the reference
      (confluence, R1.4) and that no partition makes the decoder latch
      ``malformed()`` — a split escape must buffer, not fail.

    A final targeted R1.5 case feeds a genuinely-undecodable COMPLETE event
    (invalid UTF-8 bytes terminated by ``\\n\\n``) and asserts the decoder DOES
    set ``malformed()`` and yields no frame — the fail-closed signal the caller
    turns into ``STREAM_MALFORMED_UPSTREAM``.
    """
    _LOG.info("Property 9 split-surrogate confluence seed=%#x", _SC_SEED)
    rng = random.Random(_SC_SEED)

    for i in range(_SC_ITERATIONS):
        deltas = _sc_random_deltas(rng)
        original = DownstreamFrame(text_deltas=deltas)
        wire = SSEEncoder().content(original)

        # -- reference: a single whole-stream feed ---------------------------- #
        reference = _sc_decode_whole(wire)
        ref_cps = _sc_codepoints(reference)
        # The reference itself must reproduce the original scalar values.
        assert ref_cps == _sc_codepoints((original,)), (
            f"i={i} seed={_SC_SEED:#x} whole-feed decode did not reproduce the "
            f"original code points: {ref_cps!r} != {_sc_codepoints((original,))!r}"
        )

        # -- assemble the partitions to sweep for this frame ------------------ #
        partitions: list[list[int]] = []
        # Several random partitions (boundaries can fall anywhere, incl. mid-escape).
        for _ in range(4):
            partitions.append(_sc_boundaries(rng, len(wire)))
        # Every targeted escape-interior / between-surrogate split.
        partitions.extend(_sc_escape_split_boundaries(wire))
        # Single-byte-at-a-time: the maximal split (every byte its own chunk).
        partitions.append(list(range(1, len(wire))))

        for cuts in partitions:
            chunks = _sc_chunks(wire, cuts)
            # Byte-identity sanity: the partition reassembles to the same wire.
            assert b"".join(chunks) == wire, (
                f"i={i} seed={_SC_SEED:#x} partition did not reassemble to wire "
                f"(cuts={cuts!r})"
            )
            chunked = _sc_decode_chunked(chunks)
            chunked_cps = _sc_codepoints(chunked)
            # Confluence: identical code points regardless of boundary placement.
            assert chunked_cps == ref_cps, (
                f"i={i} seed={_SC_SEED:#x} boundary placement changed the decoded "
                f"code points (confluence violated): cuts={cuts!r} "
                f"chunk_sizes={[len(c) for c in chunks]!r}\n"
                f"  chunked={chunked_cps!r}\n  reference={ref_cps!r}"
            )
            # No error_code introduced by splitting a well-formed content frame.
            assert all(f.error_code is None for f in chunked), (
                f"i={i} seed={_SC_SEED:#x} a split well-formed content frame set an "
                f"error_code: {[f.error_code for f in chunked]!r} (cuts={cuts!r})"
            )

    # -- R1.5 targeted: an undecodable COMPLETE event sets malformed() -------- #
    # A ``data:`` line carrying invalid UTF-8 (a lone 0x80 continuation byte),
    # terminated by the blank-line separator so it is a COMPLETE event. The
    # decoder must fail closed: latch ``malformed()`` and yield no frame.
    bad_decoder = SSEDecoder()
    bad_event = b"data: " + bytes([0x80, 0x81]) + b"\n\n"
    bad_frames = bad_decoder.feed(bad_event)
    assert bad_frames == (), (
        f"seed={_SC_SEED:#x} an undecodable event yielded frames {bad_frames!r}, "
        f"expected none"
    )
    assert bad_decoder.malformed(), (
        f"seed={_SC_SEED:#x} an undecodable complete event did NOT latch "
        f"malformed() (R1.5 fail-closed signal missing)"
    )

    # -- R1.5 targeted: a non-JSON ``data:`` payload is undecodable too. ------- #
    # Complete event, valid UTF-8, but the ``data:`` body is not JSON at all, so
    # ``_frame_from_json`` fails to parse it: the decoder must fail closed rather
    # than silently drop it. (A well-formed JSON object missing ``choices`` is a
    # VALID empty content frame per the codec and is deliberately NOT asserted
    # malformed here.)
    nonjson_decoder = SSEDecoder()
    nonjson_frames = nonjson_decoder.feed(b"data: not-json-at-all\n\n")
    assert nonjson_frames == (), (
        f"seed={_SC_SEED:#x} a non-JSON event yielded frames {nonjson_frames!r}, "
        f"expected none"
    )
    assert nonjson_decoder.malformed(), (
        f"seed={_SC_SEED:#x} a non-JSON complete event did NOT latch malformed() "
        f"(R1.5 fail-closed signal missing)"
    )
