"""
E14 — ADVERSARIAL leak hunt for Hallucination / Grounding (module 1.7).

Recon claims under test:
  * ungrounded responses get a metrics-only verdict; FLAG delivers content
    UNCHANGED by design;
  * the grounding embedder byte-verifies ``assume_redacted``
    (``bedrock_embedder.assert_no_residual_pii``) so RAW PII never reaches AWS.

Three hunt dimensions:
  (a) Does a hallucination/grounding FLAG ever deliver ungrounded content that
      CONTAINS PII raw? The PII path must still redact it INDEPENDENTLY — they
      must run TOGETHER (precedence), not either/or.
  (b) Can the grounding embedder be reached with ``assume_redacted=True`` but
      RESIDUAL secret/PII? i.e. does ``assert_no_residual_pii`` actually cover
      everything the gateway itself classifies as a leak before it is shipped
      to AWS Bedrock?
  (c) Does the grounding detail / telemetry leak the raw fabricated content?

The hard FINDING (a FAILING repro) is dimension (b): the gateway classifies a
JWT as BOTH a secret and a credential (``detect_secrets`` / ``detect_credential_exposure``),
so the client never sees it raw — but ``assert_no_residual_pii`` does NOT cover
JWTs, so the semantic grounding path embeds the **raw JWT to AWS Bedrock**.
That breaks the "raw secrets never reach AWS" invariant of module 1.7.

Run:
    cd gateway && .venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_e14_grounding.py -v
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from ai_mesh_gateway.output_guard import OutputGuard, OutputVerdict
from ai_mesh_gateway.patterns import (
    detect_credential_exposure,
    detect_pii,
    detect_secrets,
)
from ai_mesh_gateway.rag_pipeline.bedrock_embedder import (
    BedrockEmbedder,
    PIIRedactionRequiredError,
    assert_no_residual_pii,
)
from ai_mesh_gateway.rag_pipeline.grounding_guard import GroundingGuard

# A structurally-valid JWT (header.payload.signature). The gateway treats this
# as a secret AND a credential; a real bearer/session JWT in an LLM answer is a
# classic output leak.
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0"
    ".dozjgNryP4J3jVmNHl0w5N0iceLLkSKtQbBj4nqgY3o"
)
SSN = "123-45-6789"
EMAIL = "alice@example.com"


# --------------------------------------------------------------------------- #
# Recording bedrock client — captures every inputText that reaches "AWS".      #
# --------------------------------------------------------------------------- #
class _RecordingBedrockClient:
    """Stand-in boto3 bedrock-runtime client that records what is embedded.

    ``sent_texts`` accumulates every ``inputText`` that was about to leave the
    process for AWS. The test asserts that no raw secret/PII string appears in
    that list.
    """

    def __init__(self) -> None:
        self.sent_texts: List[str] = []

    def invoke_model(self, *, modelId: str, contentType: str, accept: str, body: str):
        payload = json.loads(body)
        self.sent_texts.append(payload.get("inputText", ""))
        resp_body = MagicMock()
        resp_body.read.return_value = json.dumps(
            {"embedding": [0.01] * BedrockEmbedder.DIMENSIONS}
        ).encode()
        return {"body": resp_body}


class _FakeScanVerdict:
    def __init__(
        self,
        threat_type: str = "allow",
        matched_patterns: Optional[List[str]] = None,
        matched_values: Optional[Dict[str, str]] = None,
        confidence: float = 0.9,
        action: str = "allow",
    ) -> None:
        self.threat_type = threat_type
        self.matched_patterns = matched_patterns or []
        self.matched_values = matched_values or {}
        self.confidence = confidence
        self.action = action


class _FakeScanner:
    """Minimal scanner that mirrors how the real gateway flags secrets.

    ``scan_output`` reports a 'secret' verdict whenever ``detect_secrets`` hits
    and a 'pii' verdict for ``detect_pii`` hits (secrets take precedence), so the
    OutputGuard PII/secret path behaves like the production ``InputScanner.scan_output``.
    ``scan_output_with_tier2`` is a clean no-op (tier-2 disabled in these unit tests).
    """

    async def scan_output(self, text: str) -> _FakeScanVerdict:
        secrets = detect_secrets(text)
        if secrets:
            return _FakeScanVerdict(
                threat_type="secret",
                matched_patterns=list(secrets.keys()),
                matched_values=dict(secrets),
                confidence=0.95,
            )
        pii = detect_pii(text)
        if pii:
            return _FakeScanVerdict(
                threat_type="pii",
                matched_patterns=list(pii.keys()),
                matched_values=dict(pii),
                confidence=0.92,
            )
        return _FakeScanVerdict()

    async def scan_output_with_tier2(self, text: str, **_kw) -> None:
        return None


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def recording_client() -> _RecordingBedrockClient:
    return _RecordingBedrockClient()


@pytest.fixture()
def embedder(recording_client) -> BedrockEmbedder:
    # No circuit breaker -> embedder will actually call invoke_model on success.
    return BedrockEmbedder(bedrock_client=recording_client, circuit_breaker=None)


@pytest.fixture()
def guard_semantic(embedder) -> OutputGuard:
    """OutputGuard wired with the semantic grounding backend (Titan v2)."""
    og = OutputGuard(scanner=_FakeScanner(), config={})
    og.set_grounding_guard(GroundingGuard(embedder=embedder))
    return og


# RAG context that the answer is ungrounded against (forces a hallucination
# flag and forces the semantic embed path to actually fire).
_UNRELATED_CONTEXT = [
    "The capital of France is Paris. The Eiffel Tower is a famous landmark.",
    "Photosynthesis converts sunlight into chemical energy in plants.",
]

_SEMANTIC_CFG = {
    "hallucination_grounding_mode": "semantic",
    "hallucination_grounding_threshold": 0.2,
    "output_tier2_enabled": False,
}


# --------------------------------------------------------------------------- #
# (a) PII + hallucination run TOGETHER — redact wins over flag (precedence).   #
#     A flagged ungrounded answer that ALSO contains PII must still be redacted #
#     for the client (not delivered "unchanged").                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pii_still_redacted_even_when_also_hallucination_flagged(guard_semantic):
    """An ungrounded (flag) answer that also contains an SSN/email must come
    back action='redact' (priority 3) — NOT 'flag' (priority 1). Proves the PII
    path and the hallucination path are evaluated together via precedence, so a
    'flag delivers content unchanged' policy can never ship raw PII to the client.
    """
    answer = (
        f"Per my earlier note the customer SSN is {SSN} and email {EMAIL}; "
        "the moon is made of cheese and was last serviced in 1842."
    )
    verdict = await guard_semantic.inspect(
        answer,
        context_chunks=list(_UNRELATED_CONTEXT),
        org_config=_SEMANTIC_CFG,
        org_slug="acme",
    )
    # redact (3) must beat flag (1) — PII is independently caught and wins.
    assert verdict.action == "redact", (
        f"PII-bearing ungrounded answer was delivered as action={verdict.action!r}; "
        "the hallucination FLAG path must not suppress PII redaction."
    )
    # The client-facing detail must never echo the raw SSN/email.
    assert SSN not in (verdict.detail or "")
    assert EMAIL not in (verdict.detail or "")


# --------------------------------------------------------------------------- #
# (b) THE FINDING — grounding embedder egress of a gateway-classified secret.  #
#     assert_no_residual_pii does NOT cover JWTs, so the raw JWT is embedded    #
#     to AWS Bedrock even though the gateway redacts it from the client.        #
# --------------------------------------------------------------------------- #
def test_jwt_is_a_gateway_classified_secret_and_embedder_backstop_now_covers_it():
    """REGRESSION GUARD (fixed reality — unified backstop inventory).

    The gateway flags a JWT as a secret AND a credential (so it WILL be redacted
    from the client). ``assert_no_residual_pii`` was NARROWER than that inventory,
    so the grounding embedder shipped the raw JWT to AWS Bedrock. The E14 fix
    unified ``_RESIDUAL_PII_PATTERNS`` with the gateway's secret/credential
    inventory, so the backstop now fail-closes on a raw JWT before the boto3 call.
    This test PINS that invariant (it would re-fail if the backstop drifts back).
    """
    assert detect_secrets(JWT), "precondition: gateway must classify JWT as a secret"
    assert detect_credential_exposure(JWT), "precondition: JWT is also a credential"

    # The backstop now covers JWTs -> assert_no_residual_pii MUST raise (fail-closed).
    try:
        assert_no_residual_pii(f"token={JWT}")
        backstop_caught = False
    except PIIRedactionRequiredError:
        backstop_caught = True

    assert backstop_caught, (
        "REGRESSION: assert_no_residual_pii no longer fail-closes on a raw JWT — "
        "the embedder backstop has drifted apart from the gateway's secret "
        "inventory again, re-opening the AWS grounding-egress hole."
    )


@pytest.mark.asyncio
async def test_grounding_path_does_not_egress_raw_secret_to_aws(
    guard_semantic, recording_client
):
    """END-TO-END LEAK REPRO (currently FAILING, by design).

    An ungrounded LLM answer that contains a raw JWT goes through OutputGuard
    with semantic grounding. The PII/secret detector redacts it from the client,
    but the semantic grounding embedder ALREADY shipped the raw answer text to
    AWS Bedrock during scoring — because ``assert_no_residual_pii`` does not
    cover JWTs. Assert no raw secret string ever reached the (recording) AWS
    client.

    fix_hint: add JWT (eyJ...\\..\\..), Slack/GitHub-PAT/Stripe/SendGrid and a
    BEGIN PRIVATE KEY token to ``_RESIDUAL_PII_PATTERNS`` in
    ``rag_pipeline/bedrock_embedder.py`` so ``assert_no_residual_pii`` fail-closes
    on every category the gateway's ``detect_secrets`` / ``detect_credential_exposure``
    already treat as a leak (unify the two inventories). The embed() residual
    check runs BEFORE the boto3 call, so a covered pattern blocks egress.
    """
    answer = (
        f"Your authenticated session token is {JWT}. "
        "I also confirm the merger closed yesterday at 4pm sharp."  # ungrounded
    )
    verdict = await guard_semantic.inspect(
        answer,
        context_chunks=list(_UNRELATED_CONTEXT),
        org_config=_SEMANTIC_CFG,
        org_slug="acme",
    )
    # Client side: the secret IS redacted (the gateway does its job there).
    assert verdict.action in ("redact", "block")

    # AWS side: NOTHING raw should have left the process. This is the leak.
    leaked = [t for t in recording_client.sent_texts if JWT in t]
    assert not leaked, (
        "RAW SECRET EGRESS: the grounding embedder sent the raw JWT to AWS "
        f"Bedrock during semantic scoring ({len(leaked)} call(s)). "
        "assert_no_residual_pii failed to cover a gateway-classified secret. "
        f"sent_texts={[t[:60] for t in recording_client.sent_texts]}"
    )


# --------------------------------------------------------------------------- #
# (b-positive) The backstop DOES fail-closed for its covered categories, and   #
#     that prevents the AWS egress. Proves the mechanism works when coverage    #
#     exists (so the fix = extend coverage, not re-architect).                  #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_covered_pii_email_blocks_aws_egress(guard_semantic, recording_client):
    """An answer containing a raw email (a COVERED residual pattern) must NOT be
    embedded to AWS: the embedder fail-closes, semantic falls back to lexical,
    and no raw email reaches the recording client.
    """
    answer = (
        f"Contact the admin at {EMAIL} for the unicorn-powered server you imagined."
    )
    await guard_semantic.inspect(
        answer,
        context_chunks=list(_UNRELATED_CONTEXT),
        org_config=_SEMANTIC_CFG,
        org_slug="acme",
    )
    leaked = [t for t in recording_client.sent_texts if EMAIL in t]
    assert not leaked, (
        "Covered residual pattern (email) STILL reached AWS — the fail-closed "
        "byte-verify backstop is not actually gating egress."
    )


# --------------------------------------------------------------------------- #
# (c) Grounding detail / telemetry must not echo the raw fabricated content.   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_grounding_detail_does_not_leak_raw_fabricated_content(guard_semantic):
    """The hallucination verdict detail is client-facing; it must report SCORES,
    not the raw fabricated/ungrounded answer text.
    """
    fabricated = (
        "The secret merger price is 4.2 billion dollars and the CEO codename is "
        "BLUEFALCON, none of which appears in any provided document."
    )
    verdict = await guard_semantic.inspect(
        fabricated,
        context_chunks=list(_UNRELATED_CONTEXT),
        org_config={
            "hallucination_grounding_mode": "semantic",
            "hallucination_grounding_threshold": 0.99,  # force ungrounded
            "output_tier2_enabled": False,
        },
        org_slug="acme",
    )
    detail = verdict.detail or ""
    # Detail should be the scores summary, never the raw fabricated sentence.
    assert "BLUEFALCON" not in detail
    assert "4.2 billion" not in detail
    assert re.search(r"hallucination_risk=", detail) or verdict.action == "allow"
