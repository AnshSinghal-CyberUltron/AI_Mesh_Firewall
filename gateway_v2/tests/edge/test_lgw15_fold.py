# Feature: chat-surface-gw15, Property 2: Input-channel fold is total (C21)
# Validates: Requirements 1.2
"""Property 2 — the input-channel fold is total over ``TEXT_CHANNELS`` (C21).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any well-formed SDK request and *any* input ``TextChannel``, a canary placed in
that channel appears in the folded ``input_channels`` of the ``CanonicalRequest``, so
every Input_Channel is enumerated into scannable text. The canary is iterated over
EVERY input ``TextChannel`` entry (the four ``messages[]``-scoped names), so a dropped
folding path for any one channel fails the gate.

The channel set under test is derived from the authoritative ``TEXT_CHANNELS`` tuple
(the input-scoped entries, ``sdk_path`` beginning ``messages[]``), NOT a hand-copied
list, so a new input channel added to the enumeration without a case here fails the
coverage assertion rather than silently escaping the test.
"""

from __future__ import annotations

import json
import random

from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.domain.plan import Surface
from gateway_v2.edge.wire.openai import OpenAIRequestParser
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_SEED = 0x6C_6757_0002
_ITERATIONS = 10_000


def _contract() -> ResourceContract:
    """A serviceable contract so ``parse`` can read its token-field parse boundary.

    The fold-totality invariant this suite checks is independent of the contract,
    so any serviceable ``from_signals`` contract suffices.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=2 * 1024 * 1024 * 1024,
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


_CONTRACT = _contract()

#: The ``TextChannel.sdk_path`` prefix that marks an INPUT (request-side) channel;
#: mirrors ``edge/wire/openai._INPUT_SDK_PATH_PREFIX`` so the test keys on the same
#: scope boundary the fold's totality check does.
_INPUT_SDK_PATH_PREFIX = "messages[]"

#: Alphabet for canary synthesis — printable, SDK-safe, and distinctive so a canary
#: that leaks across channels is unambiguous in a counterexample.
_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _canary(rng: random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler text."""
    n = rng.randint(4, 24)
    return "CANARY_" + "".join(rng.choice(_CANARY_ALPHABET) for _ in range(n))


def _input_channel_names() -> tuple[str, ...]:
    """The input-scoped ``TEXT_CHANNELS`` names, from the authoritative enumeration."""
    return tuple(
        channel.name
        for channel in TEXT_CHANNELS
        if channel.sdk_path.startswith(_INPUT_SDK_PATH_PREFIX)
    )


def _request_with_canary(channel_name: str, canary: str) -> dict[str, object]:
    """A well-formed OpenAI request body placing ``canary`` in exactly ``channel_name``.

    One message carries the canary in the field the named input ``TextChannel`` maps
    to; the request is otherwise minimal and well-formed (``model`` present, ``messages``
    a list of objects) so the parser accepts it and the fold walks it.
    """
    if channel_name == "message_content":
        message: dict[str, object] = {"role": "user", "content": canary}
    elif channel_name == "content_part_text":
        message = {
            "role": "user",
            "content": [{"type": "text", "text": canary}],
        }
    elif channel_name == "tool_arguments":
        message = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "f", "arguments": canary},
                }
            ],
        }
    elif channel_name == "function_arguments":
        message = {
            "role": "assistant",
            "function_call": {"name": "f", "arguments": canary},
        }
    else:  # pragma: no cover - guards against a new input channel with no case here
        raise AssertionError(f"no canary placement for input channel {channel_name!r}")
    return {"model": "gpt-test", "messages": [message]}


def test_property_2_input_channel_fold_is_total() -> None:
    rng = random.Random(_SEED)
    parser = OpenAIRequestParser()
    input_channels = _input_channel_names()

    # Coverage guard: every input-scoped TEXT_CHANNELS entry must have a canary
    # placement below, so a new input channel without a case fails the gate (C21).
    for name in input_channels:
        _request_with_canary(name, "CANARY_probe")
    assert input_channels, _ctx(_SEED, -1, note="no input channels enumerated")

    for i in range(_ITERATIONS):
        # Iterate the canary over EVERY input TextChannel entry each iteration so a
        # dropped folding path for ANY single channel fails the gate.
        for channel_name in input_channels:
            canary = _canary(rng)
            body = _request_with_canary(channel_name, canary)
            parsed = parser.parse(_dumps(body), surface=Surface.CHAT, contract=_CONTRACT)

            # The canary appears in the folded input_channels under the matching
            # channel name — the fold enumerated it into scannable text (R1.2, C21).
            folded = parsed.input_channels
            assert (channel_name, canary) in folded, _ctx(
                _SEED,
                i,
                note="canary missing from folded input_channels",
                channel=channel_name,
                canary=canary,
                folded=folded,
            )

            # And the canary text is reachable by its channel name specifically — a
            # dropped folding path for this channel would omit this pair.
            texts_for_channel = tuple(
                text for name, text in folded if name == channel_name
            )
            assert canary in texts_for_channel, _ctx(
                _SEED,
                i,
                note="canary not folded under its own channel name",
                channel=channel_name,
                canary=canary,
                texts=texts_for_channel,
            )


def _dumps(body: dict[str, object]) -> bytes:
    return json.dumps(body).encode("utf-8")
