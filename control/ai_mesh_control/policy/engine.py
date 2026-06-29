"""
Policy engine: evaluate a request context against active policies and rules.
Returns an EvaluationResult with action (allow/block/redact/monitor) and matched rules.
"""

import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from policy.constants import DEFAULT_REDACTION_PLACEHOLDER
from policy.mcp_presets import preset_regex, preset_replacement, preset_validator
from policy.models import Policy, Rule

logger = logging.getLogger(__name__)

# M-23: bound the cost of compiling an attacker-supplied regex. Patterns far
# longer than any legitimate rule are a ReDoS smell; we skip + warn rather
# than feed them to re.compile on the hot path.
_MAX_REGEX_LEN = 1000

# FINDING-3 (ReDoS): even with the write-time heuristic, defend the hot path
# with a wall-clock budget. Python's backtracking engine has no step/time
# limit, so a catastrophic pattern that slips past validation (e.g. a legacy /
# system-seeded rule, or a shape the heuristic doesn't cover) would otherwise
# pin the single sync executor thread for minutes. We run each regex match in a
# daemon worker thread and join with a deadline: if it overruns, the request
# thread returns "no match" and is freed (the doomed worker thread continues in
# the background but no longer holds up the API). Conservative + dependency-free.
_REGEX_MATCH_TIMEOUT_S = 1.0
# Belt-and-suspenders: also cap the input length fed to the engine. Backtracking
# blow-up scales with input size, so a hard cap bounds worst-case cost even when
# the thread-join races.
_MAX_MATCH_INPUT_LEN = 100_000


def _run_with_timeout(fn, timeout):
    """Run ``fn()`` in a daemon thread, returning its result or None on timeout.

    Frees the caller after ``timeout`` seconds even if ``fn`` (a backtracking
    regex) is still running — the worker is a daemon, so it can't block process
    exit. ``None`` is returned both on timeout and on any exception raised by
    ``fn`` (callers treat that as "no match", fail-open like the pre-existing
    re.error handling).
    """
    box: dict[str, Any] = {}

    def _target():
        try:
            box["result"] = fn()
        except Exception:  # noqa: BLE001 — mirror existing fail-open behaviour
            box["error"] = True

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return None
    if box.get("error"):
        return None
    return box.get("result")


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
VALID_POLICY_DOMAINS = {"pipeline", "rag", "mcp"}


def validate_policy_domain(domain: str | None) -> str:
    if domain is None:
        raise ValidationError({"policy_domain": "This field is required."})
    normalized = str(domain).strip().lower()
    if not normalized:
        raise ValidationError({"policy_domain": "This field may not be blank."})
    if normalized not in VALID_POLICY_DOMAINS:
        raise ValidationError({"policy_domain": f"Unsupported policy domain '{domain}'."})
    return normalized


def _policy_applies_to_actor(policy: Policy, context: dict[str, Any]) -> bool:
    """M-04: scope a policy by the request's actor (user / agent / role).

    Each allowlist on the policy is independent and additive:
      * empty list  -> wildcard (this dimension does not constrain anyone);
      * non-empty    -> strict allowlist for that dimension.

    The request's actor is read from ``context`` (all optional):
      * ``user_id``  matches ``allowed_user_ids``  (ints, str-coerced fallback);
      * ``agent_id`` matches ``allowed_agent_ids`` (string / API-key prefix);
      * ``roles``    (iterable) must overlap ``allowed_roles``.

    Each dimension constrains ONLY on a positive mismatch: the policy is
    skipped when the actor's identity for that dimension is KNOWN and not in
    the allowlist. An unknown dimension (missing key, None, empty value) leaves
    the policy applied — fail-closed, since user/role identity is supplied by
    the caller and skip-on-missing would allow bypass by omission. A context
    with no actor keys therefore behaves exactly as before M-04 (policy
    applies). Missing allowlist attributes (older model rows) also mean
    "applies".
    """
    allowed_user_ids = getattr(policy, "allowed_user_ids", None) or []
    allowed_agent_ids = getattr(policy, "allowed_agent_ids", None) or []
    allowed_roles = getattr(policy, "allowed_roles", None) or []

    if allowed_user_ids:
        user_id = context.get("user_id")
        if user_id is not None and (
            user_id not in allowed_user_ids
            and str(user_id) not in {str(u) for u in allowed_user_ids}
        ):
            return False

    if allowed_agent_ids:
        agent_id = context.get("agent_id")
        if agent_id and str(agent_id) not in {str(a) for a in allowed_agent_ids}:
            return False

    if allowed_roles:
        raw_roles = context.get("roles") or context.get("actor_roles") or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        actor_roles = {str(r) for r in raw_roles if r}
        if actor_roles and not actor_roles.intersection({str(r) for r in allowed_roles}):
            return False

    return True


