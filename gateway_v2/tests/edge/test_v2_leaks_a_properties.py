"""v2-enforcement-leaks Group A (chat): the per-channel REDACT property test.

Property 3 (design, Bug Condition A): for ANY chat request (streaming or not) whose
input decision is REDACT, the provider body's ``text_deltas`` SHALL equal
``apply_and_verify(decision, request).request.text_deltas``. No REDACT finding's
original bytes SHALL appear in any delta, and each delta SHALL hold exactly its own
sanitized bytes -- including system messages, later messages, multi-part content and
values split across consecutive ``content_part_text`` parts of one message.

This is the 10,000-iteration seeded generalisation of the example-based exploration
test ``tests/edge/test_v2_leaks_a_input_redact.py`` (task 2, now passing). It mirrors
that test's machinery exactly -- the ``_RecordingProvider`` that keeps the exact
``(channel, text)`` tuples the gateway forwarded (``Transform.to_provider`` packs
them under ``BODY_DELTAS_KEY``), the dev-profile ``_app``/``_post`` driving the whole
chat lifecycle (scan -> stage -> provider) end to end through the REAL dev ASGI app
(``serve.build_boot_dependencies`` with ``AMF_DEV_PROFILE=1`` + ``dev/plan.example.json``,
provider swapped for the recording provider) -- and replaces the four fixed placements
with 10,000 random ones.

**The oracle.** The dev plan REDACTs ``pii`` (SSN, email) and BLOCKs secret/credential.
Every iteration plants exactly ONE REDACT-category PII value (an SSN or an email) in a
random message/part so the request resolves to an input REDACT (no BLOCK) and proceeds.
The provider therefore receives the verified, transformed bytes
``apply_and_verify(decision, request).request.text_deltas``. That oracle output is, for
a single contiguous value redacted to the one GW09 placeholder ``[REDACTED]``, exactly
the parsed deltas with the value's span rewritten to ``[REDACTED]`` -- and, when the
value straddles two consecutive content parts, the replacement landing in the part where
it STARTS and the later part losing the covered prefix (the ``dispatch/verify._resplit``
behaviour). :func:`_expected_deltas` builds that oracle from the planted placement, and
every iteration asserts the recorded provider deltas EQUAL it (Property 3, clause 1),
that no contiguous raw fragment of the value survives in any delta or in their
concatenation (clause 2), and -- pinned by the per-channel equality -- that each delta
holds only its own sanitized bytes (clause 3).

**One app, cleared provider (performance).** Booting the dev ASGI app per iteration
would make 10,000 iterations minutes-long; the app is stateless per request given the
recording provider is swapped in (the GW15 lifecycle now completes every admitted slot
on exit, so admission slots do not accumulate, and the dev budget is ``unlimited``), so
the app is booted ONCE and the recording provider's buffer is cleared each iteration.

Seeded ``random.Random`` (no hypothesis); the seed is in every assertion message.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# Same dev plan, key and contract as ``test_v2_leaks_a_input_redact.py`` (kept local so
# this file type-checks on its own under ``mypy --strict``).
EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
KEY = "sk-dev-pipeline"
REDACTED = "[REDACTED]"

#: The number of seeded iterations (house idiom: seeded ``random.Random``, >= 10,000).
ITERATIONS = 10_000
#: The one deterministic seed the whole run is driven from (in every assertion message).
SEED = 0x5A1A

#: The two channel names the input fold emits for the two content shapes the generator
#: builds (``edge/wire/openai.py``): a plain-string ``content`` -> ``message_content``;
#: an array-of-parts ``content[].text`` -> ``content_part_text``.
_CHANNEL_MESSAGE_CONTENT = "message_content"
_CHANNEL_CONTENT_PART_TEXT = "content_part_text"

#: Roles the generator draws from. Every role's text content is folded identically, so
#: a system or assistant message leaks exactly as a user message would on the unfixed
#: tree -- the point of covering them.
_ROLES = ("user", "system", "assistant")

#: A fixed alphabet of clean filler words. Strictly lowercase letters and spaces, so no
#: filler ever forms a GW09 finding (no digits -> no SSN/phone/card; no ``@`` -> no
#: email; no secret keyword/keys), keeping the ONLY finding the planted PII value.
_FILLER_WORDS = (
    "hello", "there", "please", "summarise", "the", "account", "notes",
    "for", "me", "today", "kindly", "review", "and", "confirm", "status",
    "thanks", "regarding", "update", "customer", "record", "on", "file",
)

Delta = tuple[str, str]


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


class _RecordingProvider:
    """Records the exact ``(channel, text)`` deltas of every forwarded body, then echoes.

    Byte-for-byte the double from ``test_v2_leaks_a_input_redact.py``: it reads the
    ``BODY_DELTAS_KEY`` tuple the staged ``Transform.to_provider`` packed and keeps the
    ``(channel, text)`` pairs, so a test asserts against the exact wire bytes rather than
    a joined string. :meth:`clear` empties the buffer so one booted app can be reused
    across all 10,000 iterations.
    """

    def __init__(self) -> None:
        self.received: list[tuple[Delta, ...]] = []

    def clear(self) -> None:
        self.received.clear()

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        deltas = tuple((str(item[0]), str(item[1])) for item in raw)
        self.received.append(deltas)
        text = "\n".join(t for _c, t in deltas)
        yield UpstreamEvent(text_deltas=(("content", "echo: " + text),), final=True)

    async def abort(self) -> None:
        return None


def _app(provider: _RecordingProvider) -> object:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _post(app: object, messages: list[dict[str, object]], *, stream: bool) -> int:
    body = json.dumps({"model": "echo-1", "stream": stream, "messages": messages}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    return next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")


# --------------------------------------------------------------------------- #
# Generator: a planted REDACT value placed in a random multi-message request.
# --------------------------------------------------------------------------- #


def _ssn(rng: random.Random) -> str:
    """A syntactically valid, Luhn-irrelevant SSN ``ddd-dd-dddd`` (REDACT PII)."""
    return (
        f"{rng.randint(100, 899)}-{rng.randint(10, 99):02d}-{rng.randint(1000, 9999):04d}"
    )


def _email(rng: random.Random) -> str:
    """A conventional ``local@domain.tld`` email (REDACT PII).

    The local and domain labels ALWAYS carry a digit, so no contiguous fragment of the
    email coincides with a letter-only filler word -- the fragment-absence assertion can
    never false-positive on an incidental filler match.
    """
    local = _labelled(rng)
    domain = _labelled(rng)
    tld = rng.choice(("com", "net", "org", "io"))
    return f"{local}@{domain}.{tld}"


def _labelled(rng: random.Random) -> str:
    """A 4-8 char lowercase label that always contains at least one digit."""
    chars = [rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 7))]
    chars.insert(rng.randint(0, len(chars)), rng.choice("0123456789"))
    return "".join(chars)


def _value(rng: random.Random) -> str:
    """Draw one REDACT-category PII value (SSN or email) from the fixed alphabet."""
    return _ssn(rng) if rng.random() < 0.5 else _email(rng)


def _phrase(rng: random.Random, *, min_words: int, max_words: int) -> str:
    """A space-joined run of clean filler words (never a finding)."""
    count = rng.randint(min_words, max_words)
    return " ".join(rng.choice(_FILLER_WORDS) for _ in range(count))


def _split_point(rng: random.Random, value: str) -> int:
    """An interior cut index ``1..len-1`` so both halves of a split value are non-empty."""
    return rng.randint(1, len(value) - 1)


class _Plant:
    """One planted placement: the request ``messages`` plus the expected provider deltas.

    ``messages`` is the JSON the generator built; ``expected`` is the oracle -- the
    parsed deltas with the single planted value's span rewritten to ``[REDACTED]`` (the
    one GW09 PII placeholder), the straddle case putting the replacement in the start
    part and stripping the covered prefix from the next part. ``raw_fragments`` are the
    contiguous fragments of the value long enough to identify it, asserted absent from
    every delta and from their concatenation.
    """

    __slots__ = ("messages", "expected", "raw_fragments", "split", "multi_message", "system")

    def __init__(
        self,
        *,
        messages: list[dict[str, object]],
        expected: tuple[Delta, ...],
        raw_fragments: tuple[str, ...],
        split: bool,
        multi_message: bool,
        system: bool,
    ) -> None:
        self.messages = messages
        self.expected = expected
        self.raw_fragments = raw_fragments
        self.split = split
        self.multi_message = multi_message
        self.system = system


def _raw_fragments(value: str) -> tuple[str, ...]:
    """Identifying fragments of ``value``: the whole value plus each digit-bearing half.

    A fragment is kept only when it is >= 4 chars AND contains a digit, so it cannot
    coincide with a letter-only filler word (:data:`_FILLER_WORDS`) and the
    fragment-absence assertion never false-positives on incidental clean text. The
    whole value is always kept (it is the planted secret itself). Every SSN half and
    every digit-bearing email half is covered; a digit-free email half is dropped
    (identifying it is not needed -- the whole-value check and the exact per-channel
    equality already prove no raw byte survives).
    """
    half = len(value) // 2
    candidates = (value[:half], value[half:])
    kept = tuple(f for f in candidates if len(f) >= 4 and any(c.isdigit() for c in f))
    return (value, *kept)


def _plant(rng: random.Random) -> _Plant:
    """Build one random request with exactly one planted REDACT value + its oracle.

    Degrees of freedom: 1-4 messages; each message's role (user/system/assistant); each
    message's content a plain string OR an array of 1-4 text parts; which message and
    which part carries the planted value; whether (for a multi-part message) the value
    straddles two consecutive parts; and the clean surrounding text.
    """
    message_count = rng.randint(1, 4)
    target_message = rng.randint(0, message_count - 1)
    # Decide the target message's shape up front so the plant lands coherently.
    target_as_parts = rng.random() < 0.5
    straddle = target_as_parts and rng.random() < 0.5
    # A straddled value is split across two parts that join with the EMPTY separator
    # (``_request_content``), so its two halves sit flush against each other AND against
    # the neighbouring clean words. An SSN's precise matcher carries strict non-digit
    # lookarounds (``(?<!\d)…(?!\d)``), so the glued halves match as EXACTLY the SSN and
    # never extend into a letter word; an email's local/domain run has no such boundary
    # and would greedily eat an adjacent word across the join. So the straddle case
    # plants an SSN (clean-boundary value); the non-straddle case plants either.
    value = _ssn(rng) if straddle else _value(rng)

    messages: list[dict[str, object]] = []
    expected: list[Delta] = []
    system = False
    for message_index in range(message_count):
        role = rng.choice(_ROLES)
        if role == "system":
            system = True
        is_target = message_index == target_message
        as_parts = target_as_parts if is_target else (rng.random() < 0.5)
        if as_parts:
            _build_parts_message(
                rng, messages, expected, role, is_target, value, straddle
            )
        else:
            _build_string_message(rng, messages, expected, role, is_target, value)
    return _Plant(
        messages=messages,
        expected=tuple(expected),
        raw_fragments=_raw_fragments(value),
        split=straddle,
        multi_message=message_count > 1,
        system=system,
    )


def _build_string_message(
    rng: random.Random,
    messages: list[dict[str, object]],
    expected: list[Delta],
    role: str,
    is_target: bool,
    value: str,
) -> None:
    """Append a plain-string ``content`` message (one ``message_content`` delta)."""
    lead = _phrase(rng, min_words=0, max_words=4)
    tail = _phrase(rng, min_words=0, max_words=4)
    if is_target:
        raw = _join(lead, value, tail)
        masked = _join(lead, REDACTED, tail)
    else:
        raw = masked = _join(lead, "", tail) or rng.choice(_FILLER_WORDS)
    messages.append({"role": role, "content": raw})
    expected.append((_CHANNEL_MESSAGE_CONTENT, masked))


def _build_parts_message(
    rng: random.Random,
    messages: list[dict[str, object]],
    expected: list[Delta],
    role: str,
    is_target: bool,
    value: str,
    straddle: bool,
) -> None:
    """Append an array-of-parts message (one ``content_part_text`` delta per part).

    When ``is_target`` and ``straddle`` the value is cut across two consecutive parts:
    the oracle puts ``[REDACTED]`` in the part where the value STARTS and strips the
    covered prefix from the next part, keeping that part's leading separator + clean
    tail (the ``dispatch/verify._resplit`` behaviour, exactly as the example test's
    ``split_across_parts`` part1 is ``" thanks"``). When ``is_target`` and not
    ``straddle`` the whole value sits in one randomly chosen part.
    """
    part_count = rng.randint(2, 4) if (is_target and straddle) else rng.randint(1, 4)
    target_part = rng.randint(0, part_count - 1)
    if is_target and straddle and target_part == part_count - 1:
        target_part -= 1  # leave room for the value's second half in the NEXT part
    # Pre-compute the split so no state crosses loop iterations. The straddle tail is
    # always >= 1 word so the second part is non-empty (the fold would not emit an
    # empty-text delta, which would break the per-channel count the oracle asserts).
    cut = _split_point(rng, value) if (is_target and straddle) else 0
    straddle_tail = _phrase(rng, min_words=1, max_words=3) if (is_target and straddle) else ""
    parts: list[dict[str, object]] = []
    for part_index in range(part_count):
        raw, masked = _part_text(
            rng,
            is_target=is_target,
            straddle=straddle,
            value=value,
            target_part=target_part,
            part_index=part_index,
            cut=cut,
            straddle_tail=straddle_tail,
        )
        parts.append({"type": "text", "text": raw})
        expected.append((_CHANNEL_CONTENT_PART_TEXT, masked))
    messages.append({"role": role, "content": parts})


def _part_text(
    rng: random.Random,
    *,
    is_target: bool,
    straddle: bool,
    value: str,
    target_part: int,
    part_index: int,
    cut: int,
    straddle_tail: str,
) -> tuple[str, str]:
    """The ``(raw, masked)`` text of one content part. Pure given the drawn randomness.

    The straddle pair -- the start part and the next part -- is laid out so the
    request-content concatenation (EMPTY-separator, ``_request_content``) places the two
    halves of ``value`` directly adjacent, i.e. the value is contiguous on the wire:

      start part : ``lead + " " + value[:cut]``   -> masked ``lead + " " + [REDACTED]``
      next  part : ``value[cut:] + " " + tail``   -> masked ``" " + tail`` (prefix gone)

    (``" " + tail`` keeps the separator the raw had, dropping ONLY the covered
    ``value[cut:]`` prefix; ``tail`` is always a non-empty clean word so the second
    part is never an empty-text delta.)
    """
    if is_target and straddle and part_index == target_part:
        # Non-empty lead so the start part begins with a clean word and a space precedes
        # ``value[:cut]`` in the joined run (the SSN lookbehind would hold regardless --
        # clean parts are letter-only -- but a space keeps the layout unambiguous).
        lead = _phrase(rng, min_words=1, max_words=3)
        return _join(lead, value[:cut], ""), _join(lead, REDACTED, "")
    if is_target and straddle and part_index == target_part + 1:
        raw_second = value[cut:]
        return f"{raw_second} {straddle_tail}", f" {straddle_tail}"
    if is_target and not straddle and part_index == target_part:
        # Non-empty lead AND tail so the value is whitespace-isolated WITHIN its own
        # part: in the empty-separator join of the message's parts the value is then
        # preceded and followed by a space, so an adjacent part's word cannot glue onto
        # it and extend the match (e.g. an email local/domain eating a neighbouring
        # word). A string ``message_content`` target is already isolated -- it is its
        # own delta and never in a join run -- so only parts targets need this.
        lead = _phrase(rng, min_words=1, max_words=3)
        tail = _phrase(rng, min_words=1, max_words=3)
        return _join(lead, value, tail), _join(lead, REDACTED, tail)
    clean = _phrase(rng, min_words=1, max_words=4)
    return clean, clean


def _join(lead: str, middle: str, tail: str) -> str:
    """Join ``lead`` + ``middle`` + ``tail`` with single spaces, dropping empty pieces.

    Mirrors how a human writes the sentence and how the example test composes its fixed
    placements (``"My SSN is 123-45-6789 thanks"``), so the planted value is a contiguous
    run bounded by whitespace -- never glued to a digit/letter that would change its span.
    """
    return " ".join(piece for piece in (lead, middle, tail) if piece)


# --------------------------------------------------------------------------- #
# The property.
# --------------------------------------------------------------------------- #


def test_input_redact_masks_every_channel_property() -> None:
    """Property 3: the provider receives exactly ``apply_and_verify``'s deltas.

    **Validates: Requirements 2.1, 2.3**
    """
    rng = random.Random(SEED)
    provider = _RecordingProvider()
    app = _app(provider)  # booted ONCE, reused across every iteration

    stream_modes = {False: 0, True: 0}
    split_cases = 0
    multi_message_cases = 0
    system_cases = 0

    started = time.perf_counter()
    for iteration in range(ITERATIONS):
        plant = _plant(rng)
        stream = iteration % 2 == 0  # exercise BOTH modes a meaningful number of times
        provider.clear()

        status = asyncio.run(_post(app, plant.messages, stream=stream))

        tag = f"seed={SEED:#x} iter={iteration} stream={stream}"
        assert status == 200, f"{tag}: an input REDACT (no BLOCK) must proceed, got {status}"
        assert len(provider.received) == 1, f"{tag}: exactly one provider call"
        deltas = provider.received[0]

        for channel, text in deltas:
            for fragment in plant.raw_fragments:
                assert fragment not in text, (
                    f"{tag}: raw fragment {fragment!r} in provider delta "
                    f"{(channel, text)!r}; deltas={deltas!r}"
                )
        joined = "".join(text for _channel, text in deltas)
        for fragment in plant.raw_fragments:
            assert fragment not in joined, (
                f"{tag}: raw fragment {fragment!r} reached the provider "
                f"(concatenated deltas): {deltas!r}"
            )
        assert REDACTED in joined, f"{tag}: no {REDACTED} in provider deltas {deltas!r}"
        # The per-channel equality pins Property 3's clause 3 (each delta holds ONLY its
        # own sanitized bytes) AND clause 1 (the provider body == apply_and_verify's
        # transformed deltas): ``plant.expected`` IS that oracle for a single contiguous
        # value redacted to ``[REDACTED]`` with the ``_resplit`` straddle behaviour.
        assert deltas == plant.expected, (
            f"{tag}: each delta must hold exactly its own sanitized bytes: "
            f"got {deltas!r}, expected {plant.expected!r}"
        )

        stream_modes[stream] += 1
        split_cases += int(plant.split)
        multi_message_cases += int(plant.multi_message)
        system_cases += int(plant.system)

    elapsed = time.perf_counter() - started

    # Coverage counters, asserted non-trivial so a green run is not vacuous.
    assert stream_modes[False] > 0, f"seed={SEED:#x}: non-streaming never exercised"
    assert stream_modes[True] > 0, f"seed={SEED:#x}: streaming never exercised"
    assert split_cases > 100, (
        f"seed={SEED:#x}: too few split-across-parts cases ({split_cases})"
    )
    assert multi_message_cases > 100, (
        f"seed={SEED:#x}: too few multi-message cases ({multi_message_cases})"
    )
    assert system_cases > 100, (
        f"seed={SEED:#x}: too few system-message cases ({system_cases})"
    )
    print(
        f"\n[v2-leaks-a-prop] seed={SEED:#x} iterations={ITERATIONS} "
        f"wall={elapsed:.1f}s stream={stream_modes[True]} json={stream_modes[False]} "
        f"split={split_cases} multi_message={multi_message_cases} system={system_cases}"
    )
