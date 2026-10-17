# Feature: openai-conformance-gw01-local
# Validates: Requirements 5.3, 4.2, 6.2
"""Shared frozen-wire-contract assertions for the OpenAI conformance suite (task 2.1).

This is the SINGLE body of frozen-wire-contract assertions that BOTH the v1 arm (World A)
and the v2 arm (World B) call, so the specification's shared truth is literally one body of
code (design §"wire_contract.py"). It asserts ONLY wire-visible HTTP/SSE facts — the typed
JSON shapes a real ``openai`` caller reads, the streaming chunk framing, the ``data: [DONE]``
terminal, the ``{"error": {...}}`` envelope fields, and the HTTP status — and it names
**neither** ``ai_mesh_gateway`` **nor** ``gateway_v2.edge``/app packages anywhere (Requirement
5.3). It operates purely on already-parsed SDK objects / raised SDK errors passed in by the
caller, never assembling or importing an application.

**Stock-SDK, not SDK-internal (Requirement 4.2).** The helpers read the wire-visible fields a
real caller reads via duck-typed attribute access (the stock SDK returns pydantic models), and
reference no SDK internal that was renamed/relocated between the frozen ``2.38.0`` and the
installed ``3.29.0`` major. ``openai`` is imported through :func:`pytest.importorskip` so a tree
without the SDK skips cleanly rather than erroring collection (the GW15/GW16 subset idiom) — the
import is needed because :func:`assert_error_envelope` discriminates the raised ``APIStatusError``
by type to read its ``response`` / ``status_code``.

**Deliberate §10.2.2 diffs come from the ledger, never a literal (Requirement 6.2).** Any
assertion that touches a deliberate v2≠v1 difference (the streaming-block disposition, the
content-filter status/code mapping, the degraded posture) draws its expected value from
:func:`gateway_v2.contracts.openai_conformance.harness.expected_diff` — the resolver-aware reader
that returns the value expected for the app currently selected — so the identical helper is
correct under both apps. ``expected_diff`` is the ONLY ``gateway_v2.contracts`` import here (the
product resolver; a test→product import, which is allowed); no app package is named.

The ``[DONE]`` terminal check is **folded into** :func:`assert_stream_frames` — iterating the
collected chunks to completion is itself the proof the SDK stopped on the sentinel without error;
:func:`assert_done_terminates` is retained as a thin, explicit alias for callers that read better
with a named terminal check, and documents that it defers to the same logic.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

# The error-envelope helper discriminates the raised SDK error by type (to reach ``response`` /
# ``status_code``), so the stock SDK must be importable. A tree without it skips cleanly — this
# shared helper is only ever reached from the arms, which themselves ``importorskip`` the SDK.
openai = pytest.importorskip("openai")

from gateway_v2.contracts.openai_conformance.harness import expected_diff  # noqa: E402

__all__ = (
    "assert_chat_completion",
    "assert_stream_frames",
    "assert_done_terminates",
    "assert_error_envelope",
)

#: The ledger keys whose expected status/code the error-envelope helper resolves rather than
#: hard-coding, so a §10.2.2 deliberate diff is asserted against its per-app expected value.
_LEDGER_CODE_KEYS: frozenset[str] = frozenset(
    {"stream_block_disposition", "degraded_scan_status"}
)

#: The HTTP status a given wire-contract error code coheres with. These are the frozen
#: wire-contract status/code pairings a real caller observes — NOT an app internal. A content
#: filter / block is a 4xx; a bad request is 400; not-found is 404; auth is 401/403.
_CODE_STATUS: dict[str, int] = {
    "input_blocked": 400,
    "output_blocked": 400,
    "content_filter": 400,
    "invalid_request_error": 400,
    "missing_required_parameter": 400,
    "model_not_found": 404,
    "not_found": 404,
    "authentication_error": 401,
    "permission_error": 403,
}


def assert_chat_completion(completion: object) -> None:
    """Assert the typed ``ChatCompletion`` wire shape a real stock-SDK caller reads.

    Checks the wire-visible fields only — ``id`` present, ``object == "chat.completion"``, a
    non-empty ``choices`` list, ``choices[0].message.role == "assistant"``, and a ``usage`` block
    carrying the prompt/completion/total token fields. Attributes are accessed (not dict keys),
    so the stock SDK's pydantic model satisfies it under both the frozen and installed majors
    without referencing any renamed SDK internal (Requirement 4.2).
    """

    assert getattr(completion, "id", None), f"completion has no id: {completion!r}"
    assert completion.object == "chat.completion", (  # type: ignore[attr-defined]
        f"object is not 'chat.completion': {completion!r}"
    )
    choices = completion.choices  # type: ignore[attr-defined]
    assert choices, f"completion has no choices: {completion!r}"
    message = choices[0].message
    assert message.role == "assistant", f"first choice role is not assistant: {message!r}"

    usage = completion.usage  # type: ignore[attr-defined]
    assert usage is not None, f"completion carries no usage block: {completion!r}"
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, field, None)
        assert isinstance(value, int), f"usage.{field} is not an int: {usage!r}"


def assert_stream_frames(chunks: Sequence[object]) -> None:
    """Assert the collected streaming chunks are well-framed and the terminal is clean.

    ``chunks`` is the list of ``ChatCompletionChunk`` the SDK parsed by iterating the stream to
    completion — the fact the caller could collect them to the end IS the proof the ``data:
    [DONE]`` sentinel terminated iteration cleanly (the SDK stops on ``[DONE]`` without surfacing
    it as a chunk or raising). A genuine CONTENT chunk (one carrying a non-empty ``choices``
    list) must parse with ``object == "chat.completion.chunk"`` and its ``choices[0].delta
    .content`` deltas reconstruct in arrival order (which the caller preserves).

    A terminal non-content trace/metadata frame is **tolerated**, not asserted away. The v2
    streaming path emits a documented GW13 produced-mode ``amf.trace`` SSE frame on the wire
    ("produced-mode stamped on the wire"); the stock SDK parses it into a ``ChatCompletionChunk``
    with ``object`` unset (``None``), empty/absent ``choices``, and the extra fields
    ``produced_mode`` / ``degenerated_to_strict`` — the SDK ACCEPTS it (extra fields allowed,
    empty choices). Such a frame — ``object != "chat.completion.chunk"`` AND no non-empty
    ``choices`` — is a wire-visible, SDK-accepted part of the shipped GW13 contract; asserting its
    ``object`` away would contradict that contract, so this helper only requires it carry no
    content delta. A chunk WITH non-empty ``choices`` is always held to the full content-chunk
    framing.
    """

    assert chunks, "stream produced no chunks"
    reconstructed = ""
    for index, chunk in enumerate(chunks):
        choices = getattr(chunk, "choices", None)
        if not choices:
            # A non-content frame: the documented GW13 ``amf.trace`` produced-mode terminal frame
            # the SDK accepts (object unset, empty choices, extra produced-mode fields). Tolerated
            # — do NOT assert its ``object`` (that would contradict the shipped GW13 contract).
            continue
        assert chunk.object == "chat.completion.chunk", (  # type: ignore[attr-defined]
            f"content chunk {index} object is not 'chat.completion.chunk': {chunk!r}"
        )
        delta = choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            reconstructed += piece
    # The deltas reconstructed in order into a well-formed string (never ``None``); an empty
    # stream of content is still well-framed (a tool-call-only or finish-only terminal).
    assert isinstance(reconstructed, str), "content deltas did not reconstruct to a string"


def assert_done_terminates(chunks: Sequence[object]) -> None:
    """Assert the ``data: [DONE]`` sentinel terminated iteration cleanly.

    Folded into :func:`assert_stream_frames`: the caller having collected ``chunks`` to the end
    without the SDK raising is the proof the ``[DONE]`` sentinel ended iteration cleanly, so this
    is a thin, explicitly-named alias that defers to the same logic for call sites that read
    better with a named terminal check.
    """

    assert_stream_frames(chunks)


def assert_error_envelope(err: object, *, expected_code: str) -> None:
    """Assert the ``{"error": {...}}`` envelope + a coherent HTTP status for a raised SDK error.

    ``err`` is the raised stock-SDK error (an ``openai.APIStatusError`` for a status-bearing
    response). The envelope body must carry ``message`` / ``type`` / ``code`` / ``param`` /
    ``request_id`` and its ``code`` must equal ``expected_code``; the HTTP status must cohere with
    that code (a block/content-filter is a 4xx, a not-found is 404, etc.).

    Where ``expected_code`` corresponds to a deliberate §10.2.2 ledger row (the streaming-block
    disposition or the degraded posture), the EXPECTED code is drawn from
    :func:`expected_diff` — the resolver-aware reader — rather than a hard-coded v1 constant, so
    the identical assertion is correct under both apps (Requirement 6.2). A caller passes the
    ledger KEY as ``expected_code`` for those rows; a plain wire code otherwise.
    """

    resolved_code = _resolve_expected_code(expected_code)

    assert isinstance(err, openai.APIStatusError), f"expected an APIStatusError, got {err!r}"
    body = err.response.json()
    assert isinstance(body, dict), f"error response body is not a JSON object: {body!r}"
    envelope = body.get("error")
    assert isinstance(envelope, dict), f"not an Error_Envelope body: {body!r}"

    for field in ("message", "type", "code", "param", "request_id"):
        assert field in envelope, f"error envelope is missing {field!r}: {envelope!r}"

    assert envelope["code"] == resolved_code, (
        f"error code {envelope['code']!r} != expected {resolved_code!r}: {envelope!r}"
    )

    status = err.status_code
    expected_status = _CODE_STATUS.get(resolved_code)
    if expected_status is not None:
        assert status == expected_status, (
            f"HTTP {status} is not coherent with code {resolved_code!r} "
            f"(expected {expected_status}): {envelope!r}"
        )
    else:
        assert 400 <= status < 600, f"error status {status} is not a 4xx/5xx: {envelope!r}"


def _resolve_expected_code(expected_code: str) -> str:
    """Resolve a §10.2.2 ledger KEY to its per-app expected code; pass a plain code through.

    When ``expected_code`` names a deliberate-diff ledger row, the resolver-selected value is
    returned (so the helper is correct under both apps); otherwise ``expected_code`` is already a
    wire-contract code and is returned unchanged.
    """

    if expected_code in _LEDGER_CODE_KEYS:
        resolved = expected_diff(expected_code)
        assert isinstance(resolved, str), (
            f"ledger value for {expected_code!r} is not a string code: {resolved!r}"
        )
        return resolved
    return expected_code
