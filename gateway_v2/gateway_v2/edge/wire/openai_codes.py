"""Declared refusal codes + :class:`MalformedRequest` for the OpenAI request parser (GW15).

Split out of :mod:`gateway_v2.edge.wire.openai` so the fold and the forwarding policy
(``openai_fold`` / ``openai_params``) can raise the same declared codes without a
circular import. :mod:`gateway_v2.edge.wire.openai` re-exports every name here.

Codes are plain strings (the shared value-code discipline, like ``domain/posture.py``),
NOT HTTP objects and NOT capacity-position literals: the handler maps each to an
OpenAI-shaped error through the single ``edge/errors`` ``Error_Envelope``.
"""

from __future__ import annotations

__all__ = (
    "INVALID_MAX_COMPLETION_TOKENS",
    "INVALID_MAX_TOKENS",
    "MALFORMED_BODY",
    "MALFORMED_MISSING_MODEL",
    "MALFORMED_NOT_AN_OBJECT",
    "MALFORMED_WRONG_SHAPE",
    "UNSUPPORTED_CONTENT_PART",
    "UNSUPPORTED_PARAMETER",
    "MalformedRequest",
)

#: The request body is not valid JSON (the bytes could not be decoded/parsed).
MALFORMED_BODY = "malformed_request_body"

#: The decoded JSON top level is not an object (OpenAI requests are JSON objects).
MALFORMED_NOT_AN_OBJECT = "malformed_request_not_an_object"

#: A required OpenAI-contract field is absent -- e.g. ``model`` (R1.1 routing field).
MALFORMED_MISSING_MODEL = "malformed_request_missing_model"

#: A present field holds the wrong type for the OpenAI-compatible shape (e.g.
#: ``model`` not a string, ``stream`` not a bool, ``messages`` not a list).
MALFORMED_WRONG_SHAPE = "malformed_request_wrong_shape"

#: ``max_tokens`` is a well-shaped non-negative int above the parse boundary
#: (``max_output_tokens_boundary()``). v1 parity: refused HTTP 400 with ``param``
#: ``max_tokens`` (``edge/errors`` -> ``(400, "invalid_request_error")``). A code.
INVALID_MAX_TOKENS = "invalid_max_tokens"

#: ``max_completion_tokens`` above the same boundary: the sibling HTTP 400 refusal
#: with ``param`` ``max_completion_tokens``. A code, not a capacity literal.
INVALID_MAX_COMPLETION_TOKENS = "invalid_max_completion_tokens"

#: A request field, message shape or role the gateway does not scan and therefore does not
#: forward (tools, ``response_format``, tool-role messages, ...). HTTP 400 with ``param``.
UNSUPPORTED_PARAMETER = "unsupported_parameter"

#: A content part whose type is not scanned text (image, audio, file). HTTP 400.
UNSUPPORTED_CONTENT_PART = "unsupported_content_part"


class MalformedRequest(Exception):
    """A request body is malformed or off the OpenAI-compatible contract (R1.4).

    Carries a *declared value-code* (one of the codes above) rather than a raw
    framework error, so the lifecycle handler renders it through the single
    ``edge/errors`` ``Error_Envelope`` and the SDK sees an OpenAI-shaped error --
    never a leaked ``json.JSONDecodeError`` / ``KeyError``. The originating ``code``
    is kept on the instance so the trace keeps the one spelling. An optional
    ``param`` names the offending request field (e.g. ``"max_tokens"`` for
    :data:`INVALID_MAX_TOKENS`), matching v1's boundary refusal; the render site
    threads it to the envelope when present.
    """

    __slots__ = ("code", "param")

    def __init__(self, code: str, *, param: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.param = param
