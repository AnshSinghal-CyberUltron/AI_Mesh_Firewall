"""
Local policy evaluation engine for the Gateway Data Plane.

This is a Django-free port of backend/policy/engine.py that operates
on the compiled JSON bundle (plain dicts) stored in the PolicySync
in-memory cache.  No database queries, no Django ORM — pure Python
for sub-millisecond enforcement.

Action precedence: block (3) > redact (2) > monitor (1) > allow (0)
"""

import functools
import logging
import re
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("gateway.policy_engine")

ACTION_ORDER: dict[str, int] = {
    "block": 5,
    "redact": 4,
    "rewrite": 3,
    "model_downgrade": 2,
    "monitor": 1,
    "allow": 0,
}
DEFAULT_REDACTION_PLACEHOLDER = "[REDACTED]"


@functools.lru_cache(maxsize=256)
def _compile_regex(pattern: str) -> re.Pattern:
    """Compile and cache regex patterns for hot-path performance."""
    return re.compile(pattern, re.IGNORECASE)

@dataclass
class EvaluationResult:
    """Result of local policy evaluation."""

    action: str = "allow"
    matched_policy_ids: list[int] = field(default_factory=list)
    matched_rule_ids: list[int] = field(default_factory=list)
    matched_policy_codes: list[str] = field(default_factory=list)
    matched_rule_names: list[str] = field(default_factory=list)
    matched_policy_names: list[str] = field(default_factory=list)
    matched_policy_severities: list[str] = field(default_factory=list)
    matched_policy_categories: list[str] = field(default_factory=list)
    matched_rule_descriptions: list[str] = field(default_factory=list)
    redaction_hints: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

def _get_text_to_check(
    prompt: str,
    response_text: str,
    field_hint: str,
) -> str:
    """Return the text to evaluate based on the field hint."""
    if field_hint == "prompt":
        return prompt
    if field_hint == "response":
        return response_text
    return f"{prompt}\n{response_text}"

def _evaluate_rule(
    rule: dict[str, Any],
    prompt: str,
    response_text: str,
) -> bool:
    """
    Evaluate a single rule (dict from compiled bundle) against text.
    Returns True if the rule matches.
    """
    condition = rule.get("condition", {})
    field_hint = condition.get("field", "both")
    text = _get_text_to_check(prompt, response_text, field_hint)

    rule_type = rule.get("rule_type", "")

    if rule_type in {"regex", "pattern"}:
        pattern = condition.get("regex") or condition.get("pattern")
        if not pattern:
            return False
        try:
            return bool(_compile_regex(pattern).search(text))
        except re.error:
            return False
    
    if rule_type == "keywords":
        keywords = condition.get("keywords") or condition.get("keywords_list") or []
        if not keywords:
            return False
        text_lower = text.lower()
        return any(
            kw.lower() in text_lower
            for kw in keywords
            if isinstance(kw, str)
        )
    return False

def evaluate(
    prompt: str,
    response_text: str,
    compiled_policies: list[dict[str, Any]],
    tool_name: str | None = None,
) -> EvaluationResult:
    """
    Evaluate prompt/response against a list of compiled policy entries.

    Each entry in compiled_policies has the shape:
        {
            "policy": {"id": int, "code": str, "priority": int, ...},
            "rules": [{"id": int, "rule_type": str, "condition": dict, "action": str, ...}]
        }

    ``tool_name`` — if provided, rules with a non-empty target_tool are only
    evaluated when target_tool matches.  Rules with empty target_tool apply
    to all tools.

    Returns an EvaluationResult with the highest-severity action
    among all matching rules.
    """
    result = EvaluationResult()
    best_action_rank = -1

    for entry in compiled_policies:
        policy = entry.get("policy", {})
        rules = entry.get("rules", [])

        for rule in rules:
            # Tool-level targeting: skip rules bound to a different tool
            rule_target = rule.get("target_tool", "") or ""
            if rule_target and tool_name and rule_target != tool_name:
                continue

            if not _evaluate_rule(rule, prompt, response_text):
                continue
            
            result.matched_policy_ids.append(policy.get("id"))
            result.matched_rule_ids.append(rule.get("id"))
            result.matched_policy_codes.append(policy.get("code", ""))
            result.matched_rule_names.append(rule.get("name", ""))
            result.matched_policy_names.append(policy.get("name", ""))
            result.matched_policy_severities.append(policy.get("severity", ""))
            result.matched_policy_categories.append(policy.get("category", ""))
            result.matched_rule_descriptions.append(rule.get("description", ""))

            action = rule.get("action", "monitor")
            rank = ACTION_ORDER.get(action, 0)
            if rank > best_action_rank:
                best_action_rank = rank
                result.action = action
                if action == "redact" and rule.get("redaction_config"):
                    result.redaction_hints.append({
                        "rule_id": rule.get("id"),
                        "rule_name": rule.get("name"),
                        "config": rule.get("redaction_config"),
                        "condition": rule.get("condition") or {},
                    })

    if result.action == "block" and result.matched_rule_ids:
        blocker_policy = result.matched_policy_names[-1] if result.matched_policy_names else "Unknown"
        blocker_rule = result.matched_rule_names[-1] if result.matched_rule_names else "Unknown"
        result.message = f"Blocked by policy '{blocker_policy}', rule '{blocker_rule}'"
    elif result.action == "redact" and result.matched_rule_ids:
        result.message = "Content redacted by policy"
    elif result.action == "rewrite" and result.matched_rule_ids:
        result.message = "Content rewritten by policy"
    elif result.action == "model_downgrade" and result.matched_rule_ids:
        result.message = "Model downgraded by policy"
    elif result.action == "monitor" and result.matched_rule_ids:
        result.message = "Request allowed; event recorded for monitoring"

    return result


