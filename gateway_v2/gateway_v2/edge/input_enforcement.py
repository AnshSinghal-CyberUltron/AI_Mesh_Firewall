"""The ONE input-redaction path: scan, resolve, authorize, verify (Group A).

``edge/input_enforcement.py`` is the single place the input ``Decision`` is turned
into provider bytes. It closes leak A: the shipped ``ChatSurface._input_scan``
resolved the input findings but returned only a refuse code, refusing on BLOCK
alone; a REDACT decision was discarded and ``_proceed`` built the provider body
from the RAW ``req.text_deltas`` (C21: a value split across a message's content
parts also mis-aligned per-channel spans). This module keeps the resolved
``Decision`` and makes the provider body ONLY from the verified, transformed
bytes, so a REDACT is applied before the provider is opened, in both streaming
and non-streaming modes. Chat (:func:`scan_input`) and ``/v1/embeddings`` (task
25.5) both call it, so there is exactly one input redactor.

**Coordinate space.** A finding's span must live in the request CONTENT
coordinate space -- the SEPARATOR-FREE concatenation of
``GatewayRequest.text_deltas`` texts in order (``"".join(...)``) that
``dispatch/verify.py::_request_content`` / ``apply_and_verify`` index into.
:func:`scan_deltas` runs the detector per delta
and shifts every span by that delta's BASE OFFSET (the running sum of the prior
delta texts' lengths), mirroring that concatenation exactly, so the spans the
resolver keys and dispatch verifies agree byte-for-byte with the output path.

**One detector run per delta + one per join run (C21, Property 3).** A secret
split across two adjacent content parts of one message appears in NEITHER part's
own text, so beyond the per-delta runs :func:`scan_deltas` also scans each JOIN
RUN: a maximal run of consecutive deltas concatenated with the EMPTY separator
:data:`_JOIN_SEPARATOR` (byte-identical to the request-content coordinate space,
NOT ``prompt_chars``'s single-space token-cost join), shifting its spans by the
first delta's base offset. :func:`scan_input` derives those runs from
``CanonicalRequest.input_message_index`` (consecutive ``content_part_text`` deltas
of the same message, length >= 2). Exact-duplicate findings (same detector,
category, span and status -- a value found both per-delta and per-join-run) are
dropped before the ONE ``resolver.decide`` call, so a single match is not
double-counted.

**Fail closed (Property 4).** A detector OR resolver raise maps to
:data:`SCAN_ERROR_CODE` (``scan_failure`` -- the shipped spelling reused from
``edge/error_frame_scan.py``, never a new one): an uncomputable input scan
withholds rather than forwarding unscanned content.

**The authorize -> verify flow (``stage_upstream``).** :func:`stage_upstream`
mints the GW11 authorization from the input ``Decision`` via
``resolve.authorize`` (``None`` for a BLOCK decision -> :data:`REFUSE_INPUT_BLOCKED`,
defensive since BLOCK is refused earlier) and then shapes the verified upstream
bytes via ``dispatch/authorized.py::verified_upstream``. A
``VerificationFailure`` (a detected value survives, or the transformation cannot
be applied) maps to :data:`REFUSE_INPUT_UNMASKABLE`. The returned
:class:`~gateway_v2.dispatch.provider.UpstreamRequest` carries the ONLY bytes that
may reach a provider.

**Preservation (Req 3.2).** A decision with no transformations (ALLOW / FLAG /
MONITOR-only) runs through ``apply_and_verify`` unchanged, so the deltas reach the
provider byte-identical to the parsed request.

**Provenance gate untouched (Req 3.14).** The sealed ``DispatchAuthorization`` is
minted ONLY in ``resolve/`` and is only READ here (through ``verified_upstream``'s
witness-identity assertion); this module never mints, rebuilds or mutates it, so
``lint/check_dispatch_provenance`` stays green. ``edge`` is the top of the
import-linter layer order, so importing ``resolve`` / ``dispatch`` / ``domain``
below it is contract-legal.

**No capacity literal, no module-level mutable.** The refuse codes are plain
module-level strings (codes, not capacity-position literals), and there is no
module-level list / dict / set.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from gateway_v2.dispatch.authorized import verified_upstream
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.dispatch.transform import GatewayRequest, Transform
from gateway_v2.dispatch.verify import VerificationFailure
from gateway_v2.domain import Decision, Finding, Span
from gateway_v2.edge.error_frame_scan import SCAN_ERROR_CODE
from gateway_v2.edge.wire.openai import CanonicalRequest
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.resolve.authorize import authorize

__all__ = (
    "REFUSE_INPUT_BLOCKED",
    "REFUSE_INPUT_SCAN_FAILURE",
    "REFUSE_INPUT_UNMASKABLE",
    "InputScan",
    "scan_deltas",
    "scan_input",
    "stage_upstream",
)

#: An input finding resolved to a BLOCK disposition (C22 ``most_restrictive``).
#: The shipped ``output_blocked``-family input spelling (``edge/openai_chat.py``
#: ``REFUSE_INPUT_BLOCKED``); re-declared here as the one spelling
#: :func:`stage_upstream` returns when ``authorize`` mints no authorization. A
#: code, not a capacity-position literal.
REFUSE_INPUT_BLOCKED = "input_blocked"

#: An uncomputable input scan (the detector OR the resolver raised): the request
#: is withheld fail-closed. Reuses the shipped ``scan_failure`` spelling
#: (:data:`~gateway_v2.edge.error_frame_scan.SCAN_ERROR_CODE`) so an input-side and
#: an output-side scan failure render identically -- not a new code.
REFUSE_INPUT_SCAN_FAILURE = SCAN_ERROR_CODE

#: A REDACT input whose transformed outbound bytes cannot be verified against the
#: decision's finding spans (a detected value survives, or the transformation
#: cannot be applied): fail closed, zero provider calls. The new declared refusal
#: code, rendered by ``edge/errors`` as HTTP 400 ``invalid_request_error`` (the
#: same family as :data:`REFUSE_INPUT_BLOCKED`). Declared HERE because task 26.1
#: imports it from this module. A code, not a capacity-position literal.
REFUSE_INPUT_UNMASKABLE = "input_unmaskable"


@dataclass(frozen=True, slots=True)
class InputScan:
    """The result of the input scan + resolve (Group A, Property 3/Property 4).

    Carries exactly two things the lifecycle needs:

    * ``decision`` -- the resolved input :class:`~gateway_v2.domain.Decision`, in
      request-content span coordinates, so :func:`stage_upstream` can mint the
      authorization from it and ``apply_and_verify`` can mask a REDACT before the
      provider. It is ``None`` ONLY on a :data:`REFUSE_INPUT_SCAN_FAILURE` (the scan
      never produced a decision); on an ``input_blocked`` it carries the BLOCK
      decision, and on a proceed (ALLOW / FLAG / REDACT) it carries that decision.
    * ``refuse_code`` -- ``None`` means PROCEED (carry ``decision`` to the stage
      step); otherwise the one declared refusal spelling:
      :data:`REFUSE_INPUT_BLOCKED` on a BLOCK decision or
      :data:`REFUSE_INPUT_SCAN_FAILURE` on a detector/resolver raise.

    Frozen + slotted: an immutable value built once per input scan.
    """

    decision: Decision | None
    refuse_code: str | None


def scan_deltas(
    text_deltas: Sequence[tuple[str, str]],
    *,
    join_runs: Sequence[tuple[int, int]],
    detector: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
    on_invoke: Callable[[str], None] | None = None,
) -> InputScan:
    """Scan the input deltas, shift spans to request coordinates, resolve once.

    Runs ``detector`` ONCE per ``(channel, text)`` delta in ``text_deltas`` and
    shifts every finding's spans by that delta's BASE OFFSET -- the running sum of
    the prior delta texts' lengths, which is the delta's start in the concatenated
    request content coordinate space ``apply_and_verify`` indexes into. It also
    runs ``detector`` ONCE over the joined text of each ``(first_index, last_index)``
    delta run in ``join_runs`` (inclusive, the deltas concatenated with the EMPTY
    separator :data:`_JOIN_SEPARATOR`, byte-identical to the request-content
    coordinate space ``dispatch/verify.py::_request_content`` produces), shifting
    those spans by the FIRST delta's base offset, so a value split across adjacent
    content parts of one message -- contiguous only under separator-free
    concatenation -- is detected and its span aligns with ``_base_offsets``
    (C21, Property 3).

    ``on_invoke`` (when given) is called with the exact text handed to ``detector``
    on EVERY detector run (per-delta and per-join-run), so a caller can count
    detector invocations (embeddings uses it for ``exports.observe_*``).

    The per-delta and per-join-run findings are pooled, then EXACT DUPLICATES are
    dropped (a finding equal on detector, category, span and status, which arises
    when the same value is seen both in a single part and in the joined run), so a
    single match is resolved once. The pooled findings are resolved through ONE
    ``resolver.decide`` call into the returned ``InputScan.decision``.

    Fail closed (Property 4): a ``detector`` OR ``resolver`` raise is caught and
    mapped to :data:`REFUSE_INPUT_SCAN_FAILURE` (``decision=None``), so an
    uncomputable scan withholds rather than forwarding unscanned content.
    """
    try:
        findings = _collect_findings(
            text_deltas, join_runs, detector, on_invoke,
        )
        decision = resolver.decide(findings)
    except Exception:  # noqa: BLE001 — fail-closed: a raising scan/resolve withholds.
        return InputScan(decision=None, refuse_code=REFUSE_INPUT_SCAN_FAILURE)
    return InputScan(decision=decision, refuse_code=None)


def scan_input(
    req: CanonicalRequest,
    detector: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
) -> InputScan:
    """Scan a chat :class:`CanonicalRequest`'s input deltas (Group A, Property 3).

    The chat wrapper around :func:`scan_deltas`. It derives the join runs from
    ``req.input_message_index`` -- a maximal run of CONSECUTIVE deltas that are the
    SAME message's ``content_part_text`` parts (same ``messages[]`` index AND the
    ``content_part_text`` channel). Only runs of length >= 2 are join runs: a lone
    part is already covered by its own per-delta scan, so a single-part message is
    never scanned twice. Calls ``scan_deltas(req.text_deltas, join_runs=...)``.
    """
    join_runs = _message_part_join_runs(req.text_deltas, req.input_message_index)
    return scan_deltas(
        req.text_deltas,
        join_runs=join_runs,
        detector=detector,
        resolver=resolver,
    )


def stage_upstream(
    decision: Decision,
    request: GatewayRequest,
    transform: Transform,
) -> UpstreamRequest | str:
    """Authorize, verify and shape the upstream bytes for a proceed ``decision``.

    Returns the :class:`~gateway_v2.dispatch.provider.UpstreamRequest` carrying the
    ONLY bytes that may reach a provider (the sanitized / verified request body) on
    success, or a declared refuse CODE (a ``str``) when the request must be withheld:

    * ``authorize(decision)`` is ``None`` for a BLOCK decision (no authorization is
      minted) -> :data:`REFUSE_INPUT_BLOCKED`. This is defensive: a BLOCK is refused
      in the input scan phase before the stage step is reached.
    * ``verified_upstream`` raises :class:`~gateway_v2.dispatch.verify.VerificationFailure`
      when a REDACT's transformed bytes still carry a detected value, or the
      transformation cannot be applied -> :data:`REFUSE_INPUT_UNMASKABLE` (fail
      closed, zero provider calls).

    The ``str`` return is a declared refuse code, so the caller branches on
    ``isinstance(result, str)`` (an ``UpstreamRequest`` is never a ``str``). The
    sealed authorization is only READ inside ``verified_upstream`` (witness-identity
    assertion); this function mints nothing, so the provenance gate is untouched.
    """
    auth = authorize(decision)
    if auth is None:
        return REFUSE_INPUT_BLOCKED
    try:
        return verified_upstream(
            auth, decision=decision, request=request, transform=transform,
        )
    except VerificationFailure:
        return REFUSE_INPUT_UNMASKABLE


# --------------------------------------------------------------------------- #
# Internals: span shifting, join-run detection, exact-duplicate drop.
# --------------------------------------------------------------------------- #

#: The separator the content parts of one message are concatenated with when a
#: value is scanned across adjacent parts. It is the EMPTY string because the
#: join-run scan text must be byte-identical to the request-content COORDINATE
#: SPACE the span offsets live in: ``dispatch/verify.py::_request_content`` joins
#: the delta texts with ``"".join(...)`` (no separator) and :func:`_base_offsets`
#: is a separator-free running sum, so a value straddling a part boundary is
#: scanned as the exact contiguous bytes it is on the wire (``"...123-45"`` +
#: ``"-6789..."`` -> ``"...123-45-6789..."``). Any non-empty separator would make
#: the scan text diverge from that space, so the detected span would not align
#: with ``_base_offsets`` + ``apply_and_verify``'s masking and a straddling value
#: would leak (closing leak A end-to-end). NOT to be confused with
#: ``edge/wire/openai.py::prompt_chars``, whose single-space join is the v1
#: folded-input-text TOKEN-COST estimate, a different (non-coordinate) space. A
#: join character, not a capacity-position literal.
_JOIN_SEPARATOR = ""

#: The ``TextChannel.name`` of an array-of-parts content occurrence. Only
#: consecutive deltas on THIS channel sharing a ``messages[]`` index form a join
#: run (a split-across-parts value); the one spelling the fold emits.
_CONTENT_PART_TEXT_CHANNEL = "content_part_text"


def _collect_findings(
    text_deltas: Sequence[tuple[str, str]],
    join_runs: Sequence[tuple[int, int]],
    detector: Callable[[str], Sequence[Finding]],
    on_invoke: Callable[[str], None] | None,
) -> tuple[Finding, ...]:
    """Pool the per-delta and per-join-run findings in request coordinates.

    Runs ``detector`` once per delta (span-shifted by the delta's base offset) and
    once per ``(first, last)`` join run over the joined text (span-shifted by the
    first delta's base offset), calling ``on_invoke`` on each run when given, then
    drops exact duplicates (:func:`_drop_exact_duplicates`). The base offsets are a
    single running sum over the delta texts, so they match the concatenation
    ``apply_and_verify`` verifies against.
    """
    base_offsets = _base_offsets(text_deltas)
    pooled: list[Finding] = []
    for index, (_channel, text) in enumerate(text_deltas):
        if on_invoke is not None:
            on_invoke(text)
        pooled.extend(_shifted(detector(text), base_offsets[index]))
    for first, last in join_runs:
        joined = _JOIN_SEPARATOR.join(
            text for _channel, text in text_deltas[first : last + 1]
        )
        if on_invoke is not None:
            on_invoke(joined)
        pooled.extend(_shifted(detector(joined), base_offsets[first]))
    return _drop_exact_duplicates(pooled)


def _base_offsets(text_deltas: Sequence[tuple[str, str]]) -> tuple[int, ...]:
    """The start offset of each delta in the concatenated request content.

    Delta ``i``'s base offset is the summed length of deltas ``0..i-1`` -- the
    running sum ``dispatch/verify.py::_request_content`` produces by joining the
    delta texts in order. Returns one offset per delta, in order.
    """
    offsets: list[int] = []
    running = 0
    for _channel, text in text_deltas:
        offsets.append(running)
        running += len(text)
    return tuple(offsets)


def _shifted(findings: Sequence[Finding], base: int) -> tuple[Finding, ...]:
    """Shift every span of each finding by ``base`` (into request coordinates).

    A detector reports spans relative to the text it was handed; adding the
    delta's base offset lifts them into the concatenated request content space the
    resolver keys and dispatch verifies. The finding is otherwise unchanged
    (frozen value rebuilt via :func:`dataclasses.replace` with new ``spans``); a
    zero base returns equal spans, so a single-delta request is identity-shifted.
    """
    if base == 0:
        return tuple(findings)
    return tuple(
        replace(
            finding,
            spans=tuple(
                Span(start=span.start + base, end=span.end + base)
                for span in finding.spans
            ),
        )
        for finding in findings
    )


def _message_part_join_runs(
    text_deltas: Sequence[tuple[str, str]],
    input_message_index: Sequence[int],
) -> tuple[tuple[int, int], ...]:
    """Maximal runs of consecutive same-message ``content_part_text`` deltas.

    A join run is a maximal run of consecutive delta indices that share a
    ``messages[]`` index (``input_message_index``) AND are on the
    ``content_part_text`` channel, with length >= 2. Those are exactly the parts of
    one message whose split-across-parts value a per-part scan would miss (C21);
    a single-part run is omitted because its own per-delta scan already covers it.
    An empty or length-mismatched ``input_message_index`` (no index info) yields no
    runs -- every delta is then covered by its per-delta scan alone.
    """
    if len(input_message_index) != len(text_deltas):
        return ()
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(len(text_deltas)):
        if _is_content_part(text_deltas[index]) and _same_run(
            text_deltas, input_message_index, index, run_start,
        ):
            if run_start is None:
                run_start = index
            continue
        _close_run(runs, run_start, index - 1)
        run_start = index if _is_content_part(text_deltas[index]) else None
    _close_run(runs, run_start, len(text_deltas) - 1)
    return tuple(runs)


def _is_content_part(delta: tuple[str, str]) -> bool:
    """True iff ``delta`` is a ``content_part_text`` occurrence."""
    return delta[0] == _CONTENT_PART_TEXT_CHANNEL


def _same_run(
    text_deltas: Sequence[tuple[str, str]],
    input_message_index: Sequence[int],
    index: int,
    run_start: int | None,
) -> bool:
    """True iff delta ``index`` continues the open ``content_part_text`` run.

    A run continues while the previous delta is also a ``content_part_text`` part
    of the SAME message (same ``input_message_index`` value). A fresh run (no open
    start, or the previous delta is not a content part) always "continues" into a
    new one-element run; the length-2 filter is applied when the run is closed.
    """
    if run_start is None or index == 0:
        return True
    previous = index - 1
    return (
        _is_content_part(text_deltas[previous])
        and input_message_index[index] == input_message_index[previous]
    )


def _close_run(
    runs: list[tuple[int, int]],
    run_start: int | None,
    run_end: int,
) -> None:
    """Record the run ``[run_start, run_end]`` iff it spans >= 2 deltas."""
    if run_start is not None and run_end > run_start:
        runs.append((run_start, run_end))


def _drop_exact_duplicates(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """Drop exact-duplicate findings, preserving first-seen order.

    Two findings are exact duplicates when they agree on detector, category,
    status and the whole span tuple -- the case a value is reported both by a
    part's per-delta scan and by its message's join-run scan. Dropping them before
    the resolve step keeps a single match from being counted twice. Confidence and
    evidence are not part of the key: a span-identical same-category match from the
    same detector is the same finding regardless of a float confidence.
    """
    seen: set[tuple[str, str, str, tuple[tuple[int, int], ...]]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (
            finding.detector,
            str(finding.category),
            str(finding.status),
            tuple((span.start, span.end) for span in finding.spans),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return tuple(unique)
