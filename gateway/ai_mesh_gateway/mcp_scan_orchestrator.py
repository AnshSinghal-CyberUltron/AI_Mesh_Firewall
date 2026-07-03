"""MCP two-tier scan orchestrator (Tier-1 policy/presets → conditional Tier-2 Bedrock).

Emits a structured scan trace (Tier-1 + Tier-2 stages) and compliance tags for
SOC workflows. PII/secret detection is regex-based (``patterns`` module) plus the
optional Bedrock Tier-2 judge — there is no Presidio dependency.

Enforcement (``tag`` < ``redact`` < ``block``) is treated as a FLOOR: a ``block``
posture blocks on ANY finding regardless of the matching rule's authored action.
See :func:`_enforce_blocks`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_mesh_shared.mcp_compliance_tags import tags_for_preset_or_entity

from mcp_scan_targets import _safe_json, extract_and_bind
from patterns import (
    _INFRA_NETWORK_KEYS,
    detect_credential_exposure,
    detect_ip_leakage,
    detect_pii,
    detect_secrets,
    get_compliance_tags,
    redact_all,
)
from policy_engine import apply_field_redaction, apply_redaction, evaluate_mcp_policies

LOG = logging.getLogger("gateway.mcp_scan")

_INJECTION_KEYWORDS = (
    "ignore previous instructions",
    "ignore all prior",
    "disregard your instructions",
    "system prompt",
    "jailbreak",
    "do anything now",
)


@dataclass
class McpFinding:
    entity_type: str
    score: float
    start: int
    end: int
    direction: str
    tier: str = "tier1"
    threat_type: str = ""
    detail: str = ""
    # CHG-0074: the concrete detector keys that matched (e.g. ["internal_ipv6",
    # "file_path_unix"]). Lets a downstream enforcement floor tell an
    # ENFORCEABLE network-infra leak (redactable) from a flag-tier file path
    # WITHOUT re-parsing ``detail`` — see mcp_proxy._findings_have_infra_network_leak.
    matched_kinds: list[str] = field(default_factory=list)

    def to_finding_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "score": round(self.score, 4),
            "start": self.start,
            "end": self.end,
            "direction": self.direction,
            "tier": self.tier,
            "threat_type": self.threat_type,
            "detail": self.detail,
            "matched_kinds": list(self.matched_kinds),
        }


@dataclass
class McpScanResult:
    findings: list[McpFinding] = field(default_factory=list)
    compliance_tags: list[str] = field(default_factory=list)
    blocked: bool = False
    # True when a tier had findings under a 'monitor' action (observe-only):
    # the call is allowed + audited as decision='monitor'. block/redact win over
    # monitor (block > redact > monitor), so this only drives the final decision
    # when nothing blocked or redacted.
    monitored: bool = False
    scan_trace: list[dict[str, Any]] = field(default_factory=list)
    # 3b: named response fields masked on the OUTPUT via per-policy RBAC field
    # redaction (redaction_fields). Empty unless a matched policy declared fields
    # AND the posture allowed mutation (not 'monitor'). Surfaced to the audit meta.
    redacted_fields: list[str] = field(default_factory=list)
    # 3b cross-stage: the field names DECLARED by policies matched in THIS scan
    # (both directions), independent of whether they were applied. The INPUT scan
    # surfaces these so the caller can project the same fields out of the RESPONSE
    # (control HTTP-path parity: an input-stage policy match strips output fields).
    policy_redaction_fields: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


def _get_input_scanner():
    try:
        import main as gateway_main

        return getattr(gateway_main, "INPUT_SCANNER", None)
    except Exception:
        return None


def _get_policy_sync():
    try:
        import main as gateway_main

        return getattr(gateway_main, "POLICY_SYNC", None)
    except Exception:
        return None


def _direction_label(scan_direction: str) -> str:
    return "inbound" if scan_direction == "input" else "outbound"


def _tags_for_finding(finding: McpFinding) -> list[str]:
    if finding.threat_type in ("pii", "secret", "ip_leakage"):
        return get_compliance_tags(
            finding.detail.replace("Matched: ", "").split(", ")
            if "Matched:" in finding.detail
            else [finding.entity_type]
        )
    preset_tags = tags_for_preset_or_entity(finding.entity_type)
    if preset_tags:
        return preset_tags
    return tags_for_preset_or_entity(finding.threat_type or "policy_match")


def _merge_tags(existing: list[str], new: list[str]) -> list[str]:
    out = list(existing)
    for t in new:
        if t not in out:
            out.append(t)
    return out


def _effective_control(effective: dict[str, Any], tier: str, scan_direction: str) -> dict[str, Any]:
    key = f"{tier}_{scan_direction}"
    return effective.get(key) or {}


def _org_tier2_allowed(enabled_info: dict[str, Any] | None) -> bool | None:
    if not enabled_info:
        return None
    return enabled_info.get("mcp_tier2_enabled")


def _build_mcp_context(
    text: str,
    *,
    scan_direction: str,
    full_payload: Any,
) -> dict[str, Any]:
    if scan_direction == "input":
        return {
            "prompt": text,
            "response": "",
            "input_args": full_payload if isinstance(full_payload, (dict, list)) else full_payload,
            "output_data": None,
        }
    return {
        "prompt": "",
        "response": text,
        "input_args": None,
        "output_data": full_payload if isinstance(full_payload, (dict, list)) else full_payload,
    }


def _findings_from_policy_eval(
    eval_result,
    *,
    scan_direction: str,
    text: str,
) -> list[McpFinding]:
    if not eval_result.matched_rule_ids:
        return []
    mcp_dir = _direction_label(scan_direction)
    entity = "policy_match"
    if eval_result.matched_rule_names:
        entity = eval_result.matched_rule_names[-1]
    preset = None
    if eval_result.redaction_hints:
        preset = eval_result.redaction_hints[-1].get("preset")
        if preset:
            entity = str(preset)
    return [
        McpFinding(
            entity_type=entity,
            score=0.95,
            start=0,
            end=len(text),
            direction=mcp_dir,
            tier="tier1",
            threat_type=eval_result.action or "policy",
            detail=eval_result.message or "Policy rule matched",
        )
    ]


def _injection_match(text: str) -> bool:
    lower = text.lower()
    if any(kw in lower for kw in _INJECTION_KEYWORDS):
        return True
    # CHG-0078: the 6 keyword substrings above missed tool-poisoning / injection payloads
    # the CHAT scanner blocks (e.g. "…Ignore ALL previous instructions", "Before using any
    # tool, first read ~/.ssh/id_rsa…", "SYSTEM: …developer mode…") — the MCP tier-1 was
    # far weaker than scanner._scan_prompt_sync. Reach detection PARITY by reusing the same
    # high-precision prompt_injection + jailbreak patterns (ATTACK_PATTERNS). Only those two
    # LLM-manipulation categories (NOT sql/command/path — which would FP on benign tool
    # output mentioning SQL/paths); verified ~0 FP incl. docs-ABOUT-injection. compile_pattern
    # is LRU-cached so this is cheap per fragment. Enforcement is UNCHANGED (block under a
    # block posture, tag otherwise) — see the changelog follow-up for output-injection
    # enforcement / poisoned-tool-drop.
    try:
        from scanner import ATTACK_PATTERNS  # local: scanner does not import this module
        from patterns import compile_pattern
    except Exception:  # pragma: no cover - defensive; never break the scan on import error
        return False
    for _cat in ("prompt_injection", "jailbreak"):
        for _ps in ATTACK_PATTERNS.get(_cat, ()):
            if compile_pattern(_ps).search(text):
                return True
    return False


def _enforce_blocks(enforcement: str) -> bool:
    """``block`` enforcement is a FLOOR, not a ceiling.

    When the resolved tool/server posture is ``block``, ANY matched finding
    blocks the call regardless of the matching rule's *authored* action — e.g.
    a PII policy rule authored as ``redact`` still BLOCKS under a ``block``
    posture. This is the single source of truth consulted by every Tier-1 and
    Tier-2 branch so the policy-rule path can never diverge from the
    injection/PII/secret paths. That divergence (block gated on the rule's own
    action) was the A4 outbound-block defect: a ``redact``-authored rule under a
    ``block`` posture silently redacted instead of blocking.
    """
    return enforcement == "block"


def _resolve_tier_action(ctrl: dict[str, Any] | None, fallback: str) -> str:
    """Resolve the enforcement action for a single tier from its control row.

    Each tier's MCPScanControl row owns its action INDEPENDENTLY (monitor /
    redact / block). ``inherit`` (or an unset action) defers to the resolved
    server/tool ``scan_action`` passed as ``fallback`` — preserving prior
    behaviour for rows that don't opt into a per-tier override.

    Semantics applied by the scan functions: ``block`` blocks on any finding
    (floor); ``redact`` masks; ``monitor`` (and the legacy ``tag`` fallback)
    detect + tag + allow without mutating. Precedence across a request is
    block > redact > monitor, and a Tier-1 block short-circuits Tier-2.
    """
    a = ((ctrl or {}).get("action") or "inherit").strip()
    if a in ("inherit", ""):
        return fallback or "monitor"
    return a


def _neutralize_render_leaks(text: str) -> str:
    """CHG-0099: full chat-output-guard parity — neutralize every RENDER-TIME
    reconstruction leak in a single leaf, in the same order as
    ``output_guard.sanitize_output_for_verdict``:
      1. ``neutralize_exfil_channels`` — zero-click auto-render exfil beacons (runs FIRST
         so it sees the raw URL, before any masking hides the payload);
      2. ``neutralize_encoded_pii`` — HTML-entity / percent-encoded runs that DECODE to a
         PII/secret;
      3. ``neutralize_markdown_split_pii`` — a PII/secret whose chars are interleaved with
         inline markdown emphasis / code / HTML markers (``1**2**3-45-6789`` renders as an
         SSN) — the CHG-0096 MCP gap (only #1 was wired, so a markdown-split PII/secret in
         a tool RESULT evaded the raw regexes yet a markdown client reconstructed it).
    Each is a STRICT no-op on benign markdown/URLs."""
    from output_guard import (  # local: avoid import cycle
        neutralize_encoded_pii,
        neutralize_exfil_channels,
        neutralize_markdown_split_pii,
    )
    return neutralize_markdown_split_pii(neutralize_encoded_pii(neutralize_exfil_channels(text)))


def _neutralize_exfil_deep(text: str) -> str:
    """CHG-0097/0099: render-leak neutralization that is robust to JSON serialization.

    The MCP tier-1 scan target is usually the WHOLE result payload JSON-serialized
    (``target_mode="entire"``), so HTML attribute quotes are escaped (``src=\\"...\\"``)
    and the HTML/srcset regexes (which expect real quotes) miss them — the CHG-0096
    residual. If ``text`` is a JSON structure, parse it and neutralize each UNESCAPED
    string leaf (via ``_neutralize_render_leaks`` — exfil beacons + encoded-PII +
    markdown-split PII, CHG-0099), then re-serialize — so HTML/SVG/CSS beacons AND
    markdown-split PII nested in a field are handled too. Non-JSON text (a plain-string
    result) is neutralized directly. Returns the ORIGINAL text unchanged when nothing was
    neutralized (no reformatting churn, so a benign result stays byte-identical)."""
    stripped = text.lstrip()
    if stripped[:1] not in ("{", "["):
        return _neutralize_render_leaks(text)
    import json as _json
    try:
        obj = _json.loads(text)
    except Exception:
        return _neutralize_render_leaks(text)
    changed = [False]

    def _walk(o):
        if isinstance(o, str):
            n = _neutralize_render_leaks(o)
            if n != o:
                changed[0] = True
            return n
        if isinstance(o, list):
            return [_walk(x) for x in o]
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        return o

    obj = _walk(obj)
    return _json.dumps(obj) if changed[0] else text


async def _scan_text_tier1(
    text: str,
    *,
    scan_direction: str,
    enforcement: str,
    full_payload: Any,
    org_slug: str,
    server_slug: str,
    tool_name: str,
    actor: dict[str, Any] | None = None,
) -> tuple[str, list[McpFinding], bool, list[str]]:
    """Run Tier-1 policy + preset evaluation on a single text fragment.

    Returns ``(mutated_text, findings, blocked, redaction_fields)`` where
    ``redaction_fields`` (3b) are the named response fields the matched policy
    declared for RBAC masking — surfaced so ``scan_mcp_payload`` can mask them on
    the structured OUTPUT payload (the injection/PII fallbacks declare none).
    """
    if not text:
        return text, [], False, []

    findings: list[McpFinding] = []
    blocked = False
    mutated = text
    mcp_dir = _direction_label(scan_direction)
    context = _build_mcp_context(text, scan_direction=scan_direction, full_payload=full_payload)

    policy_sync = _get_policy_sync()
    policies: list[dict[str, Any]] = []
    if policy_sync is not None and org_slug and server_slug:
        try:
            policies = policy_sync.get_policies_for_server(org_slug, server_slug, domain="mcp")
        except Exception as exc:
            LOG.warning("MCP policy bundle lookup failed: %s", exc)

    if policies:
        eval_result = evaluate_mcp_policies(
            policies, context, tool_name=tool_name or None, actor=actor
        )
        if eval_result.matched_rule_ids:
            findings.extend(
                _findings_from_policy_eval(eval_result, scan_direction=scan_direction, text=text)
            )
            policy_redacts = eval_result.action == "redact"
            # A matched rule authored action='block' is an EXPLICIT block intent —
            # honor it even under a coarser posture (tag/redact), matching the
            # control-plane engine (engine.py blocks on ``result.action == "block"``)
            # and the backend HTTP path. Without this, the stdio/websocket adapter
            # path — which bypasses the backend that would re-enforce the rule —
            # silently downgrades an actor-scoped block rule to detect-and-tag
            # under the default 'tag' posture (BACKSTOP_FINDINGS G2 item 3, #3).
            # A 'monitor' posture is an explicit observe-only override and wins.
            policy_blocks = eval_result.action == "block"
            if _enforce_blocks(enforcement) or (policy_blocks and enforcement != "monitor"):
                # A4 FIX: block posture is a FLOOR (blocks ANY matched rule, even
                # one authored redact/tag); additionally a rule's own 'block'
                # action is honored under any non-monitor posture (CHG-0007).
                blocked = True
            elif enforcement == "redact" and (policy_redacts or eval_result.redaction_hints):
                mutated = apply_redaction(text, eval_result.redaction_hints)
            return mutated, findings, blocked, list(eval_result.redaction_fields)

    # CHG-0079: deobfuscate INVISIBLE / CONFUSABLE unicode (zero-width, bidi-override,
    # homoglyph, unicode-tag block, combining-mark smuggling) before detection. The chat
    # scanner normalizes via _normalize_unicode before scanning, but the MCP tier-1
    # scanned RAW text — so a zero-width-broken ("I​g​n​o​r​e…") or homoglyph ("Ｉgnore…")
    # injection, or a similarly hidden secret / internal-IP (below), bypassed it. Only
    # NON-ASCII text can carry these characters, so pure-ASCII text (the common case)
    # skips the normalize cost entirely.
    if not text.isascii():
        from scanner import _normalize_unicode  # local: scanner doesn't import this module
        _deob = _normalize_unicode(text)
    else:
        _deob = text

    if _injection_match(text) or (_deob != text and _injection_match(_deob)):
        findings.append(
            McpFinding(
                entity_type="prompt_injection",
                score=0.9,
                start=0,
                end=len(text),
                direction=mcp_dir,
                tier="tier1",
                threat_type="prompt_injection",
                detail="Matched injection keyword pattern",
            )
        )
        if _enforce_blocks(enforcement):
            blocked = True
        elif enforcement == "redact":
            mutated = redact_all(text)
        return mutated, findings, blocked, []

    pii = detect_pii(text)
    secrets = detect_secrets(text)
    # 1.4 "PII/IP/regulated": extend MCP tagging to IP/infrastructure leakage
    # (internal IPs, internal hostnames, internal URLs, private file paths). These
    # patterns + detect_ip_leakage already existed and ran on the chat output_guard
    # path, but the MCP tool-call scan only ran detect_pii/detect_secrets — so an
    # internal host/path in a tool RESULT was never detected/tagged/redacted.
    ip_leak = detect_ip_leakage(text)
    # CHG-0075: also run detect_credential_exposure. CREDENTIAL_EXPOSURE_PATTERNS is a
    # SEPARATE dict (bearer_token, connection_string w/ password, jwt, stripe_key,
    # twilio_api_key, azure_storage_key, gcp_service_account_key, slack_token,
    # github_fine_grained_pat) NOT read by detect_secrets. redact_all masks it, but the
    # MCP tier-1 scan only ran detect_pii/secrets/ip_leak — so a credential whose ONLY
    # match is a CREDENTIAL_EXPOSURE kind (e.g. a Stripe/Twilio/Azure key or a DB
    # connection string's password) was never DETECTED, so it drove no enforcement and
    # egressed RAW on a tool RESULT (and passed unblocked in tool ARGS to an untrusted
    # upstream). Same wrong-dict class as CHG-0071. Folded into the secret bucket.
    cred_exp = detect_credential_exposure(text)
    if pii or secrets or ip_leak or cred_exp:
        kinds = (
            list(pii.keys()) + list(secrets.keys())
            + list(ip_leak.keys()) + list(cred_exp.keys())
        )
        # threat precedence: pii > secret/credential > ip_leakage (a credential
        # exposure is a "secret" for _findings_have_secret_or_pii / _findings_have_
        # credential, so it drives the redact floor AND the arg credential force-block).
        threat = (
            "pii" if pii
            else ("secret" if (secrets or cred_exp) else "ip_leakage")
        )
        findings.append(
            McpFinding(
                entity_type=kinds[0] if kinds else "PII",
                score=0.85,
                start=0,
                end=len(text),
                direction=mcp_dir,
                tier="tier1",
                threat_type=threat,
                detail=f"Matched: {', '.join(kinds)}",
                matched_kinds=kinds,  # CHG-0074: enables the network-infra redact floor
            )
        )
        if _enforce_blocks(enforcement):
            blocked = True
        elif enforcement == "redact":
            candidate = redact_all(text)
            # Egress-byte truth / fail-closed: if ANY detected PII/secret/internal
            # value survives the scrub VERBATIM, do NOT forward a "redacted" result
            # that still carries it — block instead (a redact-that-leaks is the
            # A4-class defect). redact_all masks internal IP/host/URL but NOT private
            # file paths, and a masker bug could leave a detected value un-scrubbed
            # (cf. CHG-0054 private-key body). CHG-0057: byte-verify ALL detected
            # categories, not just ip_leak — the "PII/secret always covered" assumption
            # is now enforced, not assumed. Standard partial-masked PII (email/ssn/card,
            # whose raw form is always altered) is never a substring of the scrub, so
            # this does NOT false-block (verified over the full PII/secret battery).
            _detected_values = (
                list(pii.values()) + list(secrets.values())
                + list(ip_leak.values()) + list(cred_exp.values())  # CHG-0075
            )
            if any(v and str(v) in candidate for v in _detected_values):
                blocked = True
            else:
                mutated = candidate

    # CHG-0076: obfuscation-bypass parity with the chat output scanner (G33/G35).
    # detect_secrets folds base64/hex transport, but a SECRET / CREDENTIAL / INTERNAL
    # NETWORK IP hidden by a TEXT-encoding (HTML char refs &#..;, percent-encoding,
    # \u / \x escapes) dodges the raw regexes above — yet a markdown/HTML MCP client
    # decodes it back to the value, so a malicious upstream can exfil a stolen
    # credential / internal IP past the firewall (or a tenant can smuggle one in ARGS).
    # redact_all CANNOT mask an ENCODED run, so BLOCK (fail-closed) — mirroring the chat
    # INPUT path (scanner._scan_prompt_sync) and the byte-verify block above; a 'monitor'
    # posture stays observe-only. SCOPED to secret/credential/internal-NETWORK-IP (no
    # legit reason to text-encode those); generic PII is EXCLUDED so a scraped HTML page's
    # entity-encoded contact email does not false-block a legitimate web/HTML tool result.
    if not blocked and enforcement != "monitor":
        from scanner import _decode_text_encoding_variants  # local: avoid import cycle
        _variants = list(_decode_text_encoding_variants(text))
        # CHG-0079: also probe the INVISIBLE/CONFUSABLE-unicode-deobfuscated view
        # (zero-width / bidi / homoglyph / unicode-tag smuggling) — a secret / internal
        # IP hidden that way dodges the raw regexes but the model reads it deobfuscated.
        if _deob != text:
            _variants.append(_deob)
        for _variant in _variants:
            if _variant == text:
                continue
            _hidden: dict[str, str] = {}
            _hidden.update(detect_secrets(_variant))
            _hidden.update(detect_credential_exposure(_variant))
            _hidden.update({
                k: v for k, v in detect_ip_leakage(_variant).items()
                if k in _INFRA_NETWORK_KEYS
            })
            # CHG-0083: several CREDENTIALS live in PII_PATTERNS (detect_pii), not
            # SECRET_PATTERNS — aws_access_key (AKIA/ASIA), aws_secret_access_key,
            # api_key_openai, github_token, private_key_header. detect_secrets misses
            # them, so an OBFUSCATED AWS key (HTML-entity / zero-width / homoglyph) slipped
            # past the encoded-exfil block above while its raw form masks. Include decoded
            # detect_pii matches whose compliance tag is SECRET (credentials misfiled as
            # PII) — NOT generic PII (email/phone/ssn/cc: tags GDPR/PII/HIPAA/PCI-DSS, never
            # SECRET), which stays excluded to avoid FP on entity-encoded scraped-HTML PII.
            _hidden.update({
                k: v for k, v in detect_pii(_variant).items()
                if "SECRET" in get_compliance_tags([k])
            })
            if _hidden:
                findings.append(
                    McpFinding(
                        entity_type=next(iter(_hidden)),
                        score=0.9,
                        start=0,
                        end=len(text),
                        direction=mcp_dir,
                        tier="tier1",
                        threat_type="secret",
                        detail=f"Encoded exfil (text-encoding) hides: {', '.join(_hidden)}",
                        matched_kinds=list(_hidden),
                    )
                )
                blocked = True
                break

    # CHG-0096: defang zero-click auto-render EXFIL BEACONS in the tool RESULT — parity
    # with the chat output guard (G40-G43). A malicious upstream tool result can embed a
    # markdown-image ``![x](https://evil/?d=<data>)``, an HTML ``<img src=...>`` / srcset,
    # or a protocol-relative beacon that a markdown/HTML MCP client AUTO-FETCHES on render
    # — a zero-click exfil of arbitrary data the text regexes never recognise as a secret
    # (so nothing above detected/masked it, yet the raw beacon egressed). This egress
    # BYPASSES the chat output guard (a distinct API surface). ``neutralize_exfil_channels``
    # masks the smuggled payload + strips the auto-render (image -> plain link); it is a
    # STRICT no-op on benign markdown/URLs (gated by ``_url_smuggles_data``), so it is safe
    # to run unconditionally under any enforcing posture. Applied to ``mutated`` so it
    # composes on top of any PII/secret redaction above; a 'monitor' posture stays
    # observe-only (matches the encoded-exfil block's gate).
    if not blocked and enforcement != "monitor":
        # Run on the RAW text (not the already-redacted ``mutated``): the beacon's
        # smuggled payload must be VISIBLE for ``_url_smuggles_data`` to trip — if
        # redact_all masked the URL's PII first, the neutralizer would see a masked tail
        # and leave the auto-render intact. If a beacon is defanged, RE-APPLY the
        # PII/secret redaction over the defanged text (when any was detected) so both the
        # beacon AND any other sensitive value are masked. CHG-0097: ``_neutralize_exfil_deep``
        # is JSON-aware so HTML/SVG/CSS/srcset beacons nested in a JSON payload (whose
        # attribute quotes are escaped) are defanged too, not just markdown/bare-URL.
        _neu = _neutralize_exfil_deep(text)
        if _neu != text:
            findings.append(
                McpFinding(
                    entity_type="render_reconstruction",
                    score=0.9,
                    start=0,
                    end=len(text),
                    direction=mcp_dir,
                    tier="tier1",
                    threat_type="exfil",
                    detail=(
                        "Neutralized a render-time reconstruction leak (zero-click exfil "
                        "beacon / encoded-PII / markdown-split PII-secret)"
                    ),
                )
            )
            mutated = redact_all(_neu) if (pii or secrets or ip_leak or cred_exp) else _neu
    return mutated, findings, blocked, []


async def _scan_text_tier2(
    text: str,
    *,
    scan_direction: str,
    enforcement: str,
    scanner,
    org_slug: str,
    org_tier2_override: bool | None,
    org_tier2_strict: bool,
    strict_mode: str,
) -> tuple[str, list[McpFinding], bool, str | None]:
    """Tier-2 Bedrock scan. Returns (text, findings, blocked, fallback_reason)."""
    if scanner is None:
        return text, [], False, "scanner_unavailable"

    mcp_dir = _direction_label(scan_direction)
    try:
        verdict = await scanner.scan_prompt_with_tier2(
            text,
            org_tier2_override=org_tier2_override,
            org_slug=org_slug,
            org_tier2_strict=org_tier2_strict,
        )
    except Exception as exc:
        LOG.warning("MCP Tier-2 scan failed: %s", exc)
        if strict_mode == "strict":
            return text, [], True, "tier2_error_strict"
        return text, [], False, "tier2_error_fail_open"

    findings: list[McpFinding] = []
    if verdict.tier and "tier" in verdict.tier.lower() and verdict.tier != "tier_1":
        if verdict.action in ("block", "redact", "flag"):
            findings.append(
                McpFinding(
                    entity_type=verdict.threat_type or "bedrock",
                    score=verdict.confidence,
                    start=0,
                    end=len(text),
                    direction=mcp_dir,
                    tier="tier2",
                    threat_type=verdict.threat_type,
                    detail=verdict.detail,
                )
            )
    if _enforce_blocks(enforcement) and verdict.action in ("block", "redact", "flag"):
        # A4 FIX (Tier-2 parity): block posture blocks on any actionable Bedrock
        # verdict, not only verdict.action == "block".
        return text, findings, True, None
    if enforcement == "redact" and verdict.action in ("redact", "block"):
        return redact_all(text), findings, False, None
    return text, findings, False, None


async def scan_mcp_payload(
    payload: Any,
    *,
    scan_direction: str,
    enforcement: str,
    effective_controls: dict[str, Any],
    enabled_info: dict[str, Any] | None = None,
    org_slug: str = "",
    server_slug: str = "",
    tool_name: str = "",
    actor: dict[str, Any] | None = None,
    extra_redaction_fields: list[str] | None = None,
) -> tuple[Any, McpScanResult]:
    """Run Tier-1 then conditional Tier-2 on ``payload`` for input or output.

    ``actor`` (M-04): optional {user_id, agent_id, roles} identity used to
    scope actor-allowlisted MCP policies during Tier-1 evaluation.

    ``extra_redaction_fields`` (3b cross-stage): named fields to mask on the
    OUTPUT in addition to any this scan's own policy matches declare. The caller
    threads the INPUT scan's ``policy_redaction_fields`` here so an input-stage
    policy match projects those fields out of the RESPONSE — control HTTP-path
    parity for the RBAC "role X never sees field F" pattern (the rule fires on the
    call, not the response). Ignored on the input direction.
    """
    result = McpScanResult()
    tier1_ctrl = _effective_control(effective_controls, "tier1", scan_direction)
    tier2_ctrl = _effective_control(effective_controls, "tier2", scan_direction)
    # Per-tier action: each tier's control row owns its action; 'inherit'/unset
    # defers to the server/tool action passed as ``enforcement``.
    tier1_action = _resolve_tier_action(tier1_ctrl, enforcement)
    result_redacted = False
    # 3b: accumulate the named response fields that matched actor-scoped policies
    # declared for RBAC masking (deduped, order-preserving). Applied to the
    # structured OUTPUT payload at each non-blocked return via _finalize_output —
    # this closes the "adapter path does content-scan but no field-level RBAC
    # masking" gap (BACKSTOP finding #1); the HTTP path already masks these fields.
    field_redaction_union: list[str] = []

    def _finalize_output(out_payload: Any) -> Any:
        """Mask per-policy ``redaction_fields`` on the OUTPUT payload.

        Parity with the control HTTP path (apply_field_redaction): applies ONLY
        on the output direction, only when a matched policy declared fields (this
        scan's own matches PLUS the caller-threaded ``extra_redaction_fields`` from
        the input stage), and NOT under a 'monitor' posture (observe-only). A
        'block' posture already withheld the payload upstream, so field masking
        never runs on a blocked call. Records the masked field set + a scan-trace
        stage for audit ONLY when a named field was actually present (identity
        no-op otherwise — so a caller never mislabels an unchanged result).
        """
        if scan_direction != "output" or tier1_action == "monitor":
            return out_payload
        mask_fields = list(field_redaction_union)
        for _rf in (extra_redaction_fields or []):
            if isinstance(_rf, str) and _rf and _rf not in mask_fields:
                mask_fields.append(_rf)
        if not mask_fields:
            return out_payload
        masked = apply_field_redaction(out_payload, mask_fields)
        if masked is out_payload:
            return out_payload  # none of the named fields present -> true no-op
        result.redacted_fields = mask_fields
        result.scan_trace.append(
            {
                "scan_stage": "field_redaction",
                "tier": "tier1",
                "direction": scan_direction,
                "fields": list(mask_fields),
                "policy_engine": True,
            }
        )
        return masked

    if not tier1_ctrl.get("enabled", True):
        result.scan_trace.append(
            {
                "scan_stage": "tier1_skipped",
                "tier": "tier1",
                "direction": scan_direction,
                "enabled": False,
            }
        )
        return payload, result

    target_mode = tier1_ctrl.get("target_mode") or "entire"
    key_path = tier1_ctrl.get("key_path") or ""
    state_ref, targets = extract_and_bind(
        payload,
        target_mode=target_mode,
        key_path=key_path,
    )

    scanner = _get_input_scanner()
    tier1_blocked = False

    for text, setter, path_label in targets:
        if not text:
            continue
        new_text, findings, blocked, rfields = await _scan_text_tier1(
            text,
            scan_direction=scan_direction,
            enforcement=tier1_action,
            full_payload=state_ref[0],
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name=tool_name,
            actor=actor,
        )
        for _rf in rfields:
            if _rf not in field_redaction_union:
                field_redaction_union.append(_rf)
        result.findings.extend(findings)
        if findings:
            for f in findings:
                result.compliance_tags = _merge_tags(
                    result.compliance_tags, _tags_for_finding(f)
                )
            if tier1_action == "monitor":
                result.monitored = True
        if blocked:
            tier1_blocked = True
        if new_text != text and tier1_action == "redact":
            # CHG-0047: fail-closed no-op-scrub guard (egress bytes are the only
            # source of truth). Tier-1 produced a redaction (new_text != text);
            # VERIFY the setter actually applied it by comparing the payload bytes
            # before/after. A setter that silently no-ops (e.g. a best-effort
            # mutator on an exotic nested path) would otherwise leave the RAW value
            # in the payload while result_redacted claims a scrub — the CHG-0046
            # class of leak. If the payload did not change, BLOCK rather than egress
            # an un-scrubbed result. Complements CHG-0046 (which made the known
            # non-string setters real) by catching ANY residual no-op scrub.
            _before = _safe_json(state_ref[0])
            setter(new_text)
            result_redacted = True
            if _safe_json(state_ref[0]) == _before:
                tier1_blocked = True
                result.scan_trace.append(
                    {
                        "scan_stage": "noop_scrub_failclosed",
                        "tier": "tier1",
                        "direction": scan_direction,
                        "target_mode": target_mode,
                        "key_path": path_label,
                        "reason": "redaction_setter_noop_raw_survived",
                    }
                )
        result.scan_trace.append(
            {
                "scan_stage": "tier1",
                "tier": "tier1",
                "direction": scan_direction,
                "target_mode": target_mode,
                "key_path": path_label,
                "strict_mode": tier1_ctrl.get("strict_mode"),
                "action": tier1_action,
                "effective_control_id": tier1_ctrl.get("control_id"),
                "finding_count": len(findings),
                "policy_engine": True,
            }
        )

    # 3b cross-stage: surface the field names THIS scan's matched policies
    # declared (both directions) so the caller can project them out of the paired
    # RESPONSE. Set from the this-scan union only (NOT extra_redaction_fields).
    result.policy_redaction_fields = list(field_redaction_union)

    if tier1_blocked:
        result.blocked = True
        return payload, result

    mutable = state_ref[0]

    if not tier2_ctrl.get("enabled", False):
        return _finalize_output(mutable if result_redacted else payload), result

    org_override = _org_tier2_allowed(enabled_info)
    if org_override is False:
        result.scan_trace.append(
            {"scan_stage": "tier2_skipped", "tier": "tier2", "reason": "org_mcp_tier2_disabled"}
        )
        return _finalize_output(mutable if result_redacted else payload), result

    strict_mode = tier2_ctrl.get("strict_mode") or "strict"
    tier2_action = _resolve_tier_action(tier2_ctrl, enforcement)
    org_strict = bool((enabled_info or {}).get("tier2_strict", True))

    for text, setter, path_label in targets:
        if not text:
            continue
        new_text, t2_findings, t2_blocked, fallback = await _scan_text_tier2(
            text,
            scan_direction=scan_direction,
            enforcement=tier2_action,
            scanner=scanner,
            org_slug=org_slug,
            org_tier2_override=org_override,
            org_tier2_strict=org_strict,
            strict_mode=strict_mode,
        )
        result.findings.extend(t2_findings)
        if t2_findings:
            for f in t2_findings:
                result.compliance_tags = _merge_tags(
                    result.compliance_tags, _tags_for_finding(f)
                )
            if tier2_action == "monitor":
                result.monitored = True
        result.scan_trace.append(
            {
                "scan_stage": "tier2",
                "tier": "tier2",
                "direction": scan_direction,
                "target_mode": target_mode,
                "key_path": path_label,
                "strict_mode": strict_mode,
                "action": tier2_action,
                "effective_control_id": tier2_ctrl.get("control_id"),
                "fallback_reason": fallback,
                "finding_count": len(t2_findings),
            }
        )
        if t2_blocked:
            result.blocked = True
            return payload, result
        if fallback and strict_mode == "strict" and "strict" in fallback:
            result.blocked = True
            return payload, result
        if new_text != text and tier2_action == "redact":
            setter(new_text)
            result_redacted = True

    out = _finalize_output(state_ref[0] if result_redacted else payload)
    return out, result
