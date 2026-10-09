"""Mid-stream provider error-frame scanning (GW12, task 12.1 — R11).

A provider can deliver a mid-stream *error frame* (``UpstreamEvent.error_frame``,
``dispatch/provider.py``): a free-text error body the upstream emits instead of
(or in the middle of) content. That body is UNTRUSTED and can carry a secret — a
connection string, an API key echoed into a stack trace, a bearer token in a
``WWW-Authenticate`` hint. So before ANY byte of an error frame is forwarded
downstream it must pass the output guard exactly as released content does (R11.1):
scanned, resolved to one output ``Decision``, and either forwarded with the
decided redactions applied (R11.2), withheld on a block (R11.3), or withheld
fail-closed when the scan itself raises (R11.4).

This module is the ONE home for that scan step. It lives in ``edge`` (the top
layer), which is the correct injection site: ``egress`` must never import
``detect`` (R14.1), so the findings-producing scanner is INJECTED here — the same
discipline the streaming content path already follows (``edge/routes.py`` injects
the scanner + resolver into the shipped ``StreamPipeline``). The scan is reused
glue, not new detection: the injected :data:`ErrorFrameScanner` produces completed
:class:`~gateway_v2.domain.Finding`\\ s over the WHOLE error-frame text, the
injected :class:`~gateway_v2.egress.output_guard.OutputResolver` maps them to one
:class:`~gateway_v2.domain.Decision`, and the shipped
:func:`~gateway_v2.egress.output_guard.apply_decision` applies that decision's
redactions (raising :class:`~gateway_v2.egress.output_guard.OutputBlocked` on a
BLOCK). The error-frame text is a complete, bounded string (not a growing stream),
so there is no holdback — a single whole-text scan is the whole of the work.

**Byte-linearity (R11.5).** The frame is scanned exactly ONCE
(``scanner(frame)``) and the decision applied in a single pass over the frame's
bytes (``apply_decision`` composes the in-range redaction spans in one left-to-
right walk). There is no per-byte rescan and no repeated full-text pass, so the
cost is linear in the frame's byte length: ``cost(2n) ≈ 2·cost(n)`` (the property
task 12.2 asserts).

**Fail closed (R11.4 / R15.1 / R15.2).** EVERY failure withholds the frame and
reports a terminal posture code; raw error-frame bytes are NEVER forwarded when a
scan or decision cannot be completed. A scan that raises is caught and mapped to
:data:`SCAN_ERROR_CODE` (``scan_failure`` — the shipped terminal code the error
envelope already renders as "the output guard could not complete"); a BLOCK
decision maps to :data:`BLOCK_CODE` (``output_blocked``). Both are posture codes
the single :mod:`gateway_v2.edge.errors` envelope and the SSE codec already map to
a declared ``Error_Frame``, so a withheld error frame renders through the ONE
terminal site the handler uses for every other cut (``ChatRoute._finish_stream``),
never a raw framework error.

**Posture code for the withheld frame.** The task offers ``scan_failure`` or
``STREAM_MALFORMED_UPSTREAM`` for the fail-closed-on-scan-error case. We use
``scan_failure``: a scan that raised is an output-guard failure, not an
*undecodable upstream SSE* condition (which is what ``STREAM_MALFORMED_UPSTREAM``
spells — the ``SSEDecoder.malformed()`` signal, mapped to HTTP 502 Bad Gateway).
``scan_failure`` is the single spelling the shipped ``egress/stream.py`` pipeline
already raises when its own scanner fails, maps to HTTP 403 ("the guard withheld
the response"), and carries a declared SSE shape ("the output guard could not
complete and the stream was terminated") — the honest cause for a frame withheld
because its scan could not finish, and the same spelling so the two sides of the
stream cannot disagree (the C37 join failure).

**Layering.** ``edge`` may import ``egress`` (``OutputResolver`` / ``apply_decision``
/ ``OutputBlocked``) and ``domain`` (``Decision`` / ``Finding`` / ``posture``)
below it. The findings scanner is a plain injected callable (the same
``Callable[[str], Sequence[Finding]]`` shape as ``egress.stream.Detector``), so
this module adds no ``edge → detect`` import edge and ``egress`` still never
imports ``detect``. No HTTP object is constructed here — a withheld frame returns
a value-code the handler renders (the codes-vs-render boundary is untouched). No
module-level mutable, no capacity literal.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gateway_v2.domain import Finding
from gateway_v2.egress.output_guard import OutputBlocked, OutputResolver, apply_decision

__all__ = (
    "BLOCK_CODE",
    "SCAN_ERROR_CODE",
    "ErrorFrameOutcome",
    "ErrorFrameScanner",
    "scan_error_frame",
)

#: The injected findings-producing scanner for an error frame. Same shape as the
#: shipped ``egress.stream.Detector`` (``Callable[[str], Sequence[Finding]]``):
#: given the WHOLE error-frame text it returns the completed findings with spans
#: ABSOLUTE over that text (the frame is scanned as one complete string, base
#: offset 0). Injected from ``edge`` so ``egress`` never imports ``detect``
#: (R14.1); the real GW07/GW08 detector satisfies this signature unchanged.
ErrorFrameScanner = Callable[[str], Sequence[Finding]]

#: The terminal posture code for an error frame WITHHELD because its scan raised
#: (R11.4, fail closed). ``scan_failure`` is the shipped spelling the egress
#: pipeline already uses for its own scanner failure; the error envelope + SSE
#: codec render it as a declared ``Error_Frame`` (HTTP 403, "the output guard
#: could not complete"). Reused, not a new code.
SCAN_ERROR_CODE: str = "scan_failure"

#: The terminal posture code for an error frame WITHHELD because the output
#: decision BLOCKs it (R11.3). ``output_blocked`` is the shipped spelling the
#: egress pipeline raises on a BLOCK disposition; the envelope renders it as a
#: declared ``Error_Frame`` (HTTP 403, "the response was withheld by the output
#: guard"). Reused, not a new code.
BLOCK_CODE: str = "output_blocked"

# Both codes are spellings the one shared vocabulary already carries: the shipped
# `egress/stream.py` pipeline raises `scan_failure` / `output_blocked` on its own
# scanner-failure / BLOCK paths, and both `edge/errors.py::_ENVELOPE` and
# `edge/wire/sse.py::ERROR_SHAPES` map them to a declared `Error_Frame`. They are
# REUSED here (not redefined) so a withheld error frame renders at the same
# terminal site as every other cut.


@dataclass(frozen=True, slots=True)
class ErrorFrameOutcome:
    """What the error-frame scan decided — forward decided bytes, or withhold (R11).

    Exactly one of the two states holds, enforced by construction:

    * **Forward (R11.2).** ``withheld_code is None`` and ``forward_text`` is the
      error-frame text with the decided redactions applied — the bytes to forward
      downstream. An ALLOW decision forwards the frame unchanged; a REDACT decision
      forwards it with the matched spans masked. These are the ONLY bytes of the
      frame that ever leave the gateway.
    * **Withhold (R11.3 / R11.4).** ``withheld_code`` is a terminal posture code
      (:data:`BLOCK_CODE` on a block, :data:`SCAN_ERROR_CODE` on a scan error) and
      ``forward_text is None`` — NO byte of the frame is forwarded; the handler
      renders ``withheld_code`` as the declared terminal ``Error_Frame``.

    Frozen + slotted: the outcome is an immutable value the caller reads once.
    """

    forward_text: str | None
    withheld_code: str | None

    @property
    def withheld(self) -> bool:
        """Whether the frame was withheld (no byte forwarded) — a terminal cut."""
        return self.withheld_code is not None


def scan_error_frame(
    frame: str,
    *,
    scanner: ErrorFrameScanner,
    resolver: OutputResolver,
) -> ErrorFrameOutcome:
    """Scan a mid-stream error frame and decide forward-redacted vs. withhold (R11).

    The frame is scanned ONCE through the injected ``scanner`` to produce completed
    findings over the whole text (base offset 0), the injected ``resolver`` maps
    them to one ``Decision``, and the shipped ``apply_decision`` applies that
    decision's redactions in a single pass (R11.1/R11.2). The result:

    * ALLOW / REDACT → :class:`ErrorFrameOutcome` carrying ``forward_text`` (the
      frame with the decided redactions applied — the only bytes forwarded, R11.2).
    * BLOCK → ``apply_decision`` raises :class:`OutputBlocked`; the frame is
      withheld with :data:`BLOCK_CODE` (R11.3).
    * The scan (or decision/apply) raises anything else → the frame is withheld
      fail-closed with :data:`SCAN_ERROR_CODE` (R11.4). No raw byte of the frame is
      forwarded on any failure (R15.1/R15.2).

    Byte-linear (R11.5): one ``scanner`` call + one ``apply_decision`` pass, no
    per-byte rescan.
    """
    try:
        findings = scanner(frame)
    except Exception:
        # Scan itself failed: withhold fail-closed, forward nothing (R11.4). The
        # frame's raw bytes never leave the gateway on a scan error.
        return ErrorFrameOutcome(forward_text=None, withheld_code=SCAN_ERROR_CODE)

    try:
        decision = resolver.decide(tuple(findings))
        # base_offset 0: the frame is scanned as one complete string, so a
        # finding's absolute span indexes straight into the frame (R11.2).
        forward_text = apply_decision(frame, decision, 0)
    except OutputBlocked:
        # The decision blocks the frame: withhold, declared terminal frame (R11.3).
        return ErrorFrameOutcome(forward_text=None, withheld_code=BLOCK_CODE)
    except Exception:
        # Resolve/apply failed (e.g. a fail-closed uncovered-span in apply_decision):
        # withhold fail-closed rather than forward partially-decided bytes (R11.4,
        # R15.2 — never forward raw/under-redacted bytes when a decision cannot be
        # completed).
        return ErrorFrameOutcome(forward_text=None, withheld_code=SCAN_ERROR_CODE)

    return ErrorFrameOutcome(forward_text=forward_text, withheld_code=None)
