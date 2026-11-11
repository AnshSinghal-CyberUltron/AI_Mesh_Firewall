# Feature: chat-surface-gw16-misc, Property 4: Misc input fold is total and ordered (C21)
# Validates: Requirements 3.2, 4.1, 8.1
"""Property 4 — the misc input fold is total over the misc ``TEXT_CHANNELS`` and
preserves input index order (C21).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.
Mirrors ``tests/edge/test_lgw15_fold.py`` in structure, scoped to the misc
``input`` / ``input[]`` channels rather than the chat ``messages[]`` channels.

TOTALITY (C21). For any ordered misc ``input_items`` tuple and *either* misc
input ``TextChannel`` (``embeddings_input`` / ``moderations_input``), a canary
placed in any item position appears as its own ``(channel, text)`` pair in the
fold — one pair per element, no element skipped, no element double-counted. The
canary is iterated over EVERY misc input channel AND over EVERY item position
(single string, mid-batch, head, tail, empty-list neighbours), so a dropped or
duplicated folding path for any channel/position fails the gate.

ORDER (R3.2). The folded sequence preserves input index order: the text at folded
position ``k`` equals ``items[k]`` for every ``k`` (batch item 0 before 1 before
2 ...), and the fold length equals the item count exactly (totality — each item
folded exactly once).

The channel set under test is derived from the authoritative ``TEXT_CHANNELS``
tuple (the misc-scoped entries — ``sdk_path`` beginning with neither ``messages[]``
nor ``choices[]``), NOT a hand-copied list, so a new misc input channel added to
the enumeration without a case here fails the coverage assertion rather than
silently escaping the test.
"""

from __future__ import annotations

import random

from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.edge.wire.openai_misc import fold_misc_input

_SEED = 0x6C_6757_1604
_ITERATIONS = 10_000

#: The ``TextChannel.sdk_path`` prefixes that mark a NON-misc channel; mirrors
#: ``edge/wire/openai_misc._NON_MISC_SDK_PATH_PREFIXES`` so the test keys on the
#: same scope boundary the misc fold's totality check does. ``messages[]`` is
#: GW15's chat-input scope, ``choices[]`` is the output scope and ``mcp:`` is the GW17
#: MCP scope; a misc channel begins with none of them (``input`` / ``input[]``).
_NON_MISC_SDK_PATH_PREFIXES = ("messages[]", "choices[]", "mcp:")

#: Alphabet for canary synthesis — printable, SDK-safe, and distinctive so a
#: canary that leaks across positions is unambiguous in a counterexample.
_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _canary(rng: random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler text."""
    n = rng.randint(4, 24)
    return "CANARY_" + "".join(rng.choice(_CANARY_ALPHABET) for _ in range(n))


def _filler(rng: random.Random) -> str:
    """A non-canary filler item; may legitimately collide only by chance."""
    n = rng.randint(0, 16)
    return "".join(rng.choice(_CANARY_ALPHABET) for _ in range(n))


def _misc_channel_names() -> tuple[str, ...]:
    """The misc-scoped ``TEXT_CHANNELS`` names, from the authoritative enumeration."""
    return tuple(
        channel.name
        for channel in TEXT_CHANNELS
        if not channel.sdk_path.startswith(_NON_MISC_SDK_PATH_PREFIXES)
    )


def test_property_4_misc_input_fold_is_total_and_ordered() -> None:
    rng = random.Random(_SEED)
    misc_channels = _misc_channel_names()

    # Coverage guard: at least one misc input channel must be enumerated, else the
    # inner loop never runs and the property is vacuously (and wrongly) satisfied.
    assert misc_channels, _ctx(_SEED, -1, note="no misc channels enumerated")

    for i in range(_ITERATIONS):
        # Iterate over EVERY misc input channel each iteration so a dropped folding
        # path for ANY single channel fails the gate (C21).
        for channel_name in misc_channels:
            # A request shape of varied size: empty list, a single item, or a batch
            # of 2..6 items (single-string requests fold to a one-item tuple, so a
            # one-element tuple exercises that shape too).
            size = rng.randint(0, 6)
            items = tuple(_filler(rng) for _ in range(size))

            # Place a canary at a random position so totality + order are checked at
            # the head, tail, and mid-batch across iterations.
            canary_pos: int | None = None
            if size > 0:
                canary_pos = rng.randrange(size)
                canary = _canary(rng)
                items = items[:canary_pos] + (canary,) + items[canary_pos + 1 :]

            folded = fold_misc_input(items, channel_name)

            # TOTALITY: exactly one folded pair per input item — none skipped, none
            # double-counted (the fold length equals the item count).
            assert len(folded) == len(items), _ctx(
                _SEED,
                i,
                note="fold length does not equal item count (totality)",
                channel=channel_name,
                items=items,
                folded=folded,
            )

            # ORDER + channel-keying: folded position k is (channel, items[k]) for
            # every k — the fold preserves input index order and keys every pair on
            # the requested channel name.
            for k, (name, text) in enumerate(folded):
                assert name == channel_name, _ctx(
                    _SEED,
                    i,
                    note="folded pair keyed on the wrong channel",
                    channel=channel_name,
                    position=k,
                    got=name,
                )
                assert text == items[k], _ctx(
                    _SEED,
                    i,
                    note="fold does not preserve input index order",
                    channel=channel_name,
                    position=k,
                    expected=items[k],
                    got=text,
                )

            # The canary (when placed) appears exactly once and at its input index —
            # a dropped or duplicated folding path for its position fails here.
            if canary_pos is not None:
                occurrences = tuple(
                    k for k, (_, text) in enumerate(folded) if text == items[canary_pos]
                )
                assert canary_pos in occurrences, _ctx(
                    _SEED,
                    i,
                    note="canary missing from its input position in the fold",
                    channel=channel_name,
                    canary_pos=canary_pos,
                    folded=folded,
                )
                # The canary token is distinctive, so it must not appear at any OTHER
                # position (no double-counting / cross-position leak).
                assert occurrences == (canary_pos,), _ctx(
                    _SEED,
                    i,
                    note="canary folded at an unexpected position (double-counted)",
                    channel=channel_name,
                    canary_pos=canary_pos,
                    occurrences=occurrences,
                )
