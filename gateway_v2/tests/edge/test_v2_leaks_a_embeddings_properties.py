"""v2-enforcement-leaks Group A (embeddings): Property 15 as a seeded property.

Fix-checking property test (spec ``v2-enforcement-leaks``, task 28.3; design Property 15, Bug
Condition A embeddings). It GENERALISES the three example cases of the sibling exploration file
``tests/edge/test_v2_leaks_a_embeddings_redact.py`` to 10,000 random ``/v1/embeddings`` requests
driven through the REAL dev-profile ASGI surface (``AMF_DEV_PROFILE=1`` + ``dev/plan.example.json``:
pii -> redact, secret/credential -> block, every rule ``scope: both`` on chat + embeddings), with a
recording provider swapped in that returns one vector per forwarded delta so every request
completes 200.

Each iteration constructs a STRING input or a LIST input of 0..32 items. A random subset of the
items carries a planted REDACT-category PII value (an SSN or an email -- both resolve to a REDACT
disposition with NO BLOCK, so the request proceeds, admitted and dispatched) at a random position;
the surrounding and the clean items are plain letter words so the only finding in any item is the
planted PII (no accidental phone / card / secret / credential match, which would BLOCK the request
or shift a span). The provider body is then asserted against Property 15:

1. ONE ``("embeddings_input", item)`` delta per input item, in request order;
2. a STRING input gives exactly one delta;
3. every planted raw value is ABSENT from every delta (each finding's bytes become ``[REDACTED]``);
4. every clean item (no planted value) is BYTE-IDENTICAL to the parsed item;
5. no item's planted bytes appear in ANOTHER item's delta (per-item masking does not bleed).

An EMPTY list (``input: []``) is a valid shape and is included: it yields a single provider call
carrying ZERO deltas and a trivial 200, handled explicitly. A single-item list and a string are
also included. The seed is in every assertion message; the coverage counters below are asserted
non-trivial so the generator is shown to exercise every shape.

The dev app is STATELESS per request (admission completes on every exit path once Group A/D land),
so the full boot + assemble + request is re-run per iteration from one ``random.Random(seed)``;
booting is sub-millisecond (see the module wall-time note), so 10,000 iterations stay well under a
few minutes.

**Validates: Requirements 1.18, 2.19, 2.20**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from random import Random

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# Same dev plan, key and contract as the sibling example test (kept local so this file
# type-checks on its own under ``mypy --strict``).
EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
KEY = "sk-dev-pipeline"
REDACTED = "[REDACTED]"
CHANNEL = "embeddings_input"

#: The seed for the one ``random.Random`` driving every iteration. In every assertion message.
SEED = 20260717
#: At least this many random requests (house idiom: seeded, >= 10,000 iterations, no hypothesis).
ITERATIONS = 10_000
#: The most items a list input may carry (0..32 inclusive).
MAX_LIST_ITEMS = 32

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
    """Records the exact ``(channel, text)`` deltas of every body, replies one vector per delta.

    ``EmbeddingsSurface._drain_vectors`` decodes each reply delta's text as a JSON float array
    (one embedding vector, arrival order = batch index), so the reply is one scripted vector per
    forwarded item and the request completes with HTTP 200. An empty body (an ``input: []``
    request) forwards zero deltas, so the provider is opened once and the stream ends immediately
    with a single empty ``final`` event -- the shape the empty-list case asserts.
    """

    def __init__(self) -> None:
        self.received: list[tuple[Delta, ...]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        deltas = tuple((str(item[0]), str(item[1])) for item in raw)
        self.received.append(deltas)
        if not deltas:
            yield UpstreamEvent(text_deltas=(), final=True)
            return
        for index, _delta in enumerate(deltas):
            vector = json.dumps([float(index), 0.5])
            yield UpstreamEvent(text_deltas=((CHANNEL, vector),), final=index == len(deltas) - 1)

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


async def _post(app: object, embeddings_input: str | list[str]) -> int:
    body = json.dumps({"model": "echo-1", "input": embeddings_input}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/embeddings",
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


def _forwarded(embeddings_input: str | list[str]) -> tuple[Delta, ...]:
    """Drive one request through a fresh dev app; return the one recorded provider body's deltas."""
    provider = _RecordingProvider()
    status = asyncio.run(_post(_app(provider), embeddings_input))
    assert status == 200, f"seed={SEED}: an input REDACT (no BLOCK) must proceed, got {status}"
    assert len(provider.received) == 1, (
        f"seed={SEED}: exactly one provider call, got {len(provider.received)} "
        f"for input {embeddings_input!r}"
    )
    return provider.received[0]


