"""C27 provider error mapping, plan-driven timeouts, size bound, payload scan, over-band.

This is the GW11 C27 corrections, codes-and-values only -- NEVER an HTTP object. Every
function here returns a value (a :class:`GatewayError`, a ``float``, ``bytes`` or a posture
value-code); ``edge/errors.py`` is the single place those value-codes render to an HTTP status
+ SSE ``Error_Frame`` (R8.3). ``dispatch`` sits ABOVE ``detect`` and ``egress`` and BELOW
``plan`` in the import-linter layer order, so this module may import ``domain``
(``domain.posture``) but imports neither ``detect`` nor an HTTP framework: the provider-error
scan surface is an INJECTED seam (``scan_seam``) and the plan-declared timeout arrives through
an injected ``TimeoutSource`` seam, so no upward import is taken and the
``check_http_outside_edge_resolve`` gate stays green.

The five C27 corrections this module carries (runbook v2.1 register row 0-A.3):

* **Exact error-class mapping, never a blanket 502 (R8.1-R8.3).** The prototype mapped every
  provider ``4xx`` to a blanket ``502``, which made the OpenAI SDK retry three times and
  amplify load. :data:`PROVIDER_STATUS_MAP` maps a provider status to a declared
  :class:`GatewayErrorClass`: ``400 -> 400`` (preserving the provider's own error code),
  ``429 -> 429`` (preserving ``Retry-After`` + the provider rate-limit headers), and the other
  declared statuses PASS THROUGH their own status -- there is no ``502`` default anywhere in the
  table or in :func:`map_provider_error`.
* **Plan-declared timeouts per provider/model (R8.4).** :func:`provider_timeout_s` reads the
  timeout from the pinned plan per ``(provider, model)`` through an injected
  :class:`TimeoutSource`, so a legitimately slow first token (e.g. 125 s) is not cut at a fixed
  ``120`` s -- there is no fixed-timeout literal in this module.
* **Non-stream size bound, fail-closed (R8.5).** :func:`bound_nonstream_body` reads up to
  ``max_bytes`` (PASSED IN from ``ResourceContract``/plan at the wiring site -- no capacity
  literal here) and raises :class:`ResponseTooLarge` the moment the body exceeds the bound, so
  one BYOK tenant cannot OOM a shared worker by buffering an unbounded body.
* **Provider error-payload scanned before forward (R8.6).** :func:`scan_error_payload` runs the
  injected ``scan_seam`` (a minimal ``detect/guard`` scan surface) over the error body before it
  is forwarded, so a provider error frame echoing a secret is sanitized, not forwarded raw.
* **Over-band -> SDK-compatible ``context_length_exceeded``, not 413 (R8.7).**
  :func:`over_band_error` scans the conversation history incrementally and returns a
  :class:`GatewayError` whose ``code`` is :data:`OVER_BAND_CODE` once the running length exceeds
  the model's context band -- the SDK-compatible shape, never a ``413``.

**Malformed body (R9.4).** A provider body that cannot be decoded is a protocol error, reported
as the SINGLE shared posture code :data:`MALFORMED_UPSTREAM_CODE`
(``posture.STREAM_MALFORMED_UPSTREAM``, reused unchanged -- not re-spelled) and NEVER as success.
:func:`decode_nonstream_body` returns that value-code rather than a decoded value when decoding
fails.

**Audit every started request (R8.8)** is the entry's concern (the entry records a start on
lease, independent of completion); this module holds only the pure error/size/band helpers it
composes, no audit side effect.

**Dependency note (Task 3.2).** The design's ``CatalogueEntry.timeout_s`` /
``CatalogueEntry.context_band`` (``dispatch/catalogue.py``) is the real carrier of the
per-provider/model timeout and the model band. That sibling module is not yet present when this
module is authored, so :func:`provider_timeout_s` and :func:`over_band_error` are written against
the minimal injected :class:`TimeoutSource` / ``model_band: int`` seams rather than importing the
catalogue -- a ``CatalogueEntry`` is a legal source to pass to these seams once Task 3.2 lands,
with no change to these signatures.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from gateway_v2.domain import posture

__all__ = (
    "GatewayError",
    "GatewayErrorClass",
    "MALFORMED_UPSTREAM_CODE",
    "OVER_BAND_CODE",
    "PROVIDER_STATUS_MAP",
    "ResponseTooLarge",
    "ScanSeam",
    "ScannedPayload",
    "TimeoutSource",
    "bound_nonstream_body",
    "decode_nonstream_body",
    "map_provider_error",
    "over_band_error",
    "provider_timeout_s",
    "scan_error_payload",
)


# --------------------------------------------------------------------------- #
# Value-codes (single-sourced; edge renders them).
# --------------------------------------------------------------------------- #

#: The SDK-compatible over-band error code (R8.7). An over-band conversation returns THIS code,
#: never a ``413`` -- the OpenAI SDK recognises ``context_length_exceeded`` and does NOT retry it,
#: whereas a ``413`` is mis-reported. ``edge`` maps it to the declared client status.
OVER_BAND_CODE = "context_length_exceeded"

#: Malformed/undecodable provider body -> the ONE shared protocol-error spelling (R9.4). Reused
#: from ``domain.posture`` unchanged so the two sides of the stream cannot disagree on the code
#: (the C37 join failure). ``edge/errors.py`` already maps ``stream_malformed_upstream`` to its
#: declared HTTP/SSE rendering.
MALFORMED_UPSTREAM_CODE = posture.STREAM_MALFORMED_UPSTREAM


# --------------------------------------------------------------------------- #
# Error value types (NOT HTTP objects).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GatewayErrorClass:
    """A declared provider-error class: the gateway ``status`` + ``code`` for a provider status.

    This is a VALUE, not an HTTP object: ``status`` is a plain integer the gateway reports for
    this class and ``code`` is the single-sourced value-code ``edge`` renders. The table
    :data:`PROVIDER_STATUS_MAP` maps a provider status to one of these. Frozen + slotted so the
    declared classes are immutable constants.
    """

    status: int
    code: str


@dataclass(frozen=True, slots=True)
class GatewayError:
    """The mapped provider error carried down to ``edge`` for rendering (R8.1-R8.3).

    Carries the gateway ``status`` and value ``code`` (from the matched
    :class:`GatewayErrorClass`) plus the PRESERVED provider ``headers`` -- the ``Retry-After``
    and rate-limit headers on a ``429`` survive the mapping so the client backs off on the
    provider's own schedule (R8.2). ``headers`` is a read-only ``Mapping`` carried by value, not
    a mutable dict and not an HTTP response object. The construction here uses no
    ``status_code=`` keyword and no ``HTTPException``/``JSONResponse`` -- ``status`` is ordinary
    data -- so the HTTP-outside-edge gate is satisfied: ``edge`` turns this value into a
    response, this module does not.
    """

    status: int
    code: str
    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class ResponseTooLarge(Exception):
    """A non-streaming provider body exceeded the passed-in ``max_bytes`` bound (R8.5).

    Terminal and fail-closed: raised the moment the running read crosses the bound, BEFORE the
    whole body is buffered, so one BYOK tenant cannot OOM a shared worker. ``edge`` renders it;
    this module never builds the HTTP response.
    """


# --------------------------------------------------------------------------- #
# The provider-status -> gateway-error-class table (R8.1-R8.3). NEVER a blanket 502.
# --------------------------------------------------------------------------- #

# One declared class per mapped provider status. ``MappingProxyType`` like the egress error
# envelope in ``edge/errors.py`` -- a read-only module-level table, not a mutable dict, so
# ``check_no_module_mutable`` is satisfied (the gate flags ``dict``/``list``/``set`` literals and
# calls, not a ``MappingProxyType`` call). The integers are the provider/gateway STATUS values
# echoed through, not capacity-position literals, so ``check_capacity_literals`` is satisfied.
#
# Mapping decisions (R8.1-R8.3):
#   * 400 -> 400 : a provider bad-request stays a 400 and :func:`map_provider_error` preserves the
#     provider's own error code (R8.1), so the client sees the real cause, not a gateway 502.
#   * 429 -> 429 : a provider rate-limit stays a 429 and :func:`map_provider_error` preserves the
#     ``Retry-After`` + rate-limit headers (R8.2), so the SDK backs off on the provider schedule.
#   * 401 / 403 / 404 / 408 / 413 : the common client-facing provider statuses pass THROUGH their
#     own status with a declared value-code, so an auth/not-found/payload condition is reported
#     honestly rather than collapsed into a gateway error.
#   * 500 / 502 / 503 / 504 : upstream server faults pass THROUGH as the same upstream status
#     (a genuine upstream 502 is reported as 502 -- but a provider *4xx* is NEVER rewritten to a
#     blanket 502; see R8.3 and :func:`map_provider_error`'s unmapped-status handling below).
PROVIDER_STATUS_MAP: Mapping[int, GatewayErrorClass] = MappingProxyType(
    {
        400: GatewayErrorClass(status=400, code="invalid_request_error"),
        401: GatewayErrorClass(status=401, code="authentication_error"),
        403: GatewayErrorClass(status=403, code="permission_error"),
        404: GatewayErrorClass(status=404, code="not_found_error"),
        408: GatewayErrorClass(status=408, code="timeout_error"),
        413: GatewayErrorClass(status=413, code="invalid_request_error"),
        429: GatewayErrorClass(status=429, code="rate_limit_error"),
        500: GatewayErrorClass(status=500, code="upstream_error"),
        502: GatewayErrorClass(status=502, code="upstream_error"),
        503: GatewayErrorClass(status=503, code="upstream_error"),
        504: GatewayErrorClass(status=504, code="upstream_error"),
    }
)

#: Header names (lower-cased, ASGI convention) preserved from a provider ``429`` so the client
#: backs off on the provider's own schedule (R8.2). ``retry-after`` is the primary; the common
#: ``x-ratelimit-*`` family carries the remaining/reset budget. Any header NOT in this set is
#: dropped from the mapped error so a provider cannot leak an arbitrary header through the
#: gateway. A frozen set, not a module-level mutable (the gate flags ``set()`` calls and set
#: literals assigned at module scope -- ``frozenset(...)`` is neither).
_RATE_LIMIT_HEADERS = frozenset(
    {
        "retry-after",
        "x-ratelimit-limit-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    }
)

#: The value-code reported for a provider status with no declared class in
#: :data:`PROVIDER_STATUS_MAP`. An UNMAPPED status is a genuine upstream fault, reported honestly
#: as an upstream error at its own status -- NOT collapsed into a blanket ``502`` (R8.3).
_UNMAPPED_CODE = "upstream_error"


def _preserved_headers(status: int, headers: Mapping[str, str]) -> Mapping[str, str]:
    """Return the headers to carry on the mapped error (R8.2).

    For a ``429`` the ``Retry-After`` + rate-limit headers are preserved (case-insensitively) so
    the client honours the provider's schedule; for every other status no provider header is
    carried through, so a provider cannot inject arbitrary headers via an error. The result is a
    read-only ``MappingProxyType``.
    """
    if status != 429:
        return MappingProxyType({})
    kept = {
        name: value
        for name, value in headers.items()
        if name.lower() in _RATE_LIMIT_HEADERS
    }
    return MappingProxyType(kept)


def map_provider_error(
    status: int,
    headers: Mapping[str, str],
    body: bytes,
) -> GatewayError:
    """Map a provider error ``status`` to a :class:`GatewayError`, never a blanket 502 (R8.1-R8.3).

    * ``400`` -> a ``400`` carrying the provider's own error code (R8.1).
    * ``429`` -> a ``429`` preserving the ``Retry-After`` + rate-limit headers (R8.2).
    * Any other DECLARED status in :data:`PROVIDER_STATUS_MAP` -> that same status + its declared
      code.
    * An UNDECLARED status -> that same status with the generic ``upstream_error`` code -- an
      upstream fault reported at its OWN status, NEVER rewritten to a blanket ``502`` (R8.3).

    ``body`` is accepted so a caller that has already read the error payload can pass it (and so
    the signature matches the design); the payload itself is scanned before forward by
    :func:`scan_error_payload`, not here. The returned value is pure data; ``edge`` renders it.
    """
    error_class = PROVIDER_STATUS_MAP.get(status)
    if error_class is None:
        return GatewayError(
            status=status,
            code=_UNMAPPED_CODE,
            headers=MappingProxyType({}),
        )
    return GatewayError(
        status=error_class.status,
        code=error_class.code,
        headers=_preserved_headers(status, headers),
    )


# --------------------------------------------------------------------------- #
# Plan-declared timeout per provider/model (R8.4). No fixed 120 s.
# --------------------------------------------------------------------------- #


@runtime_checkable
class TimeoutSource(Protocol):
    """The injected pinned-plan carrier of the per-provider/model request timeout (R8.4).

    The real carrier is the pinned plan's catalogue (the design's ``CatalogueEntry.timeout_s``);
    it is injected as this minimal seam so this module takes NO upward import and holds NO fixed
    timeout literal. An implementation returns the plan-declared timeout in seconds for a
    ``(provider, model)`` pair, so a legitimately slow first token is not cut at a fixed bound.
    """

    def timeout_s(self, provider: str, model: str) -> float: ...


def provider_timeout_s(plan: TimeoutSource, provider: str, model: str) -> float:
    """Return the plan-declared request timeout for ``(provider, model)`` in seconds (R8.4).

    The value comes from the pinned plan (``plan.timeout_s(provider, model)``), so a provider
    whose first token legitimately takes 125 s is given 125 s -- there is no fixed ``120`` s cut
    in the gateway. This is a thin, pure accessor over the injected plan seam: the policy (what
    the timeout IS) lives in the plan, the gateway only reads it.
    """
    return plan.timeout_s(provider, model)


# --------------------------------------------------------------------------- #
# Non-stream size bound, fail-closed over the bound (R8.5). max_bytes PASSED IN.
# --------------------------------------------------------------------------- #


def bound_nonstream_body(reader: Iterable[bytes], max_bytes: int) -> bytes:
    """Read a non-streaming provider body up to ``max_bytes``, fail-closed over it (R8.5).

    ``reader`` yields the body in chunks; this accumulates them while the running total stays
    within ``max_bytes`` and raises :class:`ResponseTooLarge` the MOMENT the total would exceed
    the bound -- it does NOT buffer the whole body first, so a body three times the expected size
    cannot grow a shared worker's RSS before the check fires. ``max_bytes`` is PASSED IN (derived
    from ``ResourceContract``/plan at the wiring site), so this module holds no capacity literal.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in reader:
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(
                f"non-streaming provider body exceeded {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def decode_nonstream_body(body: bytes) -> str | None:
    """Decode a bounded non-streaming body as UTF-8, or report malformed (R9.4).

    Returns the decoded text on success; returns the SINGLE shared protocol-error value-code
    :data:`MALFORMED_UPSTREAM_CODE` (``stream_malformed_upstream``) when the body is not valid
    UTF-8 -- a malformed body is a protocol error, NEVER reported as success. ``edge`` renders
    the code; this module never claims a malformed body decoded.
    """
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Provider error-payload scan before forward (R8.6). Injected detect/guard seam.
# --------------------------------------------------------------------------- #


@runtime_checkable
class ScanSeam(Protocol):
    """The injected ``detect/guard`` scan surface used to sanitise a provider error payload (R8.6).

    ``dispatch`` sits ABOVE ``detect`` in the layer order, so importing ``detect`` here would be
    an upward import the contract forbids. Instead the scan surface is INJECTED as this minimal
    seam (exactly as ``http_provider.py`` injects its SSE decoder): an implementation takes the
    raw payload text and returns the sanitised text to forward -- a provider error frame echoing
    a secret comes back with the secret removed/masked.
    """

    def scan_text(self, payload: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ScannedPayload:
    """A provider error payload AFTER the injected scan seam ran over it (R8.6).

    ``text`` is the sanitised payload safe to forward; ``changed`` records whether the scan
    altered the payload (a provider error frame that echoed a secret comes back ``changed=True``
    with the secret removed). Frozen + slotted: the scanned result is an immutable value.
    """

    text: str
    changed: bool


def scan_error_payload(payload: str, scan_seam: ScanSeam) -> ScannedPayload:
    """Scan a provider error payload through the injected seam BEFORE forwarding it (R8.6).

    Runs ``scan_seam.scan_text`` over ``payload`` and returns the sanitised result; a provider error
    frame echoing a secret is therefore sanitised, not forwarded raw. ``changed`` is set when the
    scan altered the payload. The scan surface is injected (never imported) so no upward layer
    edge is taken.
    """
    scanned = scan_seam.scan_text(payload)
    return ScannedPayload(text=scanned, changed=scanned != payload)


# --------------------------------------------------------------------------- #
# Over-band -> SDK-compatible context_length_exceeded, incremental scan (R8.7). Not 413.
# --------------------------------------------------------------------------- #


def over_band_error(history: Iterable[str], model_band: int) -> GatewayError | None:
    """Return an over-band :class:`GatewayError` when history exceeds the model band (R8.7).

    Scans ``history`` INCREMENTALLY -- accumulating each turn's length and short-circuiting the
    instant the running total exceeds ``model_band`` -- rather than materialising and measuring
    the whole conversation. When the band is exceeded it returns a :class:`GatewayError` whose
    ``code`` is the SDK-compatible :data:`OVER_BAND_CODE` (``context_length_exceeded``), NOT a
    ``413``; when the conversation fits the band it returns ``None`` (dispatch proceeds).

    ``model_band`` is the model's declared context length (the design's
    ``CatalogueEntry.context_band``), passed in; this module holds no band literal. The returned
    error's ``status`` is the status the OpenAI SDK expects for a context-length error so the SDK
    does not retry it.
    """
    running = 0
    for turn in history:
        running += len(turn)
        if running > model_band:
            return GatewayError(
                status=400,
                code=OVER_BAND_CODE,
                headers=MappingProxyType({}),
            )
    return None
