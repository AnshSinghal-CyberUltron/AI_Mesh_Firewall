"""
Chat-pipeline enforcement resolution.

Unifies org-policy actions, guard recommendations (Tier-1/Tier-2), and defaults
into a single enforced action using a fixed precedence and severity lattice.

Precedence (highest wins):
  1. Org policy action for the matched category
  2. Guard recommendation
  3. Default action (typically ``allow``)

Action lattice (low → high): allow < monitor/flag < redact < block

``REDACT`` recommendations enforce ``redact`` unless org policy is ``block`` or
redaction is byte-impossible (honesty check — caller supplies ``redaction_possible``).
"""

from __future__ import annotations

from typing import Final

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
