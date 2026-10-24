# Feature: chat-surface-gw15, Property 1: Parse/render round-trip preserves channel content and the stream flag  # noqa: E501
# Validates: Requirements 1.1, 1.3
"""Property 1 — parse/render round-trip preserves channel content + the stream flag.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

*For any* well-formed SDK request body, parsing it into a :class:`CanonicalRequest`
and rendering it back preserves every channel's text byte-for-byte and preserves the
streaming disposition exactly as the body's ``stream`` flag.

Two invariants are checked each iteration:

* **Stream flag (R1.3).** ``parse(body).streaming`` equals the body's ``stream`` flag
  exactly — ``True`` iff the body carried ``"stream": true``, ``False`` when the key
  was ``false`` or absent (the OpenAI default). The disposition is never guessed from
  a truthy value.
* **Channel content byte-for-byte (R1.1 / R9.1).** The channel text the parser folds
  out of the body, fed back through :meth:`OpenAIResponseRenderer.non_stream_body` and
  re-parsed from the rendered JSON, is byte-identical to the originally supplied text.
  The renderer concatenates per render-channel in document order, so the round-trip is
  checked against the expected per-channel concatenation (``ensure_ascii`` escaping is
  reversed by ``json.loads`` on the re-parse, so the comparison is on the decoded
  Python ``str``, i.e. the exact code points supplied).

The generator is a smart one: it emits only well-formed OpenAI-compatible bodies, draws
texts from a corpus that includes empty strings, ASCII, non-ASCII/unicode, JSON-significant
characters (quotes, backslashes, braces), and whitespace, and exercises every input
``TextChannel`` folding path plus every render output channel.
"""

from __future__ import annotations

import json
import random

from gateway_v2.domain.plan import Surface
from gateway_v2.edge.wire.openai import (
    CanonicalRequest,
    OpenAIRequestParser,
    OpenAIResponseRenderer,
)
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_SEED = 0x16_15_0001
_ITERATIONS = 10_000

_PARSER = OpenAIRequestParser()
_RENDERER = OpenAIResponseRenderer()


