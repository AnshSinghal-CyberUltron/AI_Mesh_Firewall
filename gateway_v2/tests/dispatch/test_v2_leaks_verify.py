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

import pytest

from gateway_v2.dispatch.transform import GatewayRequest
from gateway_v2.dispatch.verify import VerificationFailure, apply_and_verify
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
from gateway_v2.resolve import resolve

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


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "v2-enforcement-leaks A: _resplit keeps delta 0 at its original 15 chars after the "
        "11-char SSN becomes the 10-char [REDACTED]; deltas ('SSN 123-45-6789', 'next message') "
        "-> ('SSN [REDACTED]n', 'ext message') -- byte 'n' of delta 1 moves into delta 0"
    ),
)
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


@pytest.mark.xfail(
    strict=True,
    raises=VerificationFailure,
    reason=(
        "v2-enforcement-leaks A: SECRET->flag + CREDENTIAL->redact, both findings from "
        "deterministic.secret; _redacting_detectors obliges the FLAG-ed SECRET span 4:24 too, "
        "which has no transformation -> false VerificationFailure 'not fully covered "
        "(span=4:24, covered_up_to=4)'"
    ),
)
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
    # detector id, and a transformation only over the CREDENTIAL span.
    assert decision.disposition is Disposition.REDACT
    assert decision.per_finding == (
        FindingDisposition(detector=_SECRET_DETECTOR, disposition=Disposition.FLAG),
        FindingDisposition(detector=_SECRET_DETECTOR, disposition=Disposition.REDACT),
    )
    assert tuple(t.span for t in decision.transformations) == (Span(cred_start, cred_end),)

    verified = apply_and_verify(decision, _request(content))

    assert verified.request.text_deltas == ((_CHANNEL, f"key {secret} pem {_PLACEHOLDER} end"),)


# --- Case 3: a no-op transformation on a REDACT decision fails closed (baseline) -----------


def test_noop_transformation_on_a_redact_decision_fails_closed() -> None:
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

    with pytest.raises(VerificationFailure):
        apply_and_verify(decision, _request(content))
