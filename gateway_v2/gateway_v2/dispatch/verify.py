"""Apply-and-verify the request bytes before any provider byte is sent (GW11 C4).

This module is the request-side mirror of
``egress/output_guard.py::apply_decision`` (R3.5): it applies a ``Decision``'s
redaction ``Transformation``s to a request's text content and then *verifies*,
byte-for-byte, that every ``finding.spans`` entry under a redacting disposition
was in fact covered by a transformation and carries no surviving raw bytes. It
is fail-closed: any uncovered or partial span, or any span whose raw bytes
survive in the output, raises :class:`VerificationFailure` and the caller sends
nothing (R3.3).

**Byte/offset model.** A :class:`~gateway_v2.dispatch.transform.GatewayRequest`
carries its payload as ``text_deltas`` — an ordered tuple of
``(channel, text)`` pairs. "Request bytes" is the concatenation of those delta
texts in order; a ``Span(start, end)`` and a ``Transformation`` index into that
concatenated content with Python string (code-point) offsets, exactly as
``apply_decision`` indexes into its released slice with ``str`` offsets and
``len()``. The whole request content is present (there is no streaming slice on
the request side), so the base offset is ``0`` and a span is covered iff
``0 <= start <= end <= len(content)``. After redaction the sanitized content is
re-split back onto the original channels at the original per-delta boundaries so
the request → provider path keeps the same ``(channel, text)`` shape.

**No new redactor (R12.1 / R3.5).** Redaction is performed solely by writing a
``Transformation``'s own ``replacement`` over its span — the same mechanism
``apply_decision`` uses. This module invents no redaction algorithm (that is
GW15); it only applies the shipped transformations and verifies the result.

**Layering.** ``dispatch`` sits above ``egress`` in the import-linter layer
order, so importing ``apply_decision``/``OutputBlocked`` from
``gateway_v2.egress.output_guard`` is contract-legal and lets the request side
reuse the exact byte-boundary discipline rather than re-deriving it. The
``Decision``/``Disposition``/``Transformation``/``Finding``/``Span`` values come
from ``gateway_v2.domain`` (the lowest layer); ``GatewayRequest`` from the
dispatch sibling ``dispatch.transform``. No ``detect``/``plan``/``admit``/
``edge`` import (those sit above ``dispatch``).
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.dispatch.transform import GatewayRequest
from gateway_v2.domain import (
    Decision,
    Disposition,
    FindingDisposition,
    Span,
)
from gateway_v2.egress.output_guard import apply_decision

__all__ = (
    "VerificationFailure",
    "VerifiedBytes",
    "apply_and_verify",
)


@dataclass(frozen=True, slots=True)
class VerifiedBytes:
    """The only bytes the entry may send (R3.1, R3.4).

    ``request`` is the sanitized :class:`GatewayRequest` — its ``text_deltas``
    carry the content after every redaction ``Transformation`` has been applied
    and verified against the finding spans. ``log`` is the LGW11-3 verification
    log entry describing what was redacted (or that nothing was).
    """

    request: GatewayRequest
    log: str


class VerificationFailure(Exception):
    """Terminal verification failure (R3.3): no bytes are sent to the provider.

    Raised when a redacting disposition's finding span is not fully covered by
    an applied transformation, or when the span's raw content survives in the
    sanitized output.
    """


#: Dispositions that oblige a finding's spans to be redacted out of the request
#: content. REDACT (incl. REWRITE→REDACT in the resolver) is the only request-
#: side redacting disposition; BLOCK never reaches here (no authorization is
#: minted for a BLOCK decision), and ALLOW/FLAG observe without redacting.
_REDACTING_DISPOSITIONS: frozenset[Disposition] = frozenset({Disposition.REDACT})


def _request_content(request: GatewayRequest) -> str:
    """Concatenate the request's delta texts into the verifiable content string.

    The concatenation order is the ``text_deltas`` order; this is the single
    coordinate space every ``Span``/``Transformation`` offset indexes into.
    """
    return "".join(text for _channel, text in request.text_deltas)


def _resplit(content: str, request: GatewayRequest) -> tuple[tuple[str, str], ...]:
    """Re-split sanitized ``content`` back onto the original delta channels.

    Redaction can change the content length, so the sanitized bytes are
    distributed by walking the original per-delta boundaries: all but the last
    delta keep their original character length and the final delta absorbs the
    remainder. This keeps the ``(channel, text)`` shape and channel order the
    provider transport expects while placing all redacted content back under the
    channels it came from. A request with no deltas yields no deltas.
    """
    deltas = request.text_deltas
    if not deltas:
        return ()
    out: list[tuple[str, str]] = []
    cursor = 0
    last = len(deltas) - 1
    for index, (channel, original) in enumerate(deltas):
        if index == last:
            out.append((channel, content[cursor:]))
        else:
            end = cursor + len(original)
            out.append((channel, content[cursor:end]))
            cursor = end
    return tuple(out)


def _redacting_detectors(decision: Decision) -> frozenset[str]:
    """Detectors whose per-finding disposition obliges redaction (R3.2)."""
    return frozenset(
        item.detector
        for item in decision.per_finding
        if item.disposition in _REDACTING_DISPOSITIONS
    )


def _spans_to_verify(decision: Decision) -> tuple[Span, ...]:
    """Every finding span that a redacting disposition requires be redacted.

    A finding is obliged to be redacted when its detector carries a redacting
    per-finding disposition. The overall ``decision.disposition`` is the
    most-restrictive of the per-finding dispositions, so a REDACT request can
    still carry ALLOW/FLAG findings whose spans are *not* obliged — those are
    skipped here (only the redacting findings' spans must be absent).
    """
    redacting = _redacting_detectors(decision)
    spans: list[Span] = []
    for finding in decision.findings:
        if finding.detector in redacting:
            spans.extend(finding.spans)
    return tuple(spans)


def _assert_covered(span: Span, length: int) -> None:
    """Fail closed unless ``span`` lies wholly inside ``[0, length]`` (R3.3).

    Mirrors ``apply_decision``'s ``_covers`` discipline with ``base_offset=0``:
    the whole request content is present, so an obliged span that is out of
    range or inverted is a partial/uncovered span and must raise rather than let
    a partially-redacted match reach the provider.
    """
    if not (0 <= span.start <= span.end <= length):
        raise VerificationFailure(
            "fail-closed: redacting finding span is not covered by the request content "
            f"(span={span.start}:{span.end}, content_len={length})",
        )


def _verify_no_raw_survives(
    original: str,
    sanitized: str,
    spans: tuple[Span, ...],
) -> None:
    """Verify no obliged span's raw bytes survive in ``sanitized`` (R3.2).

    For each obliged span the original raw content ``original[start:end]`` must
    not appear verbatim anywhere in the sanitized output. A non-empty raw span
    that is still a substring of ``sanitized`` means the transformation did not
    actually remove it (uncovered, partial, or a no-op replacement that echoes
    the raw bytes) — a fail-closed condition.
    """
    for span in spans:
        raw = original[span.start : span.end]
        if raw and raw in sanitized:
            raise VerificationFailure(
                "fail-closed: raw bytes of a redacting finding span survive the "
                f"transformation (span={span.start}:{span.end})",
            )


def _redacting_per_finding(decision: Decision) -> tuple[FindingDisposition, ...]:
    return tuple(
        item
        for item in decision.per_finding
        if item.disposition in _REDACTING_DISPOSITIONS
    )


def _assert_fully_covered_by_transformations(
    spans: tuple[Span, ...],
    decision: Decision,
) -> None:
    """Fail closed unless every obliged ``span`` is wholly covered by transforms.

    Mirrors ``apply_decision``'s "a redaction whose span is not fully covered …
    raises rather than letting a partially-redacted match through": every byte
    of an obliged finding span must fall inside the union of the decision's
    transformation spans. A span that is only partially covered (one transform
    touching part of it, the remainder left raw) is a partial redaction and must
    raise — the surviving-raw check alone would miss it when the untouched
    remainder is not a verbatim copy of the whole raw span.
    """
    intervals = sorted(
        (t.span.start, t.span.end) for t in decision.transformations
    )
    for span in spans:
        cursor = span.start
        for start, end in intervals:
            if start > cursor:
                break
            if end > cursor:
                cursor = end
            if cursor >= span.end:
                break
        if cursor < span.end:
            raise VerificationFailure(
                "fail-closed: redacting finding span is not fully covered by a "
                f"transformation (span={span.start}:{span.end}, "
                f"covered_up_to={cursor})",
            )


def apply_and_verify(decision: Decision, request: GatewayRequest) -> VerifiedBytes:
    """Apply ``decision``'s transformations to ``request`` and verify the result.

    Applies every ``Transformation`` in ``decision.transformations`` to the
    concatenated request content (via the shipped ``apply_decision`` with
    ``base_offset=0``, so the whole request is the covered slice), then verifies
    that every ``finding.spans`` entry obliged by a redacting per-finding
    disposition was covered and no longer carries its raw bytes. Returns the
    sanitized :class:`VerifiedBytes` with the LGW11-3 verification log.

    Fail-closed (R3.3): raises :class:`VerificationFailure` on any uncovered or
    partial obliged span, or any obliged span whose raw bytes survive the
    transformation. A BLOCK decision never reaches here (no authorization is
    minted for it); an ALLOW/FLAG decision with no redacting findings returns
    the request content unchanged with a log noting no redaction occurred.
    """
    content = _request_content(request)
    length = len(content)

    obliged_spans = _spans_to_verify(decision)
    # Fail closed first on any obliged span that cannot be covered by the content
    # at all — a partial/out-of-range span must not reach the apply step.
    for span in obliged_spans:
        _assert_covered(span, length)

    # Fail closed on any obliged span not WHOLLY covered by a transformation — a
    # partial transformation (which would leave a raw remainder the surviving-raw
    # check alone can miss) is a fail-closed condition, mirroring apply_decision.
    _assert_fully_covered_by_transformations(obliged_spans, decision)

    # Apply the shipped transformations with the whole content as the slice. This
    # reuses apply_decision's byte-boundary discipline: an in-range transformation
    # span not fully covered raises ValueError (fail-closed), overlaps raise, and
    # out-of-slice spans are skipped (none here, since base_offset=0 and the whole
    # content is present).
    try:
        sanitized = apply_decision(content, decision, base_offset=0)
    except ValueError as exc:
        raise VerificationFailure(
            f"fail-closed: transformation could not be applied cleanly ({exc})",
        ) from exc

    # Byte-level verification: no obliged span's raw content survives.
    _verify_no_raw_survives(content, sanitized, obliged_spans)

    sanitized_request = GatewayRequest(
        destination=request.destination,
        model=request.model,
        text_deltas=_resplit(sanitized, request),
    )

    redacting = _redacting_per_finding(decision)
    if redacting:
        detectors = ", ".join(sorted(item.detector for item in redacting))
        log = (
            f"verified REDACT dispatch: {len(obliged_spans)} span(s) across "
            f"detector(s) [{detectors}] redacted and verified absent "
            f"(plan_version={decision.plan_version})"
        )
    else:
        log = (
            "verified dispatch: no redacting disposition, request content "
            f"unchanged (disposition={decision.disposition}, "
            f"plan_version={decision.plan_version})"
        )

    return VerifiedBytes(request=sanitized_request, log=log)
