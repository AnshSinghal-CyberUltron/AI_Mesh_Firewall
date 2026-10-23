"""v2-enforcement-leaks Group A (dispatch): latent ``apply_and_verify`` defects.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 4; design Property 1 (A),
Property 3 and Property 4). These defects sit behind the unwired input REDACT path: once the
edge routes an input REDACT decision through ``dispatch/verify.apply_and_verify`` they become
reachable on every chat and embeddings request.

* Case 1 (``_resplit`` length change). Redaction changes the content length, but ``_resplit``
  keeps every non-final delta at its ORIGINAL length, so the sanitized bytes of delta 1 shift
  into delta 0. Expected (Property 3): each delta holds exactly its own sanitized bytes.
* Case 2 (mixed plan). ``_spans_to_verify`` obliges every finding of a detector that has ANY
  redacting per-finding disposition. ``deterministic.secret`` emits both SECRET and CREDENTIAL
  findings, so under ``SECRET -> flag`` plus ``CREDENTIAL -> redact`` the FLAG-ed SECRET span
  is obliged too, has no transformation, and the request fails closed with no leak. Expected:
  obligations are keyed per finding; verification succeeds and masks only the CREDENTIAL span.
* Case 3 (no-op transformation). A REDACT decision whose transformation echoes the raw bytes
  must raise ``VerificationFailure`` (Property 4, the dispatch-level precursor of
  ``input_unmaskable`` with zero provider calls). Holds on the unfixed tree: baseline.

Decisions come from the real pure ``resolve`` over hand-built findings, so per-finding
dispositions and transformations are exactly what the resolver emits for the plan.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Sequence

import pytest

from gateway_v2.dispatch.authorized import AuthorizedProviderEntry, verified_upstream
from gateway_v2.dispatch.catalogue import ProviderCatalogue
from gateway_v2.dispatch.pool import ProviderPool
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY, GatewayRequest, Transform
from gateway_v2.dispatch.verify import VerificationFailure, _resplit, apply_and_verify
from gateway_v2.domain import (
    Action,
    Category,
    Decision,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    FindingDisposition,
    FindingStatus,
    Mode,
    Phase,
    Rule,
    RuleScope,
    Span,
    StreamingMode,
    Surface,
    Transformation,
)
from gateway_v2.domain.dispatch_authorization import DispatchAuthorization
from gateway_v2.egress.output_guard import apply_decision
from gateway_v2.resolve import authorize, resolve

_CHANNEL = "message_content"
_PLACEHOLDER = "[REDACTED]"
_PII_DETECTOR = "deterministic.pii"
_SECRET_DETECTOR = "deterministic.secret"


def _rule(rule_id: str, category: Category, action: Action) -> Rule:
    return Rule(
        rule_id=rule_id,
        category=category,
        mode=Mode.ENFORCE,
        action=action,
        threshold=None,
        priority=100,
        scope=RuleScope.BOTH,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT, Surface.EMBEDDINGS}),
    )


def _plan(*rules: Rule) -> ExecutionPlan:
    return ExecutionPlan(
        org_id="org-v2-leaks",
        epoch=1,
        sequence=1,
        content_hash="v2leaks" + "0" * 57,
        compiled_at=0.0,
        rules=rules,
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _finding(detector: str, category: Category, start: int, end: int) -> Finding:
    return Finding(
        detector=detector,
        detector_version="1",
        category=category,
        status=FindingStatus.EXECUTED,
        confidence=0.99,
        spans=(Span(start=start, end=end),),
        evidence=None,
    )


def _request(*texts: str) -> GatewayRequest:
    return GatewayRequest(
        destination="amf://v2-leaks",
        model="gpt-x",
        text_deltas=tuple((_CHANNEL, text) for text in texts),
    )


def _span_of(content: str, value: str) -> tuple[int, int]:
    start = content.index(value)
    return start, start + len(value)


# --- Case 1: _resplit keeps the original per-delta lengths ---------------------------------


# Fixed by task 16.2. Unfixed counterexample: ('SSN 123-45-6789', 'next message') ->
# ('SSN [REDACTED]n', 'ext message'), byte 'n' of delta 1 moved into delta 0.
def test_resplit_keeps_each_delta_its_own_bytes_after_a_length_change() -> None:
    ssn = "123-45-6789"
    request = _request("SSN " + ssn, "next message")
    start, end = _span_of("SSN " + ssn, ssn)
    decision = resolve(
        (_finding(_PII_DETECTOR, Category.PII, start, end),),
        _plan(_rule("pii-redact", Category.PII, Action.REDACT)),
        Phase.INPUT,
    )
    assert decision.disposition is Disposition.REDACT

    verified = apply_and_verify(decision, request)

    assert verified.request.text_deltas == (
        (_CHANNEL, "SSN " + _PLACEHOLDER),
        (_CHANNEL, "next message"),
    )


# --- Case 2: obligations keyed by detector id, not per finding -----------------------------
# Fixed by tasks 16.1 (finding-keyed verify) + 17.1 (resolve sets finding_index). Unfixed
# counterexample: SECRET->flag + CREDENTIAL->redact, both from deterministic.secret, with no
# finding_index; the detector-keyed rule obliged the FLAG-ed SECRET span 4:24 too, which has
# no transformation -> false VerificationFailure 'not fully covered (span=4:24,
# covered_up_to=4)'.


def test_mixed_plan_obliges_only_the_redacting_finding() -> None:
    secret = "sk-test-abcdef123456"
    credential = "-----BEGIN RSA PRIVATE KEY-----"
    content = f"key {secret} pem {credential} end"
    secret_start, secret_end = _span_of(content, secret)
    cred_start, cred_end = _span_of(content, credential)
    decision = resolve(
        (
            _finding(_SECRET_DETECTOR, Category.SECRET, secret_start, secret_end),
            _finding(_SECRET_DETECTOR, Category.CREDENTIAL, cred_start, cred_end),
        ),
        _plan(
            _rule("secret-flag", Category.SECRET, Action.FLAG),
            _rule("credential-redact", Category.CREDENTIAL, Action.REDACT),
        ),
        Phase.INPUT,
    )
    # The repro is faithful to the resolver: one FLAG and one REDACT disposition on the same
    # detector id, each keyed to its own finding, and a transformation only over the
    # CREDENTIAL span.
    assert decision.disposition is Disposition.REDACT
    assert decision.per_finding == (
        FindingDisposition(
            detector=_SECRET_DETECTOR, disposition=Disposition.FLAG, finding_index=0,
        ),
        FindingDisposition(
            detector=_SECRET_DETECTOR, disposition=Disposition.REDACT, finding_index=1,
        ),
    )
    assert tuple(t.span for t in decision.transformations) == (Span(cred_start, cred_end),)

    verified = apply_and_verify(decision, _request(content))

    assert verified.request.text_deltas == ((_CHANNEL, f"key {secret} pem {_PLACEHOLDER} end"),)


# --- Case 3: a no-op transformation on a REDACT decision fails closed (baseline) -----------


def _noop_redact_decision() -> tuple[Decision, str]:
    """A REDACT decision whose one transformation echoes the raw SSN bytes."""
    ssn = "123-45-6789"
    content = f"My SSN is {ssn} thanks"
    start, end = _span_of(content, ssn)
    span = Span(start=start, end=end)
    decision = Decision(
        disposition=Disposition.REDACT,
        per_finding=(FindingDisposition(detector=_PII_DETECTOR, disposition=Disposition.REDACT),),
        transformations=(Transformation(kind="redact", span=span, replacement=ssn),),
        findings=(_finding(_PII_DETECTOR, Category.PII, start, end),),
        plan_version="1.1.0",
        deciding_rules=("pii-redact",),
        unavailable_detectors=(),
    )
    return decision, content


def test_noop_transformation_on_a_redact_decision_fails_closed() -> None:
    decision, content = _noop_redact_decision()

    with pytest.raises(VerificationFailure):
        apply_and_verify(decision, _request(content))


# --- Task 16.1: finding-keyed obligations (hand-built, finding_index set) ------------------


def _mixed_secret_credential(
    *,
    secret_index: int | None,
    credential_index: int | None,
    credential_detector: str = _SECRET_DETECTOR,
) -> tuple[Decision, str, str, str]:
    """SECRET -> FLAG + CREDENTIAL -> REDACT on one detector, as the fixed resolver keys it."""
    secret = "sk-test-abcdef123456"
    credential = "-----BEGIN RSA PRIVATE KEY-----"
    content = f"key {secret} pem {credential} end"
    secret_start, secret_end = _span_of(content, secret)
    cred_start, cred_end = _span_of(content, credential)
    decision = Decision(
        disposition=Disposition.REDACT,
        per_finding=(
            FindingDisposition(
                detector=_SECRET_DETECTOR,
                disposition=Disposition.FLAG,
                finding_index=secret_index,
            ),
            FindingDisposition(
                detector=credential_detector,
                disposition=Disposition.REDACT,
                finding_index=credential_index,
            ),
        ),
        transformations=(
            Transformation(
                kind="redact",
                span=Span(start=cred_start, end=cred_end),
                replacement=_PLACEHOLDER,
            ),
        ),
        findings=(
            _finding(_SECRET_DETECTOR, Category.SECRET, secret_start, secret_end),
            _finding(_SECRET_DETECTOR, Category.CREDENTIAL, cred_start, cred_end),
        ),
        plan_version="1.1.0",
        deciding_rules=("credential-redact",),
        unavailable_detectors=(),
    )
    return decision, content, secret, credential


def test_finding_keyed_mixed_plan_masks_only_the_redacting_finding() -> None:
    decision, content, secret, credential = _mixed_secret_credential(
        secret_index=0,
        credential_index=1,
    )

    verified = apply_and_verify(decision, _request(content))

    assert verified.request.text_deltas == ((_CHANNEL, f"key {secret} pem {_PLACEHOLDER} end"),)
    joined = "".join(text for _channel, text in verified.request.text_deltas)
    assert credential not in joined
    assert secret in joined  # FLAG observes; it is not obliged


def test_unkeyed_mixed_plan_keeps_the_detector_keyed_fallback() -> None:
    # No finding_index anywhere -> shipped rule: the FLAG-ed SECRET span is obliged too.
    decision, content, _secret, _credential = _mixed_secret_credential(
        secret_index=None,
        credential_index=None,
    )

    with pytest.raises(VerificationFailure, match="not fully covered"):
        apply_and_verify(decision, _request(content))


def test_partially_keyed_decision_keeps_the_detector_keyed_fallback() -> None:
    decision, content, _secret, _credential = _mixed_secret_credential(
        secret_index=None,
        credential_index=1,
    )

    with pytest.raises(VerificationFailure, match="not fully covered"):
        apply_and_verify(decision, _request(content))


@pytest.mark.parametrize("bad_index", [2, 99, -1])
def test_out_of_range_finding_index_fails_closed(bad_index: int) -> None:
    decision, content, _secret, _credential = _mixed_secret_credential(
        secret_index=0,
        credential_index=bad_index,
    )

    with pytest.raises(VerificationFailure, match="out of range"):
        apply_and_verify(decision, _request(content))


def test_finding_index_naming_another_detectors_finding_fails_closed() -> None:
    decision, content, _secret, _credential = _mixed_secret_credential(
        secret_index=0,
        credential_index=1,
        credential_detector=_PII_DETECTOR,
    )

    with pytest.raises(VerificationFailure, match="another detector"):
        apply_and_verify(decision, _request(content))


# --- Task 16.2: _resplit keeps each delta's own sanitized bytes ---------------------------


def _edit(start: int, end: int, replacement: str = _PLACEHOLDER) -> Transformation:
    return Transformation(kind="redact", span=Span(start=start, end=end), replacement=replacement)


def _resplit_deltas(
    deltas: tuple[tuple[str, str], ...],
    *edits: Transformation,
) -> tuple[tuple[str, str], ...]:
    """Sanitize with the shipped ``apply_decision``, then re-split with ``_resplit``."""
    request = GatewayRequest(destination="amf://v2-leaks", model="gpt-x", text_deltas=deltas)
    decision = Decision(
        disposition=Disposition.REDACT,
        per_finding=(),
        transformations=edits,
        findings=(),
        plan_version="1.1.0",
        deciding_rules=(),
        unavailable_detectors=(),
    )
    content = "".join(text for _channel, text in deltas)
    return _resplit(apply_decision(content, decision, base_offset=0), request, edits)


def test_resplit_without_transformations_returns_identical_deltas() -> None:
    deltas = (("system", "be brief"), ("", ""), ("message_content", "hello there"))

    assert _resplit_deltas(deltas) == deltas


def test_resplit_with_no_deltas_yields_no_deltas() -> None:
    assert _resplit_deltas(()) == ()


def test_resplit_growth_keeps_the_later_delta_byte_identical() -> None:
    # "123" (3 chars) -> "[REDACTED]" (10 chars): delta 0 grows by 7.
    assert _resplit_deltas((("a", "SSN 123"), ("b", "next")), _edit(4, 7)) == (
        ("a", "SSN " + _PLACEHOLDER),
        ("b", "next"),
    )


def test_resplit_shrink_keeps_the_later_delta_byte_identical() -> None:
    deltas = (("a", "token abcdefghijklmnop ok"), ("b", "next"))
    start, end = _span_of(deltas[0][1], "abcdefghijklmnop")

    assert _resplit_deltas(deltas, _edit(start, end, "X")) == (
        ("a", "token X ok"),
        ("b", "next"),
    )


def test_resplit_straddling_value_lands_in_the_start_delta_via_apply_and_verify() -> None:
    # The chat exploration example: an SSN split across two parts of one message.
    channel = "content_part_text"
    request = GatewayRequest(
        destination="amf://v2-leaks",
        model="gpt-x",
        text_deltas=((channel, "My SSN is 123-45"), (channel, "-6789 thanks")),
    )
    start, end = _span_of("My SSN is 123-45-6789 thanks", "123-45-6789")
    decision = resolve(
        (_finding(_PII_DETECTOR, Category.PII, start, end),),
        _plan(_rule("pii-redact", Category.PII, Action.REDACT)),
        Phase.INPUT,
    )

    verified = apply_and_verify(decision, request)

    assert verified.request.text_deltas == (
        (channel, "My SSN is " + _PLACEHOLDER),
        (channel, " thanks"),
    )


def test_resplit_straddle_over_a_whole_middle_delta_empties_it() -> None:
    deltas = (("a", "x 12"), ("b", "34"), ("c", "56 y"))

    assert _resplit_deltas(deltas, _edit(2, 8)) == (
        ("a", "x " + _PLACEHOLDER),
        ("b", ""),
        ("c", " y"),
    )


def test_resplit_keeps_empty_deltas_empty() -> None:
    # The edit starts right after an empty delta: it belongs to the delta holding its first byte.
    deltas = (("a", ""), ("b", "123 tail"), ("c", ""), ("d", "x"))

    assert _resplit_deltas(deltas, _edit(0, 3)) == (
        ("a", ""),
        ("b", _PLACEHOLDER + " tail"),
        ("c", ""),
        ("d", "x"),
    )


def test_resplit_edit_starting_on_a_boundary_belongs_to_the_later_delta() -> None:
    assert _resplit_deltas((("a", "head "), ("b", "123 tail")), _edit(5, 8)) == (
        ("a", "head "),
        ("b", _PLACEHOLDER + " tail"),
    )


def test_resplit_multiple_transformations_across_several_deltas() -> None:
    deltas = (
        ("system", "key sk-1 ok"),
        ("message_content", "mail a@b.c and 99"),
        ("message_content", "88 end"),
        ("tool", "fine"),
    )
    content = "".join(text for _channel, text in deltas)
    sk_start, sk_end = _span_of(content, "sk-1")
    mail_start, mail_end = _span_of(content, "a@b.c")
    num_start, num_end = _span_of(content, "9988")

    assert _resplit_deltas(
        deltas,
        _edit(sk_start, sk_end, "[K]"),
        _edit(mail_start, mail_end, "[EMAIL-REDACTED]"),
        _edit(num_start, num_end, ""),
    ) == (
        ("system", "key [K] ok"),
        ("message_content", "mail [EMAIL-REDACTED] and "),
        ("message_content", " end"),
        ("tool", "fine"),
    )


def test_resplit_fails_closed_when_transformations_do_not_match_the_sanitized_bytes() -> None:
    request = GatewayRequest(
        destination="amf://v2-leaks",
        model="gpt-x",
        text_deltas=(("a", "SSN 123"),),
    )

    with pytest.raises(VerificationFailure, match="does not re-split"):
        _resplit("SSN " + _PLACEHOLDER, request, ())


_RESPLIT_SEED = 0x16_02
_RESPLIT_ITERATIONS = 10_000
_CHANNELS = ("system", "message_content", "content_part_text", "embeddings_input", "tool")


def _random_deltas(rng: random.Random) -> tuple[tuple[str, str], ...]:
    # Lowercase originals; replacements use uppercase/brackets, so they are distinguishable.
    return tuple(
        (
            rng.choice(_CHANNELS),
            "".join(rng.choice("abc -") for _ in range(rng.choice((0, 0, 1, 2, 5, 9)))),
        )
        for _ in range(rng.randint(0, 6))
    )


def _random_edits(rng: random.Random, length: int) -> tuple[Transformation, ...]:
    """Non-overlapping, non-empty spans over ``[0, length)`` in random order."""
    edits: list[Transformation] = []
    cursor = 0
    while cursor < length and rng.random() < 0.7:
        start = rng.randint(cursor, length - 1)
        end = rng.randint(start + 1, min(length, start + 1 + rng.randint(0, 12)))
        replacement = "".join(rng.choice("XYZ[]") for _ in range(rng.randint(0, 12)))
        edits.append(_edit(start, end, replacement))
        cursor = end
    rng.shuffle(edits)
    return tuple(edits)


def _expected_delta(
    content: str,
    delta_start: int,
    delta_end: int,
    edits: tuple[Transformation, ...],
) -> str:
    """Oracle: the delta's own bytes, each edit's replacement placed where the edit starts."""
    starts = {t.span.start: t for t in edits}
    covered = {i for t in edits for i in range(t.span.start, t.span.end)}
    pieces: list[str] = []
    for i in range(delta_start, delta_end):
        if i in starts:
            pieces.append(starts[i].replacement)
        elif i not in covered:
            pieces.append(content[i])
    return "".join(pieces)


def test_property_resplit_keeps_each_delta_its_own_sanitized_bytes() -> None:
    """Property 3 / Property 15: each delta holds exactly its own sanitized bytes.

    Random deltas (incl. empty ones) and random non-overlapping transformations over the
    joined content. Every re-split delta equals its own original text with only its own covered
    bytes replaced (a straddling replacement sits in the delta where the edit starts), the
    concatenation equals the sanitized whole, and delta count and channels are preserved.

    **Validates: Requirements 2.1, 2.3, 2.20**
    """
    rng = random.Random(_RESPLIT_SEED)
    for i in range(_RESPLIT_ITERATIONS):
        deltas = _random_deltas(rng)
        content = "".join(text for _channel, text in deltas)
        edits = _random_edits(rng, len(content))
        request = GatewayRequest(destination="amf://v2-leaks", model="gpt-x", text_deltas=deltas)
        decision = Decision(
            disposition=Disposition.REDACT,
            per_finding=(),
            transformations=edits,
            findings=(),
            plan_version="1.1.0",
            deciding_rules=(),
            unavailable_detectors=(),
        )
        sanitized = apply_decision(content, decision, base_offset=0)
        ctx = f"seed={_RESPLIT_SEED:#x} iteration={i} deltas={deltas!r} edits={edits!r}"

        out = _resplit(sanitized, request, edits)

        assert len(out) == len(deltas), ctx
        assert tuple(channel for channel, _text in out) == tuple(
            channel for channel, _text in deltas
        ), ctx
        assert "".join(text for _channel, text in out) == sanitized, ctx
        offset = 0
        for index, ((_channel, original), (_out, text)) in enumerate(zip(deltas, out, strict=True)):
            expected = _expected_delta(content, offset, offset + len(original), edits)
            assert text == expected, f"{ctx} delta={index}"
            offset += len(original)
        if not edits:
            assert out == deltas, ctx


# --- Task 16.4: verified_upstream, the one request-side path to a provider ------------------


class _CountingTransform(Transform):
    """The shipped ``Transform``, counting every ``to_provider`` call."""

    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls = 0

    def to_provider(self, req: GatewayRequest) -> UpstreamRequest:
        self.calls += 1
        return super().to_provider(req)


def _upstream_deltas(upstream: UpstreamRequest) -> object:
    return upstream.body[BODY_DELTAS_KEY]


def _ssn_redact_decision(content: str, ssn: str) -> Decision:
    start, end = _span_of(content, ssn)
    decision = resolve(
        (_finding(_PII_DETECTOR, Category.PII, start, end),),
        _plan(_rule("pii-redact", Category.PII, Action.REDACT)),
        Phase.INPUT,
    )
    assert decision.disposition is Disposition.REDACT
    return decision


def test_verified_upstream_packs_only_the_masked_deltas() -> None:
    ssn = "123-45-6789"
    request = GatewayRequest(
        destination="amf://v2-leaks/upstream",
        model="gpt-x",
        text_deltas=((_CHANNEL, "SSN " + ssn), ("system", "be brief")),
    )
    decision = _ssn_redact_decision("SSN " + ssn + "be brief", ssn)
    auth = authorize(decision)
    assert auth is not None
    transform = _CountingTransform()

    upstream = verified_upstream(auth, decision=decision, request=request, transform=transform)

    assert transform.calls == 1
    assert upstream.destination == "amf://v2-leaks/upstream"
    assert _upstream_deltas(upstream) == (
        (_CHANNEL, "SSN " + _PLACEHOLDER),
        ("system", "be brief"),
    )
    assert ssn not in repr(dict(upstream.body))


def test_verified_upstream_rejects_a_forged_authorization_before_any_shaping() -> None:
    decision = _ssn_redact_decision("SSN 123-45-6789", "123-45-6789")
    # object.__new__ skips __post_init__: the witness slot is never set.
    forged = object.__new__(DispatchAuthorization)
    transform = _CountingTransform()

    with pytest.raises(ValueError, match="provenance"):
        verified_upstream(
            forged,
            decision=decision,
            request=_request("SSN 123-45-6789"),
            transform=transform,
        )

    assert transform.calls == 0


def test_verified_upstream_rejects_a_non_authorization_object() -> None:
    decision = _ssn_redact_decision("SSN 123-45-6789", "123-45-6789")
    transform = _CountingTransform()

    with pytest.raises(ValueError, match="provenance"):
        verified_upstream(
            object(),  # type: ignore[arg-type]
            decision=decision,
            request=_request("SSN 123-45-6789"),
            transform=transform,
        )

    assert transform.calls == 0


def test_verified_upstream_propagates_a_noop_redact_verification_failure() -> None:
    decision, content = _noop_redact_decision()
    auth = authorize(decision)
    assert auth is not None
    transform = _CountingTransform()

    with pytest.raises(VerificationFailure):
        verified_upstream(auth, decision=decision, request=_request(content), transform=transform)

    assert transform.calls == 0


def test_block_decision_mints_no_authorization() -> None:
    content = "key sk-test-abcdef123456 end"
    start, end = _span_of(content, "sk-test-abcdef123456")
    decision = resolve(
        (_finding(_SECRET_DETECTOR, Category.SECRET, start, end),),
        _plan(_rule("secret-block", Category.SECRET, Action.BLOCK)),
        Phase.INPUT,
    )
    assert decision.disposition is Disposition.BLOCK

    # No authorization exists, so verified_upstream (which requires one) is unreachable.
    assert authorize(decision) is None


_FLAGGED_SECRET = "sk-test-abcdef123456"


@pytest.mark.parametrize(
    ("disposition", "deltas"),
    [
        (Disposition.ALLOW, ()),
        (Disposition.ALLOW, ((_CHANNEL, ""),)),
        (Disposition.ALLOW, (("system", "be brief"), ("", ""), ("content_part_text", "héllo 🌍"))),
        (Disposition.FLAG, ((_CHANNEL, f"key {_FLAGGED_SECRET} end"),)),
        (Disposition.FLAG, (("system", "key sk-test-"), (_CHANNEL, "abcdef123456 end"))),
    ],
)
def test_verified_upstream_keeps_allow_and_flag_deltas_byte_identical(
    disposition: Disposition,
    deltas: tuple[tuple[str, str], ...],
) -> None:
    """Preservation 3.2: no redacting disposition -> the provider gets the original deltas."""
    content = "".join(text for _channel, text in deltas)
    if disposition is Disposition.FLAG:
        start, end = _span_of(content, _FLAGGED_SECRET)
        findings: tuple[Finding, ...] = (_finding(_SECRET_DETECTOR, Category.SECRET, start, end),)
    else:
        findings = ()
    decision = resolve(
        findings,
        _plan(_rule("secret-flag", Category.SECRET, Action.FLAG)),
        Phase.INPUT,
    )
    assert decision.disposition is disposition
    auth = authorize(decision)
    assert auth is not None
    request = GatewayRequest(destination="amf://v2-leaks", model="gpt-x", text_deltas=deltas)

    upstream = verified_upstream(auth, decision=decision, request=request, transform=Transform())

    assert _upstream_deltas(upstream) == deltas
    assert upstream.destination == "amf://v2-leaks"


class _CapturingClient:
    """A ``ProviderClient`` recording every ``UpstreamRequest`` it is opened with."""

    __slots__ = ("seen",)

    def __init__(self) -> None:
        self.seen: list[UpstreamRequest] = []

    async def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.seen.append(request)
        yield UpstreamEvent(text_deltas=(), final=True)

    async def abort(self) -> None:
        return None


class _NullSeams:
    """Audit, rewrite, scan and wait-metric seams that do nothing."""

    __slots__ = ()

    def record_started(self, request_id: str, destination: str) -> None:
        _ = (request_id, destination)

    async def classify(self, windows: Sequence[str]) -> Sequence[object]:
        _ = windows
        return ()

    def scan_text(self, payload: str) -> str:
        return payload

    def observe_wait(self, seconds: float) -> None:
        _ = seconds


def test_authorized_entry_dispatch_sends_exactly_the_verified_upstream() -> None:
    ssn = "123-45-6789"
    request = _request("SSN " + ssn, "next message")
    decision = _ssn_redact_decision("SSN " + ssn + "next message", ssn)
    auth = authorize(decision)
    assert auth is not None
    plan = _plan(_rule("pii-redact", Category.PII, Action.REDACT))
    router = DispatchRouter()
    client = _CapturingClient()
    seams = _NullSeams()
    entry = AuthorizedProviderEntry(
        client=client,
        transform=Transform(),
        router=router,
        pool=ProviderPool(bound=1, wait_metric=seams),
        rewrite=seams,
        scan_seam=seams,
        audit=seams,
    )

    async def drain() -> None:
        async for _event in entry.dispatch(
            auth,
            decision=decision,
            request=request,
            plan=plan,
            catalogue=ProviderCatalogue(org_id=plan.org_id, entries=()),
            request_id="req-v2-leaks",
        ):
            pass

    asyncio.run(drain())

    # The entry stamps the plan-derived destination, then runs the shared request-side path.
    expected = verified_upstream(
        auth,
        decision=decision,
        request=GatewayRequest(
            destination=router.select(plan),
            model=request.model,
            text_deltas=request.text_deltas,
        ),
        transform=Transform(),
    )
    assert len(client.seen) == 1
    sent = client.seen[0]
    assert sent.destination == expected.destination
    assert dict(sent.body) == dict(expected.body)
    assert _upstream_deltas(sent) == ((_CHANNEL, "SSN " + _PLACEHOLDER), (_CHANNEL, "next message"))
