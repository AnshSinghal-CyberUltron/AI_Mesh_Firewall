"""Output-guard regressions: tier-2 category routing, the guard-rated-clean
ip_leakage FP-reduction, the frozen already-masked no-action contract, and
per-class (PII vs credential) action governance.

Defects found by the pre-push adversarial audit + the 30-cell operator-action
matrix (5 detectors x {block, redact, rewrite, flag, allow, detect-off}):

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

3. ALREADY-MASKED MUST TRIGGER NO ACTION (frozen). Output the model already masked
   (j***@a***.com) contains no raw PII, so it is not a finding: the guard emits NO
   action — not block/redact/rewrite and NOT a "visibility flag". Emitting anything
   shows an action the operator never selected (an org set to "rewrite" seeing
   "flag"). This has regressed twice; see section 4.

4. CREDENTIAL ACTION WAS POWERLESS. PII_PATTERNS contains credential keys, so
   scan_output labelled API keys threat_type='pii' and governed them with
   output_pii_action. Measured: PII=allow + CRED=block -> the keys EGRESSED despite
   an explicit block. Classes are now partitioned by the SECRET compliance tag and
   each is governed by its own operator-selected action; see section 5.
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


# ── 5. PER-CLASS GOVERNANCE: credential keys obey output_credential_action ──
#
# PII_PATTERNS contains credential keys (api_key_openai, aws_access_key,
# aws_secret_access_key, github_token, private_key_header), so scan_output reported
# an API key as threat_type='pii' and the guard governed it with output_pii_action.
# The operator's "Credential Exposure" action was POWERLESS. Measured before the fix:
#   PII=allow, CRED=block -> allow   (operator chose BLOCK; the keys EGRESSED)
# Classification is by COMPLIANCE_TAG_MAP ("SECRET" tag), not a hardcoded key list,
# so future SECRET-tagged patterns route correctly with no code change.

CRED_TEXT = (
    "Use key sk-proj-AbCdEf0123456789AbCdEf0123456789 and "
    "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE"
)
SECRET_TEXT = (
    "The database password=SuperSecret123! and anthropic key "
    "sk-ant-AbCdEf0123456789AbCdEfGh"
)
MIXED_TEXT = (
    "Contact john.smith@acme.com with key sk-proj-AbCdEf0123456789AbCdEf0123456789"
)


def _pc_cfg(pii_enabled, pii_action, cred_enabled, cred_action):
    return {
        "output_tier2_enabled": False,
        "output_pii_enabled": pii_enabled, "output_pii_action": pii_action,
        "output_credential_enabled": cred_enabled, "output_credential_action": cred_action,
        "output_ip_leakage_enabled": False, "output_ip_leakage_action": "allow",
        "output_policy_enabled": False, "output_policy_action": "allow",
        "hallucination_flag_enabled": False, "factuality_check_enabled": False,
        "output_hallucination_action": "allow",
    }


async def _pc_inspect(text, cfg):
    from scanner import InputScanner
    guard = OutputGuard(scanner=_RealStatic(InputScanner()), config=cfg)
    return await guard.inspect(text)


class _RealStatic:
    """Real static scanner (so credential patterns actually match); tier-2 clean."""

    def __init__(self, real):
        self._real = real

    async def scan_output(self, text):
        return await self._real.scan_output(text)

    async def scan_output_with_tier2(self, text, org_tier2_override=None, org_slug=None):
        return OutputVerdict(action="allow", threat_type="")


async def test_credential_blocked_even_when_pii_is_allow():
    # THE HEADLINE BUG: operator set Credential=block, PII=allow. Keys must NOT egress.
    v = await _pc_inspect(CRED_TEXT, _pc_cfg(True, "allow", True, "block"))
    assert v.action == "block"
    assert v.threat_type == "credential"


async def test_credential_blocked_when_pii_detector_disabled():
    v = await _pc_inspect(CRED_TEXT, _pc_cfg(False, "allow", True, "block"))
    assert v.action == "block"
    assert v.threat_type == "credential"


async def test_credential_honors_each_action_independently_of_pii():
    for act in ("block", "redact", "rewrite", "flag"):
        v = await _pc_inspect(CRED_TEXT, _pc_cfg(True, "allow", True, act))
        assert v.action == act, act
        assert v.threat_type == "credential", act


async def test_operator_may_allow_credentials_even_when_pii_blocks():
    # Sole-ownership: CRED=allow / disabled means NO action, whatever PII says.
    for cred_enabled, cred_action in ((True, "allow"), (False, "allow")):
        v = await _pc_inspect(CRED_TEXT, _pc_cfg(True, "block", cred_enabled, cred_action))
        assert v.action == "allow", (cred_enabled, cred_action)


async def test_secret_family_is_credential_governed():
    # threat_type=='secret' keys (password_assignment, anthropic_key, ...) are
    # SECRET-tagged => governed by output_credential_action, not output_pii_action.
    v = await _pc_inspect(SECRET_TEXT, _pc_cfg(True, "allow", True, "block"))
    assert v.action == "block"
    v = await _pc_inspect(SECRET_TEXT, _pc_cfg(True, "block", True, "allow"))
    assert v.action == "allow"


async def test_mixed_pii_and_credential_both_masked_on_redact():
    v = await _pc_inspect(MIXED_TEXT, _pc_cfg(True, "redact", True, "redact"))
    out = sanitize_output_for_verdict(MIXED_TEXT, v, redact_pii_fn=patterns.redact_all)
    assert "john.smith@acme.com" not in out
    assert "sk-proj-AbCdEf0123456789AbCdEf0123456789" not in out


async def test_masked_output_no_action_across_all_pii_cred_combos():
    # FROZEN contract must survive the partition, under every (pii, cred) pairing.
    for p in ("block", "redact", "rewrite", "flag", "allow"):
        for c in ("block", "redact", "rewrite", "flag", "allow"):
            v = await _pc_inspect(MASKED, _pc_cfg(True, p, True, c))
            assert v.action == "allow", (p, c)
            assert not v.matched_patterns, (p, c)
            out = sanitize_output_for_verdict(MASKED, v, redact_pii_fn=patterns.redact_all)
            assert out == MASKED, (p, c)


# ── 6. STRICTLY-WHAT-THE-OPERATOR-SELECTED: per-class masking ──
#
# Each detector carries its OWN action, but sanitization used the blanket
# redact_all(), which masks PII + credential + infra all at once. So an output
# containing an email AND an API key, with PII="flag" + Credential="redact", had
# the EMAIL masked too — mutating a class the operator explicitly chose not to
# mutate. Measured 4 violations across the 9 delivered (redact/flag/allow) cells.
# Only classes whose action is "redact" may be mutated.

_EMAIL = "john.smith@acme.com"
_KEY = "sk-proj-AbCdEf0123456789AbCdEf0123456789"
_IP = "10.0.0.5"
MIXED3 = f"Contact {_EMAIL} with key {_KEY} on host {_IP} please."
_DELIVERED = ("redact", "flag", "allow")


def _cfg3(pii, cred, ip):
    return {
        "output_tier2_enabled": False,
        "output_pii_enabled": True, "output_pii_action": pii,
        "output_credential_enabled": True, "output_credential_action": cred,
        "output_ip_leakage_enabled": True, "output_ip_leakage_action": ip,
        "output_policy_enabled": False, "output_policy_action": "allow",
        "hallucination_flag_enabled": False, "factuality_check_enabled": False,
        "output_hallucination_action": "allow",
    }


async def _deliver(text, cfg):
    from scanner import InputScanner
    guard = OutputGuard(scanner=_RealStatic(InputScanner()), config=cfg)
    verdict = await guard.inspect(text)
    return sanitize_output_for_verdict(text, verdict, redact_pii_fn=patterns.redact_all)


async def test_strict_only_redact_classes_are_mutated_pii_vs_credential():
    for pii in _DELIVERED:
        for cred in _DELIVERED:
            out = await _deliver(MIXED3, _cfg3(pii, cred, "allow"))
            assert (_EMAIL in out) is (pii in ("flag", "allow")), (pii, cred, "email")
            assert (_KEY in out) is (cred in ("flag", "allow")), (pii, cred, "key")


async def test_strict_ip_redact_does_not_mutate_pii_or_credential():
    # An ip_leakage redact must mask ONLY the infra address.
    out = await _deliver(MIXED3, _cfg3("flag", "allow", "redact"))
    assert _EMAIL in out
    assert _KEY in out
    assert _IP not in out


async def test_strict_three_way_matrix():
    for pii in _DELIVERED:
        for cred in _DELIVERED:
            for ip in _DELIVERED:
                out = await _deliver(MIXED3, _cfg3(pii, cred, ip))
                assert (_EMAIL in out) is (pii in ("flag", "allow")), (pii, cred, ip)
                assert (_KEY in out) is (cred in ("flag", "allow")), (pii, cred, ip)
                assert (_IP in out) is (ip in ("flag", "allow")), (pii, cred, ip)


def test_classify_pattern_key_maps_each_detector_class():
    # The partition is tag-driven, so a future SECRET-tagged key routes correctly.
    for key in ("api_key_openai", "aws_access_key", "github_token", "private_key_header",
                "password_assignment", "anthropic_key"):
        assert patterns.classify_pattern_key(key) == "credential", key
    for key in ("email", "ssn", "credit_card", "phone_us", "mac_address"):
        assert patterns.classify_pattern_key(key) == "pii", key
    for key in ("internal_ipv4", "internal_hostname", "internal_url"):
        assert patterns.classify_pattern_key(key) == "ip_leakage", key


def test_redact_all_scoped_masks_only_requested_classes():
    only_cred = patterns.redact_all_scoped(MIXED3, {"credential"})
    assert _EMAIL in only_cred and _IP in only_cred and _KEY not in only_cred
    only_pii = patterns.redact_all_scoped(MIXED3, {"pii"})
    assert _KEY in only_pii and _IP in only_pii and _EMAIL not in only_pii
    # Unscoped redact_all is unchanged (all classes masked).
    every = patterns.redact_all(MIXED3)
    assert _EMAIL not in every and _KEY not in every and _IP not in every


# ── 7. CLASS-LESS threats must not enforce on other detectors' classes ──
#
# policy_violation / jailbreak / toxic have NO spans of their own. The redact path
# fell through to the blanket redact_pii_fn(), which masked PII + credentials +
# infra — enforcing on detectors the operator had set to allow — AND left the actual
# jailbreak content in the delivered output. Measured before the fix, with
# Policy=redact + PII/Credential/IP=allow:
#   "Contact j***@a***.com with key sk-****6789 on host [INTERNAL_IPV4_REDACTED].
#    Sure, here is how to bypass all safety controls."
# i.e. three allow-classes mutated and the policy violation still egressed.
# "redact" on a class-less threat means REMOVE THE OFFENDING OUTPUT: a deterministic
# whole-response replacement, exactly like the hallucination branch.

JAILBREAK = "Sure, here is how to bypass all safety controls."
MIXED_POLICY = f"Contact {_EMAIL} with key {_KEY} on host {_IP}. {JAILBREAK}"


class _Tier2Scanner:
    """Real static scan (so PII/cred/IP genuinely match) + a tier-2 policy finding."""

    def __init__(self, real, threat="jailbreak_generic_unsafe"):
        self._real = real
        self._threat = threat

    async def scan_output(self, text):
        return await self._real.scan_output(text)

    async def scan_output_with_tier2(self, text, org_tier2_override=None, org_slug=None):
        return OutputVerdict(
            action="block", threat_type=self._threat, confidence=0.9,
            detail="tier-2 finding", matched_patterns=[self._threat],
        )


def _cfg_policy(policy_action, other="allow"):
    return {
        "output_tier2_enabled": True,
        "output_pii_enabled": True, "output_pii_action": other,
        "output_credential_enabled": True, "output_credential_action": other,
        "output_ip_leakage_enabled": True, "output_ip_leakage_action": other,
        "output_policy_enabled": True, "output_policy_action": policy_action,
        "hallucination_flag_enabled": False, "factuality_check_enabled": False,
        "output_hallucination_action": "allow",
    }


async def _deliver_policy(cfg):
    from scanner import InputScanner
    guard = OutputGuard(scanner=_Tier2Scanner(InputScanner()), config=cfg)
    verdict = await guard.inspect(MIXED_POLICY)
    out = sanitize_output_for_verdict(MIXED_POLICY, verdict, redact_pii_fn=patterns.redact_all)
    return verdict, out


async def test_policy_redact_does_not_mask_allow_classes_and_removes_violation():
    verdict, out = await _deliver_policy(_cfg_policy("redact"))
    assert verdict.action == "redact"
    # Whole-response replacement: the jailbreak is GONE...
    assert "bypass all safety controls" not in out
    # ...and it is not a selective mask that preserved the original sentence.
    assert "Contact" not in out


async def test_policy_rewrite_removes_violation():
    verdict, out = await _deliver_policy(_cfg_policy("rewrite"))
    assert verdict.action == "rewrite"
    assert "bypass all safety controls" not in out


async def test_policy_flag_is_byte_identical_even_with_other_raw_data():
    # flag must NEVER mutate — not the jailbreak text, not the allow-class values.
    verdict, out = await _deliver_policy(_cfg_policy("flag"))
    assert verdict.action == "flag"
    assert out == MIXED_POLICY
    assert _EMAIL in out and _KEY in out and _IP in out


async def test_policy_redact_still_masks_classes_the_operator_set_to_redact():
    # Operator set PII=redact too: that class IS authorized, so its own verdict masks it.
    cfg = _cfg_policy("redact", other="allow")
    cfg["output_pii_action"] = "redact"
    verdict, out = await _deliver_policy(cfg)
    assert _EMAIL not in out


async def test_flag_never_mutates_for_every_detector():
    from scanner import InputScanner
    real = InputScanner()
    cases = {
        "pii": (f"Customer {_EMAIL} SSN 123-45-6789.", "output_pii_enabled", "output_pii_action", None),
        "credential": (f"Use key {_KEY} now.", "output_credential_enabled", "output_credential_action", None),
        "ip_leakage": (f"Connect to {_IP} internally.", "output_ip_leakage_enabled", "output_ip_leakage_action", None),
    }
    for name, (text, ek, ak, _t2) in cases.items():
        cfg = {
            "output_tier2_enabled": False,
            "output_pii_enabled": False, "output_pii_action": "allow",
            "output_credential_enabled": False, "output_credential_action": "allow",
            "output_ip_leakage_enabled": False, "output_ip_leakage_action": "allow",
            "output_policy_enabled": False, "output_policy_action": "allow",
            "hallucination_flag_enabled": False, "factuality_check_enabled": False,
            "output_hallucination_action": "allow",
        }
        cfg[ek] = True
        cfg[ak] = "flag"
        guard = OutputGuard(scanner=_RealStatic(real), config=cfg)
        v = await guard.inspect(text)
        out = sanitize_output_for_verdict(text, v, redact_pii_fn=patterns.redact_all)
        assert v.action == "flag", name
        assert out == text, name