# --------------------------------------------------------------------------- #
# Generator: a small fixed alphabet of REDACT-category PII (SSN + email). Both the dev plan's
# ``pii -> redact`` rule and no other rule fires on them, so the request resolves to REDACT (no
# BLOCK) and proceeds. The clean surrounding words are plain letters (no digit run, no ``@``, no
# ``AKIA`` / long token) so no phone / card / secret / credential ever co-matches.
# --------------------------------------------------------------------------- #

#: Plain letter words for clean filler + clean items -- nothing a detector can match.
_CLEAN_WORDS = (
    "release", "notes", "summary", "report", "draft", "review", "status",
    "update", "ticket", "backlog", "roadmap", "retro", "standup", "memo",
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
)


def _ssn(rng: Random) -> str:
    """A valid ``ddd-dd-dddd`` SSN-shaped PII value (``pii_ssn``, REDACT)."""
    return (
        f"{rng.randint(100, 999)}-{rng.randint(10, 99):02d}-{rng.randint(1000, 9999):04d}"
    )


def _email(rng: Random) -> str:
    """A conventional ``local@domain.tld`` email PII value (``pii_email``, REDACT)."""
    local = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 8)))
    domain = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 8)))
    tld = rng.choice(("com", "org", "net", "io"))
    return f"{local}@{domain}.{tld}"


def _planted_value(rng: Random) -> str:
    """One REDACT-category PII value from the fixed alphabet (SSN or email)."""
    return _ssn(rng) if rng.random() < 0.5 else _email(rng)


def _clean_words(rng: Random, count: int) -> str:
    """``count`` plain letter words joined by single spaces (never empty when count >= 1)."""
    return " ".join(rng.choice(_CLEAN_WORDS) for _ in range(count))


@dataclass(slots=True)
class _Item:
    """One generated input item: its text plus the raw planted values it carries (if any)."""

    text: str
    planted: tuple[str, ...]


def _make_item(rng: Random, *, plant: bool) -> _Item:
    """Build one item. Clean items are plain words; a planted item embeds a PII value in words.

    The planted value sits at a random position among the clean words (start, middle or end), and
    the surrounding words keep it from abutting another token into a digit run, so the only finding
    is the planted PII and the clean prefix / suffix stay byte-identical inside the masked item.
    """
    lead = _clean_words(rng, rng.randint(0, 3))
    tail = _clean_words(rng, rng.randint(0, 3))
    if not plant:
        text = (f"{lead} {tail}".strip()) or rng.choice(_CLEAN_WORDS)
        return _Item(text=text, planted=())
    value = _planted_value(rng)
    parts = [part for part in (lead, value, tail) if part]
    return _Item(text=" ".join(parts), planted=(value,))


@dataclass(slots=True)
class _Case:
    """One generated request: the wire ``input`` plus per-item planted values, in request order."""

    embeddings_input: str | list[str]
    items: tuple[_Item, ...]
    is_string: bool


def _make_case(rng: Random) -> _Case:
    """Randomise string vs list, list length 0..32, which items are planted, and the planted PII.

    A string input is one item that always carries a planted value (a clean string is covered by
    the clean-list / clean-item paths and by the single-item list branch); every list length in
    0..MAX_LIST_ITEMS is reachable, and each list item is independently planted or clean.
    """
    if rng.random() < 0.25:
        item = _make_item(rng, plant=True)
        return _Case(embeddings_input=item.text, items=(item,), is_string=True)
    length = rng.randint(0, MAX_LIST_ITEMS)
    items = tuple(_make_item(rng, plant=rng.random() < 0.5) for _ in range(length))
    return _Case(embeddings_input=[item.text for item in items], items=items, is_string=False)


@dataclass(slots=True)
class _Coverage:
    """Counters asserted non-trivial so the generator is shown to reach every Property-15 shape."""

    string_inputs: int = 0
    empty_list_inputs: int = 0
    single_item_lists: int = 0
    multi_item_lists: int = 0
    planted_items: int = 0
    clean_items: int = 0
    requests_two_plus_redacted: int = 0


