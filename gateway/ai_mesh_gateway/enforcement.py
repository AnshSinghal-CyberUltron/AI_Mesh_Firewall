"""
Chat-pipeline enforcement resolution — the ONE enforcement authority.

Unifies org-policy actions, guard recommendations (Tier-1/Tier-2), and defaults
into a single enforced action using a fixed precedence and severity lattice.

Precedence (highest wins):
  1. Org policy action for the matched category
  2. Guard recommendation
  3. Default action (typically ``allow``)

Action lattice (low → high): allow < monitor/flag < redact < block

``REDACT`` recommendations enforce ``redact`` unless org policy is ``block`` or
redaction is byte-impossible (honesty check — caller supplies ``redaction_possible``).

Two canonical entry points (PIPELINE-0004):
  - ``resolve_and_enforce()`` — input-side enforcement (Stage 3)
  - ``enforce_output()`` — output-side enforcement (Stage 6)
Both return a frozen ``PipelineDecision``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Final

LOG = logging.getLogger(__name__)

# Severity rank — higher number wins when merging two actions.
ACTION_RANK: Final[dict[str, int]] = {
    "allow": 0,
    "monitor": 1,
    "flag": 1,
    "redact": 2,
    "rewrite": 2,
    "model_downgrade": 2,
    "block": 3,
}

_ALIASES: Final[dict[str, str]] = {
    "block_immediately": "block",
    "block_and_alert": "block",
    "deny": "block",
    "reject": "block",
}


def normalize_action(action: str | None, *, default: str = "allow") -> str:
    """Normalize external action strings to the gateway enforcement vocabulary."""
    if not action or not isinstance(action, str):
        return default
    key = action.strip().lower()
    return _ALIASES.get(key, key)


def action_rank(action: str | None) -> int:
    """Return the lattice rank for *action* (unknown → allow)."""
    return ACTION_RANK.get(normalize_action(action), 0)


def max_action(*actions: str | None, default: str = "allow") -> str:
    """Return the highest-severity action among *actions*."""
    best = normalize_action(default)
    best_rank = action_rank(best)
    for act in actions:
        norm = normalize_action(act)
        rank = action_rank(norm)
        if rank > best_rank:
            best = norm
            best_rank = rank
    return best


def resolve_enforcement(
    recommendation: str | None,
    *,
    org_policy_action: str | None = None,
    enforcement_mode: str = "block",
    redaction_possible: bool = True,
    default_action: str = "allow",
) -> str:
    """
    Resolve the enforced pipeline action for an input/output guard verdict.

    Parameters
    ----------
    recommendation:
        Guard recommendation (e.g. Tier-2 ``recommended_action`` or scanner
        ``verdict.action``).
    org_policy_action:
        Winning org-policy action for the matched category (from policy engine).
    enforcement_mode:
        Org enforcement posture — ``block`` applies blocks; anything else
        downgrades a resolved ``block`` to ``monitor`` (observe-only).
    redaction_possible:
        False when byte-verification proves redaction cannot change egress bytes
        (honesty check at main.py:1566 / :6315). Forces ``block`` when the
        resolved action would otherwise be ``redact``.
    default_action:
        Fallback when neither policy nor recommendation supplies an action.

    Returns
    -------
    str
        One of ``allow``, ``monitor``, ``flag``, ``redact``, ``block``.
    """
    rec = normalize_action(recommendation, default=default_action)
    policy = normalize_action(org_policy_action) if org_policy_action else None
    default = normalize_action(default_action)

    # Precedence: org policy > recommendation > default — take the max of the
    # available layers (policy layer omitted when None).
    candidates: list[str] = [rec, default]
    if policy:
        candidates.insert(0, policy)
    resolved = max_action(*candidates, default=default)

    # REDACT contract: recommendation/policy redact must not become block
    # unless org policy is block OR redaction is impossible.
    if resolved == "redact" and not redaction_possible:
        resolved = "block"
    elif (
        rec == "redact"
        and policy != "block"
        and resolved == "block"
        and action_rank(rec) < action_rank("block")
    ):
        # Guard said redact; only escalate to block via org policy or no-op scrub.
        resolved = "redact"

    # Monitor posture: never hard-block when org enforcement_mode is not block.
    if resolved == "block" and str(enforcement_mode or "").strip().lower() != "block":
        resolved = "monitor"

    return resolved


def should_apply_redaction(resolved_action: str, threat_type: str | None = None) -> bool:
    """True when the resolved action requires deterministic PII/secret masking."""
    act = normalize_action(resolved_action)
    if act == "redact":
        return True
    if act in ("flag", "monitor") and threat_type in ("pii", "secret", "phi", "pci"):
        return True
    return False


def should_hard_block(resolved_action: str, enforcement_mode: str = "block") -> bool:
    """True when the resolved action should terminate the request with HTTP 403."""
    return (
        normalize_action(resolved_action) == "block"
        and str(enforcement_mode or "").strip().lower() == "block"
    )


# ---------------------------------------------------------------------------
# Canonical pipeline types (PIPELINE-0004)
# ---------------------------------------------------------------------------

_REDACTABLE_PII_THREAT_TYPES: Final[frozenset[str]] = frozenset(
    {"pii", "phi", "pci", "secret"}
)

_INJECTION_THREAT_TYPES: Final[frozenset[str]] = frozenset(
    {"prompt_injection", "jailbreak", "goal_hijacking"}
)


@dataclass(frozen=True)
class PipelineDecision:
    """Immutable terminal enforcement outcome for one pipeline stage."""

    action: str
    blocked_by: str | None = None
    threat_type: str | None = None
    detection_tier: str | None = None
    matched_rules: list[str] = field(default_factory=list)
    matched_policy_names: list[str] = field(default_factory=list)
    confidence: float | None = None
    degraded: bool = False
    redaction_applied: bool = False
    scan_text: str | None = None
    stage_latency_ms: int = 0

    @property
    def is_terminal_block(self) -> bool:
        return self.action == "block"

    @property
    def is_redact(self) -> bool:
        return self.action == "redact"

    def as_dict(self) -> dict:
        """Serializable summary for zeroshield metadata / pipeline trace."""
        d: dict = {"action": self.action}
        if self.blocked_by:
            d["blocked_by"] = self.blocked_by
        if self.threat_type:
            d["threat_type"] = self.threat_type
        if self.detection_tier:
            d["detection_tier"] = self.detection_tier
        if self.matched_rules:
            d["matched_rules"] = self.matched_rules
        if self.matched_policy_names:
            d["matched_policy_names"] = self.matched_policy_names
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.degraded:
            d["degraded"] = True
        if self.redaction_applied:
            d["redaction_applied"] = True
        if self.stage_latency_ms:
            d["latency_ms"] = self.stage_latency_ms
        return d


def resolve_and_enforce(
    *,
    scanner_recommendation: str | None = None,
    scanner_action: str | None = None,
    scanner_threat_type: str | None = None,
    scanner_confidence: float | None = None,
    scanner_tier: str | None = None,
    scanner_matched_patterns: list | None = None,
    scanner_detail: str | None = None,
    org_policy_action: str | None = None,
    matched_rules: list[str] | None = None,
    matched_policy_names: list[str] | None = None,
    enforcement_mode: str = "block",
    tier2_degraded: bool = False,
    tier1_pii_detected: bool = False,
    redaction_possible: bool = True,
    pii_detection_enabled: bool = True,
    scan_block_on_injection: bool = True,
    injection_threshold: float = 0.80,
) -> PipelineDecision:
    """
    Single entry point for **input-side** enforcement resolution (Stage 3).

    Merges the scanner recommendation, org policy action, and enforcement mode
    through the existing ``resolve_enforcement`` precedence lattice. Applies
    the fail-closed contract for degraded scanners and PII/injection gating.

    Returns a frozen ``PipelineDecision``. The caller short-circuits on
    ``decision.is_terminal_block`` and applies redaction on ``decision.is_redact``.

    **Fail-closed contract (PIPELINE-0006):**
    - ``tier2_degraded`` + Tier-1 detected PII/secret → ``redact``
    - ``tier2_degraded`` + ``tier1_pii_detected`` (pattern detectors found
      PII/secrets/credentials in the scan text) → ``redact``
    - ``tier2_degraded`` + Tier-1 clean → ``monitor`` (degraded=True)
    - An exception inside → ``PipelineDecision(action="block")``
    """
    t0 = time.perf_counter()
    try:
        threat = scanner_threat_type
        guard_rec = scanner_recommendation or scanner_action or "allow"

        is_injection = threat in _INJECTION_THREAT_TYPES
        if is_injection and normalize_action(guard_rec) == "block":
            if (
                not scan_block_on_injection
                or (scanner_confidence or 0) < injection_threshold
            ):
                guard_rec = "monitor"
        elif (
            normalize_action(guard_rec) == "block"
            and threat in _REDACTABLE_PII_THREAT_TYPES
        ):
            guard_rec = "redact"

        resolved = resolve_enforcement(
            guard_rec,
            org_policy_action=org_policy_action,
            enforcement_mode=enforcement_mode,
            redaction_possible=redaction_possible,
        )

        is_block = should_hard_block(resolved, enforcement_mode)

        _threat_is_secret = threat == "secret"
        _threat_is_pii_class = threat in _REDACTABLE_PII_THREAT_TYPES
        _redact_eligible = _threat_is_secret or (pii_detection_enabled and _threat_is_pii_class)

        should_redact = should_apply_redaction(resolved, threat) and _redact_eligible

        if is_block:
            action = "block"
            blocked_by = "input_scan"
        elif should_redact:
            action = "redact"
            blocked_by = None
        elif not _redact_eligible and resolved == "redact":
            action = "allow"
            blocked_by = None
        elif tier2_degraded and (_threat_is_pii_class or tier1_pii_detected):
            action = "redact" if redaction_possible else "block"
            blocked_by = None if redaction_possible else "input_scan"
        elif tier2_degraded:
            action = resolved if resolved != "allow" else "monitor"
            blocked_by = None
        else:
            action = resolved
            blocked_by = None

        elapsed = int((time.perf_counter() - t0) * 1000)
        return PipelineDecision(
            action=action,
            blocked_by=blocked_by,
            threat_type=threat,
            detection_tier=scanner_tier,
            matched_rules=list(matched_rules or []),
            matched_policy_names=list(matched_policy_names or []),
            confidence=scanner_confidence,
            degraded=tier2_degraded,
            redaction_applied=False,
            stage_latency_ms=elapsed,
        )
    except Exception:
        LOG.exception("resolve_and_enforce failed; failing CLOSED (block)")
        elapsed = int((time.perf_counter() - t0) * 1000)
        return PipelineDecision(
            action="block",
            blocked_by="enforcement_error",
            degraded=tier2_degraded,
            stage_latency_ms=elapsed,
        )


def enforce_output(
    *,
    verdict_action: str | None = None,
    verdict_threat_type: str | None = None,
    verdict_confidence: float | None = None,
    verdict_detail: str | None = None,
    verdict_matched_patterns: list | None = None,
    verdict_compliance_tags: list | None = None,
    scan_degraded: bool = False,
    enforcement_mode: str = "block",
    is_streaming: bool = False,
    exception: Exception | None = None,
) -> PipelineDecision:
    """
    Single entry point for **output-side** enforcement resolution (Stage 6).

    Called by:
      - ``_apply_output_guard_nonstream`` (Paths B/D)
      - ``SecureStreamingResponse._flush_buffer`` (Paths A/C)

    **Fail-closed contract:**
    - ``exception`` (guard crash/timeout) → ``block`` (fixes D-05)
    - ``scan_degraded`` → ``redact`` (not raw pass-through)
    - ``rewrite`` + streaming → coerced to ``block``
    - ``flag`` + ``enforcement_mode=="block"`` → ``block`` (harmonizes D-18)
    """
    t0 = time.perf_counter()
    try:
        if exception is not None:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return PipelineDecision(
                action="block",
                blocked_by="output_guard",
                threat_type="guard_exception",
                degraded=True,
                stage_latency_ms=elapsed,
            )

        if scan_degraded:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return PipelineDecision(
                action="redact",
                blocked_by=None,
                threat_type=verdict_threat_type,
                detection_tier="output_guard",
                degraded=True,
                stage_latency_ms=elapsed,
            )

        act = normalize_action(verdict_action)

        if act == "rewrite" and is_streaming:
            act = "block"
        elif act == "flag" and str(enforcement_mode or "").strip().lower() == "block":
            act = "block"

        blocked_by = "output_guard" if act == "block" else None

        elapsed = int((time.perf_counter() - t0) * 1000)
        return PipelineDecision(
            action=act,
            blocked_by=blocked_by,
            threat_type=verdict_threat_type,
            detection_tier="output_guard",
            confidence=verdict_confidence,
            stage_latency_ms=elapsed,
        )
    except Exception:
        LOG.exception("enforce_output failed; failing CLOSED (block)")
        elapsed = int((time.perf_counter() - t0) * 1000)
        return PipelineDecision(
            action="block",
            blocked_by="output_guard",
            threat_type="enforcement_error",
            degraded=True,
            stage_latency_ms=elapsed,
        )
