"""
Local policy evaluation engine for the Gateway Data Plane.

This is a Django-free port of backend/policy/engine.py that operates
on the compiled JSON bundle (plain dicts) stored in the PolicySync
in-memory cache.  No database queries, no Django ORM — pure Python
for sub-millisecond enforcement.

Action precedence: block (3) > redact (2) > monitor (1) > allow (0)
"""

import functools
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ai_mesh_shared.mcp_presets import preset_regex, preset_replacement, preset_validator

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


# ── MCP context evaluation (parity with control/ai_mesh_control/policy/engine.py) ──


def _normalize_key(key: Any) -> str:
    if not isinstance(key, str):
        return ""
    return unicodedata.normalize("NFKC", key).casefold()


def _safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _collect_key_values(obj: Any, key: str, *, _depth: int = 0) -> list[str]:
    if _depth > 10 or not key:
        return []
    target = _normalize_key(key)
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _normalize_key(k) == target:
                out.append(v if isinstance(v, str) else _safe_json(v))
            else:
                out.extend(_collect_key_values(v, key, _depth=_depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_key_values(item, key, _depth=_depth + 1))
    return out


def _resolve_matcher_dict(rule: dict[str, Any]) -> dict[str, Any]:
    cond = rule.get("condition") or {}
    raw_dir = str(cond.get("direction") or cond.get("field") or "both").strip().lower()
    direction = {
        "prompt": "input",
        "response": "output",
        "input": "input",
        "output": "output",
        "both": "both",
    }.get(raw_dir, "both")

    scope = str(cond.get("scope") or "entire").strip().lower()
    if scope not in ("entire", "key"):
        scope = "entire"
    key = str(cond.get("key") or "").strip()
    if scope == "key" and not key:
        scope = "entire"

    preset = cond.get("preset")
    regex = None
    keywords = None
    if preset:
        regex = preset_regex(preset)
    elif rule.get("rule_type") == "keywords":
        keywords = cond.get("keywords") or cond.get("keywords_list") or []
    else:
        regex = cond.get("regex") or cond.get("pattern")

    redaction_cfg = rule.get("redaction_config") or {}
    replacement = (
        redaction_cfg.get("replacement")
        or (preset_replacement(preset) if preset else None)
        or DEFAULT_REDACTION_PLACEHOLDER
    )

    return {
        "direction": direction,
        "scope": scope,
        "key": key,
        "regex": regex,
        "keywords": [k for k in (keywords or []) if isinstance(k, str)],
        "preset": preset,
        "replacement": replacement,
    }


def _candidate_texts_mcp(context: dict[str, Any], matcher: dict[str, Any]) -> list[str]:
    prompt = context.get("prompt") if isinstance(context.get("prompt"), str) else ""
    response = context.get("response") if isinstance(context.get("response"), str) else ""
    input_args = context.get("input_args")
    output_data = context.get("output_data")

    direction = matcher["direction"]
    scope = matcher["scope"]
    key = matcher["key"]
    texts: list[str] = []

    want_input = direction in ("input", "both")
    want_output = direction in ("output", "both")

    if want_input:
        if scope == "key":
            texts.extend(_collect_key_values(input_args, key))
        else:
            texts.append(prompt or _safe_json(input_args))
    if want_output:
        if scope == "key":
            texts.extend(_collect_key_values(output_data, key))
        else:
            texts.append(response or _safe_json(output_data))

    return [t for t in texts if t]


def _evaluate_rule_mcp(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    matcher = _resolve_matcher_dict(rule)
    texts = _candidate_texts_mcp(context, matcher)
    if not texts:
        return False

    if matcher["regex"]:
        validator = preset_validator(matcher["preset"]) if matcher["preset"] else None
        try:
            compiled = _compile_regex(matcher["regex"])
        except re.error:
            return False
        for text in texts:
            if validator is None:
                if compiled.search(text):
                    return True
            else:
                for m in compiled.finditer(text):
                    if validator(m.group(0)):
                        return True
        return False

    if matcher["keywords"]:
        for text in texts:
            text_lower = text.lower()
            if any(kw.lower() in text_lower for kw in matcher["keywords"]):
                return True
        return False

    return False


def evaluate_mcp_policies(
    compiled_policies: list[dict[str, Any]],
    context: dict[str, Any],
    tool_name: str | None = None,
) -> EvaluationResult:
    """Evaluate MCP tool-call context against compiled policy bundle entries."""
    result = EvaluationResult()
    best_action_rank = -1

    for entry in compiled_policies:
        policy = entry.get("policy", {})
        rules = entry.get("rules", [])

        for rule in rules:
            rule_target = rule.get("target_tool", "") or ""
            if rule_target and tool_name and rule_target != tool_name:
                continue

            if not _evaluate_rule_mcp(rule, context):
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

            if action == "redact":
                matcher = _resolve_matcher_dict(rule)
                config: dict[str, Any] = {"replacement": matcher["replacement"]}
                if matcher["regex"]:
                    config["regex"] = matcher["regex"]
                if matcher["keywords"]:
                    config["keywords"] = matcher["keywords"]
                if matcher["regex"] or matcher["keywords"]:
                    result.redaction_hints.append({
                        "rule_id": rule.get("id"),
                        "rule_name": rule.get("name"),
                        "direction": matcher["direction"],
                        "scope": matcher["scope"],
                        "key": matcher["key"],
                        "preset": matcher["preset"],
                        "config": config,
                        "condition": rule.get("condition") or {},
                    })

    if result.action == "block" and result.matched_rule_ids:
        blocker_policy = result.matched_policy_names[-1] if result.matched_policy_names else "Unknown"
        blocker_rule = result.matched_rule_names[-1] if result.matched_rule_names else "Unknown"
        result.message = f"Blocked by policy '{blocker_policy}', rule '{blocker_rule}'"
    elif result.action == "redact" and result.matched_rule_ids:
        result.message = "Content redacted by policy"
    elif result.action == "monitor" and result.matched_rule_ids:
        result.message = "Request allowed; event recorded for monitoring"

    return result


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
                    def _mask_card(m):
                        digits = re.sub(r"[^\d]", "", m.group(0))
                        return f"****-****-****-{digits[-4:]}"

                    result = compiled.sub(_mask_card, result)
                elif "d{3}" in normalized and "d{4}" in normalized and "(?" in regex_pattern:
                    def _mask_phone(m):
                        digits = re.sub(r"[^\d]", "", m.group(0))
                        return f"***-***-{digits[-4:]}"

                    result = compiled.sub(_mask_phone, result)
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