def _assert_case(case: _Case) -> None:
    """Drive one case and assert Property 15 on the recorded provider deltas."""
    deltas = _forwarded(case.embeddings_input)
    items = case.items
    # (1) one delta per input item, in request order; and the empty-list shape.
    assert len(deltas) == len(items), (
        f"seed={SEED}: one delta per input item, got {len(deltas)} for {len(items)} item(s): "
        f"{deltas!r}"
    )
    # (2) a string input gives exactly one delta.
    if case.is_string:
        assert len(deltas) == 1, (
            f"seed={SEED}: a string input must give exactly one delta, got {deltas!r}"
        )
    masked_texts = [text for _channel, text in deltas]
    for position, ((channel, text), item) in enumerate(zip(deltas, items, strict=True)):
        assert channel == CHANNEL, (
            f"seed={SEED}: delta {position} channel must be {CHANNEL!r}, got {channel!r}"
        )
        if not item.planted:
            # (4) clean items are byte-identical to the parsed item.
            assert text == item.text, (
                f"seed={SEED}: clean item {position} must be byte-identical: "
                f"got {text!r}, parsed {item.text!r}"
            )
            continue
        # (3) planted raw bytes are absent; each finding's bytes became ``[REDACTED]``.
        for raw in item.planted:
            assert raw not in text, (
                f"seed={SEED}: raw {raw!r} in item {position} provider delta {(channel, text)!r}"
            )
        assert REDACTED in text, (
            f"seed={SEED}: item {position} must carry {REDACTED} after masking, got {text!r}"
        )
    # (5) no item's planted bytes appear in ANOTHER item's delta (per-item masking does not bleed).
    for owner, item in enumerate(items):
        for raw in item.planted:
            for other, text in enumerate(masked_texts):
                if other == owner:
                    continue
                assert raw not in text, (
                    f"seed={SEED}: planted {raw!r} from item {owner} bled into item {other} "
                    f"delta {text!r}"
                )


def _tally(coverage: _Coverage, case: _Case) -> None:
    """Record which Property-15 shapes this case exercised."""
    if case.is_string:
        coverage.string_inputs += 1
    else:
        length = len(case.items)
        if length == 0:
            coverage.empty_list_inputs += 1
        elif length == 1:
            coverage.single_item_lists += 1
        else:
            coverage.multi_item_lists += 1
    redacted = 0
    for item in case.items:
        if item.planted:
            coverage.planted_items += 1
            redacted += 1
        else:
            coverage.clean_items += 1
    if redacted >= 2:
        coverage.requests_two_plus_redacted += 1


def test_embeddings_redact_property_15_over_random_string_and_list_inputs() -> None:
    """Property 15 over 10,000 seeded string / list inputs: masked per item, shape preserved.

    One ``random.Random(SEED)`` drives string-vs-list, list length 0..32, which items carry a
    planted REDACT-category PII value and where, and the clean surrounding text. Each request is
    driven through the real dev-profile ASGI embeddings surface and asserted against Property 15
    (one delta per item in order; a string gives one delta; planted bytes absent; clean items
    byte-identical; no cross-item bleed). The coverage counters are asserted non-trivial so the
    generator is shown to reach every shape, including the empty-list input.

    **Validates: Requirements 1.18, 2.19, 2.20**
    """
    rng = Random(SEED)
    coverage = _Coverage()
    for _iteration in range(ITERATIONS):
        case = _make_case(rng)
        _assert_case(case)
        _tally(coverage, case)

    # Coverage: every Property-15 shape was exercised a non-trivial number of times.
    assert coverage.string_inputs > 0, f"seed={SEED}: no string inputs generated ({coverage})"
    assert coverage.empty_list_inputs > 0, f"seed={SEED}: no empty-list inputs ({coverage})"
    assert coverage.single_item_lists > 0, f"seed={SEED}: no single-item lists ({coverage})"
    assert coverage.multi_item_lists > 0, f"seed={SEED}: no multi-item lists ({coverage})"
    assert coverage.planted_items > 0, f"seed={SEED}: no items carried a planted value ({coverage})"
    assert coverage.clean_items > 0, f"seed={SEED}: no clean items ({coverage})"
    assert coverage.requests_two_plus_redacted > 0, (
        f"seed={SEED}: no request had >= 2 redacted items ({coverage})"
    )