def evaluate_for_stage(
    prompt: str,
    response_text: str,
    compiled_policies: list[dict[str, Any]],
    stage: str,
) -> EvaluationResult:
    """Evaluate only rules that apply to a specific pipeline stage.

    Rules with an empty pipeline_stage apply to all stages.
    Rules with a specific pipeline_stage only apply to that stage.
    """
    filtered_policies = []
    for entry in compiled_policies:
        policy = entry.get("policy", {})
        rules = entry.get("rules", [])
        stage_rules = [
            r for r in rules
            if not r.get("pipeline_stage") or r.get("pipeline_stage") == stage
        ]
        if stage_rules:
            filtered_policies.append({"policy": policy, "rules": stage_rules})
    return evaluate(prompt, response_text, filtered_policies)


def apply_redaction(
    text: str,
    redaction_hints: list[dict[str, Any]],
    placeholder: str = DEFAULT_REDACTION_PLACEHOLDER,
) -> str:
    """
    Apply redaction hints to text.  Each hint has a 'config' dict
    which may contain 'regex', 'keywords', and 'replacement'.
    """
    if not text or not redaction_hints:
        return text

    result = text
    for hint in redaction_hints:
        config = hint.get("config") or {}
        condition = hint.get("condition") or {}
        repl = config.get("replacement") or placeholder

        regex_pattern = (
            config.get("regex")
            or config.get("pattern")
            or condition.get("regex")
            or condition.get("pattern")
        )
        if regex_pattern:
            try:
                compiled = _compile_regex(regex_pattern)
                normalized = regex_pattern.replace("\\", "")
                if "@" in normalized and "[A-Za-z" in regex_pattern:
                    result = compiled.sub(lambda m: f"{m.group(0).split('@', 1)[0][:1] or '*'}***@{m.group(0).split('@', 1)[1].split('.', 1)[0][:1] or '*'}***.{m.group(0).rsplit('.', 1)[-1]}", result)
                elif "d{3}-d{2}-d{4}" in normalized:
                    result = compiled.sub(lambda m: f"***-**-{m.group(0)[-4:]}", result)
                elif "d{4}[s-]?d{4}[s-]?d{4}[s-]?d{4}" in normalized:
                    result = compiled.sub(lambda m: f"****-****-****-{re.sub(r'[^\\d]', '', m.group(0))[-4:]}", result)
                elif "d{3}" in normalized and "d{4}" in normalized and "(?" in regex_pattern:
                    result = compiled.sub(lambda m: f"***-***-{re.sub(r'[^\\d]', '', m.group(0))[-4:]}", result)
                else:
                    result = compiled.sub(repl, result)
            except re.error:
                continue

        keywords = (
            config.get("keywords")
            or config.get("keywords_list")
            or condition.get("keywords")
            or condition.get("keywords_list")
            or []
        )
        for kw in keywords:
            if not isinstance(kw, str):
                continue
            pattern = rf"\b{re.escape(kw)}\b"
            result = _compile_regex(pattern).sub(repl, result)

    return result