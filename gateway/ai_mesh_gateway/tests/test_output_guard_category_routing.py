"""Output-guard regressions (2026-07-17): tier-2 category routing, the
guard-rated-clean ip_leakage FP-reduction, and the already-masked visibility flag.

Three defects found by the pre-push adversarial audit:

1. ROUTING FAIL-OPEN. ``_operator_action_for_category`` mapped a tier-2 guard-model
   finding to a per-detector action using order-dependent substring ``in`` checks with
   the PII branch FIRST. Generic PII words collide with other categories'
   labels — 'personal_access_token' contains 'personal', 'ip_address' contains
   'address' — so under a legitimate asymmetric config (pii="allow" +
   credential="block") a real credential was routed to the DISABLED pii detector and
   silently dropped: the operator's credential="block" was never honored.

2. DEAD FP-REDUCTION. The guard-rated-clean ip_leakage suppression was gated on
   ``_ip_op_action != "allow"``, but action=="allow" skips the ip detector entirely,
   so the suppression was unreachable — benign textbook IPs the tier-2 guard had
   cleared were redacted anyway, contradicting the in-file comment.

3. ALREADY-MASKED WAS INVISIBLE. Output the model had already masked
   (j***@a***.com) produced NO verdict at all, leaving operators blind to masked
   PII passing through. It must FLAG (visible) while leaving bytes untouched.
"""

from __future__ import annotations

from output_guard import OutputGuard, OutputVerdict, sanitize_output_for_verdict

import patterns

BENIGN = "The deployment completed successfully."
# NOTE: 192.168.x.x is deliberately NOT flagged by detect_ip_leakage (common benign
# example), so it would make these assertions pass vacuously. 10.0.0.5 does flag.
IP_TEXT = "Connect to 10.0.0.5 internally."
MASKED = (
    'Example:\n```json\n{\n  "ssn": "***-**-6789",\n'
    '  "email": "j***@a***.com",\n'
    '  "phone": "***-***-5309",\n'
    '  "credit_card": "****-****-****-1111"\n}\n```\n'
)

# Every category gets a DISTINCT action so the resulting action identifies which
# detector the tier-2 finding was routed to.
DISTINCT = {
    "output_pii_action": "allow",           # operator DISABLED pii
    "output_credential_action": "block",
    "output_ip_leakage_action": "redact",
    "output_hallucination_action": "flag",
    "output_policy_action": "rewrite",
}


class _FakeScanner:
    """Static output scan is clean; tier-2 returns a chosen finding (or clean)."""

    def __init__(self, t2_threat: str = "", t2_action: str = "block"):
        self._t2_threat = t2_threat
        self._t2_action = t2_action

    async def scan_output(self, text):
        return OutputVerdict()

    async def scan_output_with_tier2(self, text, org_tier2_override=None, org_slug=None):
        if not self._t2_threat:
            # Guard model ran and rated the output CLEAN.
            return OutputVerdict(action="allow", threat_type="")
        return OutputVerdict(
            action=self._t2_action,
            threat_type=self._t2_threat,
            confidence=0.9,
            detail="tier-2 finding",
            matched_patterns=[self._t2_threat],
        )


async def _route(threat: str, config: dict = None) -> str:
    guard = OutputGuard(scanner=_FakeScanner(threat), config=dict(config or DISTINCT))
    verdict = await guard.inspect(BENIGN)
    return verdict.action


# ── 1. routing: credential / ip_leakage matched BEFORE pii ──


async def test_credential_label_containing_pii_word_is_not_dropped():
    # 'personal_access_token' contains the pii word 'personal'. It is a CREDENTIAL:
    # the operator's credential="block" must win, not the disabled pii detector.
    assert await _route("personal_access_token") == "block"


async def test_ip_label_containing_pii_word_routes_to_ip_leakage():
    # 'ip_address' contains the pii word 'address' but is infra leakage.
    assert await _route("ip_address") == "redact"


async def test_pii_label_still_routes_to_pii_and_honors_allow():
    # A genuine PII label still hits the pii detector; operator set it to "allow",
    # so the finding is dropped and nothing is enforced.
    assert await _route("pii_email_exposure") == "allow"


async def test_uncertain_is_not_mistaken_for_certificate():
    # 'uncertain_claim' contains the substring 'cert' but is not a credential;
    # token matching keeps it out of the credential branch (falls to policy).
    assert await _route("uncertain_claim") == "rewrite"


async def test_zip_code_is_not_mistaken_for_ip():
    # 'zip_code' contains the substring 'ip' but is not infra leakage.
    assert await _route("zip_code") != "redact"


# ── 2. masking labels: pure detection allows, evasion does NOT ──


async def test_pure_masking_detection_is_allowed():
    assert await _route("pii_masking_detection") == "allow"


async def test_masking_evasion_labels_are_not_swallowed():
    for label in ("masking_bypass", "unmasking_attempt"):
        assert await _route(label) != "allow", label


# ── 3. ip_leakage guard-rated-clean FP-reduction ──


async def test_default_org_gets_ip_fp_reduction_when_guard_rates_clean():
    # No explicit output_ip_leakage_action => default org => a tier-2 "clean"
    # rating suppresses the noisy static ip_leakage verdict.
    guard = OutputGuard(scanner=_FakeScanner(""), config={})
    verdict = await guard.inspect(IP_TEXT)
    assert verdict.threat_type != "ip_leakage"


async def test_explicit_ip_action_disables_fp_reduction():
    # An EXPLICIT operator choice wins: the guard model cannot silently drop it.
    guard = OutputGuard(
        scanner=_FakeScanner(""), config={"output_ip_leakage_action": "redact"}
    )
    verdict = await guard.inspect(IP_TEXT)
    assert verdict.threat_type == "ip_leakage"
    assert verdict.action == "redact"


# ── 4. FROZEN: already-masked output triggers NO action, under EVERY setting ──
#
# The operator is the SOLE owner of their org's actions. Already-masked output
# (j***@a***.com) contains no raw PII, so it is not a finding and MUST produce no
# action at all — not block, not redact, not rewrite, and NOT a "harmless"
# visibility flag. Emitting anything here would show an action the operator never
# selected (an org configured "rewrite" seeing "flag"), which has regressed twice.
# The configured action applies ONLY to genuinely raw PII.


async def test_already_masked_output_never_triggers_any_action():
    for configured in ("block", "redact", "rewrite", "flag", "allow"):
        guard = OutputGuard(
            scanner=_FakeScanner(""),
            config={"pii_detection_enabled": True, "output_pii_action": configured},
        )
        verdict = await guard.inspect(MASKED)
        # "allow" is the empty/no-finding verdict — i.e. the guard took NO action.
        assert verdict.action == "allow", (
            f"operator selected {configured!r}; already-masked output must yield NO "
            f"action, got {verdict.action!r}"
        )
        assert not verdict.matched_patterns, configured


async def test_already_masked_output_is_delivered_byte_identical():
    for configured in ("block", "redact", "rewrite", "flag", "allow"):
        guard = OutputGuard(
            scanner=_FakeScanner(""),
            config={"pii_detection_enabled": True, "output_pii_action": configured},
        )
        verdict = await guard.inspect(MASKED)
        out = sanitize_output_for_verdict(
            MASKED, verdict, redact_pii_fn=patterns.redact_all
        )
        assert out == MASKED, configured
        assert "[PII_REDACTED]" not in out, configured
        assert "j***@a***.com" in out, configured