def _contract() -> ResourceContract:
    """A serviceable contract (the shared ``from_signals`` fixture shape the edge tests use).

    ``parse`` reads ``max_output_tokens_boundary()`` off it to validate the token
    fields; the value-code / stream / channel invariants this suite checks are
    unaffected by the contract, so any serviceable contract suffices.
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

# A corpus that stresses byte-for-byte preservation: empties, ASCII, unicode (so the
# renderer's ``ensure_ascii`` escaping must round-trip), JSON-significant characters,
# control/whitespace, and emoji (astral code points -> surrogate escapes in the wire).
_TEXT_CORPUS: tuple[str, ...] = (
    "",
    "hello world",
    "SSN 123-45-6789",
    'quote " and backslash \\ and brace }{',
    "unicode: café — naïve © Ω λ",
    "日本語テキスト",
    "emoji 🙂🚀 end",
    "line1\nline2\ttab\r\n",
    "   leading and trailing   ",
    '{"nested":"json","as":"text"}',
    "a" * 64,
)

# The render output channels the non-stream renderer folds into the response body, with
# the response JSON path a re-parse reads each back out of (see ``OpenAIResponseRenderer``).
_OUTPUT_CONTENT_CHANNEL = "output_content"
_OUTPUT_REASONING_CHANNEL = "output_reasoning"
_OUTPUT_REFUSAL_CHANNEL = "output_refusal"
_OUTPUT_TOOL_ARGUMENTS_CHANNEL = "output_tool_arguments"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _text(rng: random.Random) -> str:
    return rng.choice(_TEXT_CORPUS)


def _gen_body(rng: random.Random) -> tuple[dict[str, object], bool]:
    """Emit a well-formed OpenAI body + the stream flag it carries.

    Exercises every input ``TextChannel`` folding path across a random number of
    messages: plain-string ``content``, array-of-parts ``content[].text``,
    ``tool_calls[].function.arguments``, and the legacy ``function_call.arguments``.
    The ``stream`` key is present-true, present-false, or absent (default false), so
    both dispositions and the default are covered.
    """
    messages: list[dict[str, object]] = []
    for _ in range(rng.randint(0, 4)):
        message: dict[str, object] = {"role": rng.choice(("user", "assistant", "system"))}
        shape = rng.randint(0, 3)
        if shape == 0:
            message["content"] = _text(rng)
        elif shape == 1:
            parts: list[dict[str, object]] = [
                {"type": "text", "text": _text(rng)} for _ in range(rng.randint(1, 3))
            ]
            message["content"] = parts
        elif shape == 2:
            message["tool_calls"] = [
                {
                    "id": f"call_{j}",
                    "type": "function",
                    "function": {"name": "f", "arguments": _text(rng)},
                }
                for j in range(rng.randint(1, 3))
            ]
        else:
            message["function_call"] = {"name": "legacy", "arguments": _text(rng)}
        messages.append(message)

    body: dict[str, object] = {
        "model": rng.choice(("gpt-4o", "gpt-4.1", "o3")),
        "messages": messages,
    }

    stream_mode = rng.randint(0, 2)
    if stream_mode == 0:
        body["stream"] = True
        stream_flag = True
    elif stream_mode == 1:
        body["stream"] = False
        stream_flag = False
    else:  # absent => OpenAI default is non-streaming
        stream_flag = False
    return body, stream_flag


def _render_deltas(rng: random.Random, parsed: CanonicalRequest) -> tuple[tuple[str, str], ...]:
    """Map the parsed input channels onto render output channels (document order).

    Each parsed input-channel text is re-emitted under a render output channel so the
    renderer must carry it byte-for-byte into the response JSON. We route every input
    pair to ``output_content`` and additionally sprinkle in reasoning/refusal/tool
    channels so every render fold path is covered across the run.
    """
    deltas: list[tuple[str, str]] = []
    for _channel, text in parsed.input_channels:
        deltas.append((_OUTPUT_CONTENT_CHANNEL, text))
    extra = rng.randint(0, 3)
    if extra == 1:
        deltas.append((_OUTPUT_REASONING_CHANNEL, _text(rng)))
    elif extra == 2:
        deltas.append((_OUTPUT_REFUSAL_CHANNEL, _text(rng)))
    elif extra == 3:
        deltas.append((_OUTPUT_TOOL_ARGUMENTS_CHANNEL, _text(rng)))
    return tuple(deltas)


def _expected_concat(deltas: tuple[tuple[str, str], ...], channel: str) -> str:
    return "".join(text for ch, text in deltas if ch == channel)


def test_property_1_parse_render_roundtrip_preserves_content_and_stream_flag() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        body_obj, stream_flag = _gen_body(rng)
        raw = json.dumps(body_obj).encode("utf-8")

        parsed = _PARSER.parse(raw, surface=Surface.CHAT, contract=_CONTRACT)

        # --- Stream flag preserved exactly as the body ``stream`` flag (R1.3). -------
        assert parsed.streaming is stream_flag, _ctx(
            _SEED, i, note="stream flag mismatch", got=parsed.streaming, want=stream_flag
        )

        # The parser mirrors input_channels into text_deltas unchanged, so the folded
        # channel content survived parsing byte-for-byte.
        assert parsed.text_deltas == parsed.input_channels, _ctx(
            _SEED, i, note="text_deltas diverged from input_channels"
        )

        # --- Channel content byte-for-byte through render + re-parse (R1.1/R9.1). ----
        deltas = _render_deltas(rng, parsed)
        rendered = _RENDERER.non_stream_body(parsed, deltas)
        reparsed = json.loads(rendered)

        message = reparsed["choices"][0]["message"]

        # content: concatenation of every input-channel text, byte-identical.
        want_content = _expected_concat(deltas, _OUTPUT_CONTENT_CHANNEL)
        assert message["content"] == want_content, _ctx(
            _SEED, i, note="content not preserved", got=message.get("content"), want=want_content
        )

        # reasoning_content / refusal appear only when their channel carried non-empty
        # text (the renderer omits empty optional fields); when present, byte-identical.
        want_reasoning = _expected_concat(deltas, _OUTPUT_REASONING_CHANNEL)
        if want_reasoning:
            assert message["reasoning_content"] == want_reasoning, _ctx(
                _SEED, i, note="reasoning not preserved", got=message.get("reasoning_content")
            )
        else:
            assert "reasoning_content" not in message, _ctx(
                _SEED, i, note="unexpected reasoning_content field"
            )

        want_refusal = _expected_concat(deltas, _OUTPUT_REFUSAL_CHANNEL)
        if want_refusal:
            assert message["refusal"] == want_refusal, _ctx(
                _SEED, i, note="refusal not preserved", got=message.get("refusal")
            )
        else:
            assert "refusal" not in message, _ctx(
                _SEED, i, note="unexpected refusal field"
            )

        # tool_calls arguments: concatenation byte-identical when carried.
        want_tool_args = _expected_concat(deltas, _OUTPUT_TOOL_ARGUMENTS_CHANNEL)
        if want_tool_args:
            got_args = message["tool_calls"][0]["function"]["arguments"]
            assert got_args == want_tool_args, _ctx(
                _SEED, i, note="tool arguments not preserved", got=got_args, want=want_tool_args
            )
        else:
            assert "tool_calls" not in message, _ctx(
                _SEED, i, note="unexpected tool_calls field"
            )

        # The routing model survives into the rendered body (R1.1 routing field).
        assert reparsed["model"] == parsed.model, _ctx(
            _SEED, i, note="model not preserved", got=reparsed.get("model"), want=parsed.model
        )

# =========================================================================== #
# Task 1.6 — malformed-body unit tests (example tests, NOT a property test).
# =========================================================================== #
#
# # Feature: chat-surface-gw15
# # Validates: Requirements 1.4
#
# These are *example* tests (no >= 10,000-iteration requirement). They assert
# that every malformed / off-contract request body raises ``MalformedRequest``
# carrying a *declared value-code* (one of the ``MALFORMED_*`` constants) and
# never leaks a raw framework error (``json.JSONDecodeError`` / ``KeyError`` /
# ``UnicodeDecodeError`` / ``ValueError`` / ``TypeError``) past the parser
# boundary (R1.4). Each variant pins the EXACT declared code so a code rename or
# a widened/narrowed shape check is caught here.
#
# Everything task 1.6 owns is uniquely prefixed ``_mb_`` / ``MB_`` so it never
# clashes with the Property-1 round-trip test above. The imports below are local
# to this section (Python allows mid-module imports) so this block is additive
# and does not touch task 1.4's top-of-file import list.
#
# _Design: Testing Strategy (edge cases), Error Handling table (malformed body)._

import pytest  # noqa: E402

from gateway_v2.edge.wire.openai import (  # noqa: E402
    MALFORMED_BODY,
    MALFORMED_MISSING_MODEL,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
    MalformedRequest,
)

#: One shared stateless parser — the parser is pure/stateless (``__slots__ = ()``),
#: so reuse across every case is safe and mirrors the single shared boot instance.
MB_PARSER = OpenAIRequestParser()

#: The surface the parser is called with. The malformed-body checks are
#: surface-independent (shape validation precedes any surface branch), so CHAT is
#: used throughout.
MB_SURFACE = Surface.CHAT


def _mb_parse_raising(body: bytes) -> MalformedRequest:
    """Parse ``body`` expecting a :class:`MalformedRequest`; return the exception.

    Fails the test if the parser does NOT raise ``MalformedRequest`` — a body that
    silently parses (or leaks a different exception type) is itself the bug this
    suite guards against.
    """
    with pytest.raises(MalformedRequest) as excinfo:
        MB_PARSER.parse(body, surface=MB_SURFACE, contract=_CONTRACT)
    return excinfo.value


# --- Not valid JSON -> MALFORMED_BODY. ------------------------------------- #


def test_mb_empty_body_raises_malformed_body() -> None:
    """An empty byte string is not valid JSON -> declared ``MALFORMED_BODY``."""
    assert _mb_parse_raising(b"").code == MALFORMED_BODY


def test_mb_non_json_text_raises_malformed_body() -> None:
    """Arbitrary non-JSON bytes -> ``MALFORMED_BODY`` (no raw ``JSONDecodeError``)."""
    assert _mb_parse_raising(b"this is not json").code == MALFORMED_BODY


def test_mb_truncated_json_raises_malformed_body() -> None:
    """A truncated / unterminated JSON object -> ``MALFORMED_BODY``."""
    assert _mb_parse_raising(b'{"model": "gpt-4"').code == MALFORMED_BODY


def test_mb_trailing_garbage_after_json_raises_malformed_body() -> None:
    """Valid JSON followed by trailing garbage is not a valid document -> body."""
    assert _mb_parse_raising(b'{"model": "gpt-4"} and then some').code == MALFORMED_BODY


def test_mb_invalid_utf8_raises_malformed_body() -> None:
    """Non-UTF-8 bytes -> ``MALFORMED_BODY`` (no raw ``UnicodeDecodeError``)."""
    assert _mb_parse_raising(b"\xff\xfe\x00\x01").code == MALFORMED_BODY


# --- JSON, but top level is not an object -> MALFORMED_NOT_AN_OBJECT. ------- #


def test_mb_json_array_top_level_raises_not_an_object() -> None:
    """A top-level JSON array is valid JSON but off-contract -> not-an-object."""
    assert _mb_parse_raising(b'[{"model": "gpt-4"}]').code == MALFORMED_NOT_AN_OBJECT


def test_mb_json_string_top_level_raises_not_an_object() -> None:
    """A top-level JSON string -> ``MALFORMED_NOT_AN_OBJECT``."""
    assert _mb_parse_raising(b'"gpt-4"').code == MALFORMED_NOT_AN_OBJECT


def test_mb_json_number_top_level_raises_not_an_object() -> None:
    """A top-level JSON number -> ``MALFORMED_NOT_AN_OBJECT``."""
    assert _mb_parse_raising(b"42").code == MALFORMED_NOT_AN_OBJECT


def test_mb_json_null_top_level_raises_not_an_object() -> None:
    """A top-level JSON ``null`` -> ``MALFORMED_NOT_AN_OBJECT``."""
    assert _mb_parse_raising(b"null").code == MALFORMED_NOT_AN_OBJECT


def test_mb_json_bool_top_level_raises_not_an_object() -> None:
    """A top-level JSON boolean -> ``MALFORMED_NOT_AN_OBJECT``."""
    assert _mb_parse_raising(b"true").code == MALFORMED_NOT_AN_OBJECT


# --- Required routing field absent -> MALFORMED_MISSING_MODEL. ------------- #


def test_mb_missing_model_raises_missing_model() -> None:
    """A JSON object with no ``model`` key -> declared ``MALFORMED_MISSING_MODEL``."""
    assert _mb_parse_raising(b'{"messages": []}').code == MALFORMED_MISSING_MODEL


def test_mb_empty_object_raises_missing_model() -> None:
    """An empty JSON object has no ``model`` -> ``MALFORMED_MISSING_MODEL``."""
    assert _mb_parse_raising(b"{}").code == MALFORMED_MISSING_MODEL


# --- Present field, wrong type -> MALFORMED_WRONG_SHAPE. ------------------- #


def test_mb_model_not_a_string_raises_wrong_shape() -> None:
    """``model`` present but not a string -> declared ``MALFORMED_WRONG_SHAPE``."""
    assert _mb_parse_raising(b'{"model": 123}').code == MALFORMED_WRONG_SHAPE


def test_mb_model_null_raises_wrong_shape() -> None:
    """``model`` present as ``null`` (not a string) -> ``MALFORMED_WRONG_SHAPE``."""
    assert _mb_parse_raising(b'{"model": null}').code == MALFORMED_WRONG_SHAPE


def test_mb_stream_not_a_bool_raises_wrong_shape() -> None:
    """``stream`` present but not a bool -> ``MALFORMED_WRONG_SHAPE`` (never coerced)."""
    body = b'{"model": "gpt-4", "stream": "yes"}'
    assert _mb_parse_raising(body).code == MALFORMED_WRONG_SHAPE


def test_mb_stream_number_raises_wrong_shape() -> None:
    """A truthy non-bool ``stream`` (``1``) -> ``MALFORMED_WRONG_SHAPE``, not coerced true."""
    assert _mb_parse_raising(b'{"model": "gpt-4", "stream": 1}').code == MALFORMED_WRONG_SHAPE


def test_mb_messages_not_a_list_raises_wrong_shape() -> None:
    """``messages`` present but not a list -> ``MALFORMED_WRONG_SHAPE``."""
    body = b'{"model": "gpt-4", "messages": "hello"}'
    assert _mb_parse_raising(body).code == MALFORMED_WRONG_SHAPE


def test_mb_messages_object_not_a_list_raises_wrong_shape() -> None:
    """``messages`` present as an object (not a list) -> ``MALFORMED_WRONG_SHAPE``."""
    body = b'{"model": "gpt-4", "messages": {"role": "user"}}'
    assert _mb_parse_raising(body).code == MALFORMED_WRONG_SHAPE


def test_mb_message_element_not_an_object_raises_wrong_shape() -> None:
    """A ``messages`` element that is not an object -> ``MALFORMED_WRONG_SHAPE``."""
    body = b'{"model": "gpt-4", "messages": ["not an object"]}'
    assert _mb_parse_raising(body).code == MALFORMED_WRONG_SHAPE


def test_mb_message_null_element_raises_wrong_shape() -> None:
    """A ``null`` ``messages`` element (not an object) -> ``MALFORMED_WRONG_SHAPE``."""
    body = b'{"model": "gpt-4", "messages": [null]}'
    assert _mb_parse_raising(body).code == MALFORMED_WRONG_SHAPE


# --- Cross-cutting guarantees over every malformed variant (R1.4). --------- #

#: Every malformed/off-contract body variant this suite exercises, paired with the
#: EXACT declared value-code the parser must surface. A tuple (not a mutable list)
#: at module level keeps the suite free of module-level mutable state.
MB_MALFORMED_CASES: tuple[tuple[bytes, str], ...] = (
    (b"", MALFORMED_BODY),
    (b"this is not json", MALFORMED_BODY),
    (b'{"model": "gpt-4"', MALFORMED_BODY),
    (b'{"model": "gpt-4"} trailing', MALFORMED_BODY),
    (b"\xff\xfe\x00\x01", MALFORMED_BODY),
    (b'[{"model": "gpt-4"}]', MALFORMED_NOT_AN_OBJECT),
    (b'"gpt-4"', MALFORMED_NOT_AN_OBJECT),
    (b"42", MALFORMED_NOT_AN_OBJECT),
    (b"null", MALFORMED_NOT_AN_OBJECT),
    (b"true", MALFORMED_NOT_AN_OBJECT),
    (b'{"messages": []}', MALFORMED_MISSING_MODEL),
    (b"{}", MALFORMED_MISSING_MODEL),
    (b'{"model": 123}', MALFORMED_WRONG_SHAPE),
    (b'{"model": null}', MALFORMED_WRONG_SHAPE),
    (b'{"model": "gpt-4", "stream": "yes"}', MALFORMED_WRONG_SHAPE),
    (b'{"model": "gpt-4", "stream": 1}', MALFORMED_WRONG_SHAPE),
    (b'{"model": "gpt-4", "messages": "hello"}', MALFORMED_WRONG_SHAPE),
    (b'{"model": "gpt-4", "messages": {"role": "user"}}', MALFORMED_WRONG_SHAPE),
    (b'{"model": "gpt-4", "messages": ["not an object"]}', MALFORMED_WRONG_SHAPE),
    (b'{"model": "gpt-4", "messages": [null]}', MALFORMED_WRONG_SHAPE),
)

#: The complete set of declared malformed value-codes a malformed body may carry.
#: A surfaced code NOT in this set would be an undeclared (non-value) code — the
#: exact leak R1.4 forbids.
MB_DECLARED_CODES = frozenset(
    (
        MALFORMED_BODY,
        MALFORMED_NOT_AN_OBJECT,
        MALFORMED_MISSING_MODEL,
        MALFORMED_WRONG_SHAPE,
    )
)


@pytest.mark.parametrize(("body", "expected_code"), MB_MALFORMED_CASES)
def test_mb_variant_raises_declared_code(body: bytes, expected_code: str) -> None:
    """Every malformed variant raises ``MalformedRequest`` with its declared code.

    The table-driven form of the per-case tests above: pins each off-contract body
    to the EXACT value-code the parser must surface, so a widened/narrowed shape
    check or a code rename is caught at one site (R1.4).
    """
    assert _mb_parse_raising(body).code == expected_code


@pytest.mark.parametrize(("body", "expected_code"), MB_MALFORMED_CASES)
def test_mb_code_is_a_declared_value_code(body: bytes, expected_code: str) -> None:
    """Every surfaced code is one of the declared value-codes, never ad-hoc (R1.4)."""
    assert _mb_parse_raising(body).code in MB_DECLARED_CODES


@pytest.mark.parametrize("body", [case[0] for case in MB_MALFORMED_CASES])
def test_mb_no_raw_framework_error_leaks(body: bytes) -> None:
    """A malformed body never leaks a raw framework error past the parser (R1.4).

    The parser must translate every ``json``/decode/shape failure into a
    :class:`MalformedRequest`; a raw ``json.JSONDecodeError``, ``ValueError``,
    ``UnicodeDecodeError``, ``KeyError``, or ``TypeError`` escaping the parse
    boundary is the leak this guards against. ``MalformedRequest`` subclasses
    ``Exception`` but NONE of those framework types, so a leaked framework error
    fails here rather than being swallowed as a ``MalformedRequest``.
    """
    try:
        MB_PARSER.parse(body, surface=MB_SURFACE, contract=_CONTRACT)
    except MalformedRequest:
        return
    except (
        json.JSONDecodeError,
        ValueError,
        UnicodeDecodeError,
        KeyError,
        TypeError,
    ) as exc:  # pragma: no cover - a leak here is a test failure, not a pass path
        pytest.fail(f"raw framework error leaked past the parser: {exc!r}")
    pytest.fail("malformed body did not raise MalformedRequest")