def _normalize_key(key: Any) -> str:
    """NFKC + casefold a dict key for homoglyph-safe matching."""
    if not isinstance(key, str):
        return ""
    return unicodedata.normalize("NFKC", key).lower()


def _safe_json(value: Any) -> str:
    """Serialize a structured value to text for 'entire' scope matching."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _collect_key_values(obj: Any, key: str, *, _depth: int = 0) -> list[str]:
    """Recursively collect string-ified values stored under ``key``.

    Walks dicts/lists; key match is NFKC + case-insensitive. Bounded depth
    guards against pathological nesting. Used for scope='key' rules so an
    operator can target a single argument/response field by name.
    """
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


def _resolve_matcher(rule: Rule) -> dict[str, Any]:
    """Resolve a rule's condition into a normalized matcher descriptor.

    Backward compatible: legacy rules (no direction/scope/preset keys) fall
    back to the previous prompt/response + regex/keywords behaviour.

    Returns keys: direction (input|output|both), scope (entire|key), key,
    regex, keywords, preset, replacement.
    """
    cond = rule.condition or {}

    # Direction: new 'direction' (input/output/both) or legacy 'field'
    # (prompt/response/both). prompt->input, response->output.
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
        # No key name given -> degrade gracefully to whole-payload scan.
        scope = "entire"

    preset = cond.get("preset")
    regex = None
    keywords = None
    if preset:
        regex = preset_regex(preset)
    elif rule.rule_type == "keywords":
        keywords = cond.get("keywords") or cond.get("keywords_list") or []
    else:  # regex / pattern
        regex = cond.get("regex") or cond.get("pattern")

    redaction_cfg = rule.redaction_config or {}
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


def _candidate_texts(context: dict[str, Any], matcher: dict[str, Any]) -> list[str]:
    """Build the list of text fragments a rule should be matched against,
    honouring direction (input/output/both) and scope (entire/key)."""
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


def _evaluate_rule(rule: Rule, context: dict[str, Any]) -> bool:
    """Evaluate a single rule against context. Returns True if it matches."""
    matcher = _resolve_matcher(rule)
    texts = _candidate_texts(context, matcher)
    if not texts:
        return False

    if matcher["regex"]:
        validator = preset_validator(matcher["preset"]) if matcher["preset"] else None
        # M-23: previously a bad/oversized regex was swallowed silently — the
        # rule just never matched, masking a misconfiguration. Now we log a
        # warning so operators can see a rule's pattern failed to compile, and
        # we skip pathologically long patterns (cheap ReDoS guard) before
        # handing them to re.compile.
        pattern = matcher["regex"]
        if isinstance(pattern, str) and len(pattern) > _MAX_REGEX_LEN:
            logger.warning(
                "Rule id=%s regex skipped: pattern length %d exceeds limit %d (possible ReDoS)",
                getattr(rule, "id", "?"), len(pattern), _MAX_REGEX_LEN,
            )
            return False
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            logger.warning(
                "Rule id=%s has an invalid regex and will never match: %s",
                getattr(rule, "id", "?"), exc,
            )
            return False
        for text in texts:
            # FINDING-3: cap input length and run the match under a wall-clock
            # deadline so a catastrophic-backtracking pattern can't pin this
            # thread. On timeout we treat the rule as non-matching and free the
            # caller (the runaway worker thread is a daemon).
            if len(text) > _MAX_MATCH_INPUT_LEN:
                text = text[:_MAX_MATCH_INPUT_LEN]
            if validator is None:
                matched = _run_with_timeout(
                    lambda _c=compiled, _t=text: _c.search(_t) is not None,
                    _REGEX_MATCH_TIMEOUT_S,
                )
                if matched is None:
                    logger.warning(
                        "Rule id=%s regex match exceeded %.1fs budget and was "
                        "abandoned (possible ReDoS)",
                        getattr(rule, "id", "?"), _REGEX_MATCH_TIMEOUT_S,
                    )
                    continue
                if matched:
                    return True
            else:
                # Validator-gated preset (e.g. Luhn): a regex hit only counts
                # if at least one candidate match also passes the validator.
                def _scan(_c=compiled, _t=text, _v=validator):
                    for m in _c.finditer(_t):
                        if _v(m.group(0)):
                            return True
                    return False

                matched = _run_with_timeout(_scan, _REGEX_MATCH_TIMEOUT_S)
                if matched is None:
                    logger.warning(
                        "Rule id=%s validator-gated regex match exceeded %.1fs "
                        "budget and was abandoned (possible ReDoS)",
                        getattr(rule, "id", "?"), _REGEX_MATCH_TIMEOUT_S,
                    )
                    continue
                if matched:
                    return True
        return False

    if matcher["keywords"]:
        for text in texts:
            text_lower = text.lower()
            if any(kw.lower() in text_lower for kw in matcher["keywords"]):
                return True
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

    ``domain`` — if set, only policies matching this domain are evaluated.
    Valid values: ``"pipeline"``, ``"rag"``, ``"mcp"``.

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
    # M-21: track which policy ids we've already recorded so analytics /
    # top-violators aren't skewed by counting the same policy once per matched
    # rule. matched_policy_ids stays 1 entry per policy; matched_rule_ids is
    # still per-rule.
    seen_policy_ids: set[int] = set()

    for policy in policies_qs:
        # M-04: actor-scoping — skip an entire policy whose allowlists exclude
        # this request's actor (user/agent/role). No allowlist == applies to all.
        if not _policy_applies_to_actor(policy, context):
            continue

        rules = [r for r in policy.rules.all() if r.enabled]
        rules.sort(key=lambda r: (-r.priority, r.id))

        for rule in rules:
            # Tool-level targeting: skip rules bound to a different tool
            rule_target = getattr(rule, "target_tool", "") or ""
            if rule_target and tool_name and rule_target != tool_name:
                continue

            if not _evaluate_rule(rule, context):
                continue

            # M-21: append the policy id at most once even if several of its
            # rules match.
            if policy.id not in seen_policy_ids:
                seen_policy_ids.add(policy.id)
                result.matched_policy_ids.append(policy.id)
                result.matched_policy_codes.append(policy.code)
            result.matched_rule_ids.append(rule.id)
            result.matched_rule_names.append(rule.name)

            rank = ACTION_ORDER.get(rule.action, 0)
            if rank > best_action_rank:
                best_action_rank = rank
                result.action = rule.action

            # Collect a redaction hint for EVERY matched redact rule (not just
            # the highest-ranked one) so output/input scrubbing can union all
            # of them regardless of the final aggregate action.
            if rule.action == "redact":
                hint = _build_redaction_hint(rule)
                if hint:
                    result.redaction_hints.append(hint)

    if result.action == "block" and result.matched_rule_ids:
        result.message = "Request blocked by policy"
    elif result.action == "redact" and result.matched_rule_ids:
        result.message = "Content redacted by policy"
    elif result.action == "monitor" and result.matched_rule_ids:
        result.message = "Request allowed; event recorded for monitoring"

    return result


def _build_redaction_hint(rule: Rule) -> dict[str, Any] | None:
    """Build a structured redaction hint for a matched redact rule.

    The hint carries BOTH:
      * ``config`` (regex/keywords/replacement) so the legacy string
        redactor ``apply_redaction`` keeps working for dry-run previews;
      * ``direction`` / ``scope`` / ``key`` / ``preset`` so the MCP
        enforcement path can scrub the right side (input args vs. tool
        response) at the right granularity (whole payload vs. one field).
    """
    matcher = _resolve_matcher(rule)
    config: dict[str, Any] = {"replacement": matcher["replacement"]}
    if matcher["regex"]:
        config["regex"] = matcher["regex"]
    if matcher["keywords"]:
        config["keywords"] = matcher["keywords"]
    if not matcher["regex"] and not matcher["keywords"]:
        return None
    return {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "direction": matcher["direction"],
        "scope": matcher["scope"],
        "key": matcher["key"],
        "preset": matcher["preset"],
        "config": config,
    }
