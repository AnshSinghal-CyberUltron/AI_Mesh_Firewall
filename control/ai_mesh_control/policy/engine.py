"""
Policy engine: evaluate a request context against active policies and rules.
Returns an EvaluationResult with action (allow/block/redact/monitor) and matched rules.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from policy.constants import DEFAULT_REDACTION_PLACEHOLDER
from policy.models import Policy, Rule


@dataclass
class EvaluationResult:
    """Result of policy evaluation."""

    action: str  # 'allow' | 'block' | 'redact' | 'monitor'
    matched_policy_ids: list[int] = field(default_factory=list)
    matched_rule_ids: list[int] = field(default_factory=list)
    matched_policy_codes: list[str] = field(default_factory=list)
    matched_rule_names: list[str] = field(default_factory=list)
    redaction_hints: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


# Action severity for "first match wins" by severity: block > redact > monitor > allow
ACTION_ORDER = {"block": 5, "redact": 4, "rewrite": 3, "model_downgrade": 2, "monitor": 1, "allow": 0}
VALID_POLICY_DOMAINS = {"global", "pipeline", "rag", "mcp"}


def validate_policy_domain(domain: str | None) -> str:
    if domain is None:
        raise ValidationError({"policy_domain": "This field is required."})
    normalized = str(domain).strip().lower()
    if not normalized:
        raise ValidationError({"policy_domain": "This field may not be blank."})
    if normalized not in VALID_POLICY_DOMAINS:
        raise ValidationError({"policy_domain": f"Unsupported policy domain '{domain}'."})
    return normalized


def _get_text_to_check(context: dict[str, Any], field_hint: str) -> str:
    """Return the text from context to evaluate (prompt, response, or both)."""
    prompt = (context.get("prompt") or "") if isinstance(context.get("prompt"), str) else ""
    response = (context.get("response") or "") if isinstance(context.get("response"), str) else ""
    if field_hint == "prompt":
        return prompt
    if field_hint == "response":
        return response
    return f"{prompt}\n{response}"


def _evaluate_rule(rule: Rule, context: dict[str, Any]) -> bool:
    """
    Evaluate a single rule against context. Returns True if the rule matches.
    """
    condition = rule.condition or {}
    field_hint = condition.get("field", "both")
    text = _get_text_to_check(context, field_hint)

    if rule.rule_type == "regex":
        pattern = condition.get("regex") or condition.get("pattern")
        if not pattern:
            return False
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            return False

    if rule.rule_type == "keywords":
        keywords = condition.get("keywords") or condition.get("keywords_list") or []
        if not keywords:
            return False
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords if isinstance(kw, str))

    if rule.rule_type == "pattern":
        pattern = condition.get("pattern") or condition.get("regex")
        if not pattern:
            return False
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            return False

    return False


def evaluate(
    context: dict[str, Any],
    *,
    policies_qs: QuerySet[Policy] | None = None,
    domain: str | None = None,
    tool_name: str | None = None,
) -> EvaluationResult:
    """
    Evaluate context against active policies and rules.

    Context should contain at least one of: prompt, response (strings).
    Optional: user_id, endpoint_id, request_metadata.

    ``domain`` — if set, only policies matching this domain *plus* global
    policies are evaluated.  Valid values: ``"global"``, ``"pipeline"``,
    ``"rag"``, ``"mcp"``.

    ``tool_name`` — if set, rules with a non-empty target_tool are only
    evaluated when target_tool matches.  Rules with empty target_tool
    apply to all tools.

    Returns EvaluationResult with action (allow/block/redact/monitor), matched
    policy/rule ids, and optional redaction_hints for redact action.
    """
    if policies_qs is None:
        policies_qs = Policy.objects.filter(enabled=True).order_by("-priority").prefetch_related("rules")

    if domain is not None:
        normalized_domain = validate_policy_domain(domain)
        policies_qs = policies_qs.filter(policy_domain=normalized_domain)

    result = EvaluationResult(action="allow")
    best_action_rank = -1

    for policy in policies_qs:
        rules = [r for r in policy.rules.all() if r.enabled]
        rules.sort(key=lambda r: (-r.priority, r.id))

        for rule in rules:
            # Tool-level targeting: skip rules bound to a different tool
            rule_target = getattr(rule, "target_tool", "") or ""
            if rule_target and tool_name and rule_target != tool_name:
                continue

            if not _evaluate_rule(rule, context):
                continue

            result.matched_policy_ids.append(policy.id)
            result.matched_rule_ids.append(rule.id)
            result.matched_policy_codes.append(policy.code)
            result.matched_rule_names.append(rule.name)

            rank = ACTION_ORDER.get(rule.action, 0)
            if rank > best_action_rank:
                best_action_rank = rank
                result.action = rule.action
                if rule.action == "redact" and rule.redaction_config:
                    result.redaction_hints.append(
                        {
                            "rule_id": rule.id,
                            "rule_name": rule.name,
                            "config": rule.redaction_config,
                            "condition": rule.condition,
                        }
                    )
                if rule.action == "redact":
                    cfg = _redaction_config_for_rule(rule)
                    if cfg:
                        result.redaction_hints.append(
                            {
                                "rule_id": rule.id,
                                "rule_name": rule.name,
                                "config": cfg,
                            }
                        )

    if result.action == "block" and result.matched_rule_ids:
        result.message = "Request blocked by policy"
    elif result.action == "redact" and result.matched_rule_ids:
        result.message = "Content redacted by policy"
    elif result.action == "monitor" and result.matched_rule_ids:
        result.message = "Request allowed; event recorded for monitoring"

    return result


def _redaction_config_for_rule(rule: Rule) -> dict[str, Any] | None:
    """
    Build a redaction config for a redact rule.
    Priority:
    1) rule.redaction_config (if provided)
    2) synthesize from rule.condition so redact always has something to apply
    """
    cfg = rule.redaction_config or {}
    if cfg:
        return cfg

    cond = rule.condition or {}
    out: dict[str, Any] = {}

    if rule.rule_type == "regex":
        pattern = cond.get("regex") or cond.get("pattern")
        if pattern:
            out["regex"] = pattern
    elif rule.rule_type == "pattern":
        pattern = cond.get("pattern") or cond.get("regex")
        if pattern:
            # Treat pattern rules as regex for redaction substitution.
            out["regex"] = pattern
    elif rule.rule_type == "keywords":
        keywords = cond.get("keywords") or cond.get("keywords_list") or []
        if keywords:
            out["keywords"] = keywords

    if out:
        out.setdefault("replacement", DEFAULT_REDACTION_PLACEHOLDER)
        return out

    return None
