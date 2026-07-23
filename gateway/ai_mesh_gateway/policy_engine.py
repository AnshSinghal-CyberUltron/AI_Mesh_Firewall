"""
Local policy evaluation engine for the Gateway Data Plane.

This is a Django-free port of backend/policy/engine.py that operates
on the compiled JSON bundle (plain dicts) stored in the PolicySync
in-memory cache.  No database queries, no Django ORM — pure Python
for sub-millisecond enforcement.

Action precedence: block (3) > redact (2) > monitor (1) > allow (0)
"""

import copy
import functools
import json
import logging
import re
import threading
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

# Phase 2 (2026-07-23): the "detector" rule type. An operator-facing detector class maps to
# the redact_all_scoped class set (classify_pattern_key → "pii"/"credential"/"ip_leakage";
# secrets fold into "credential" via the SECRET compliance tag). ``all`` = every class.
# ``secret`` is accepted as an operator-friendly alias of ``credential``.
_DETECTOR_CLASS_MAP: dict[str, frozenset[str]] = {
    "pii": frozenset({"pii"}),
    "credential": frozenset({"credential"}),
    "secret": frozenset({"credential"}),
    "ip_leakage": frozenset({"ip_leakage"}),
    "all": frozenset({"pii", "credential", "ip_leakage"}),
}
_DETECTOR_ALL_CLASSES = _DETECTOR_CLASS_MAP["all"]


def _resolve_detector_classes(value: Any) -> frozenset[str]:
    """Map an operator ``detector_class`` (str or list) to redact_all_scoped classes.
    Unknown/empty → the full suite (``all``), the safe/complete default for a detector rule."""
    if isinstance(value, str):
        return _DETECTOR_CLASS_MAP.get(value.strip().lower(), _DETECTOR_ALL_CLASSES)
    if isinstance(value, (list, tuple, set)):
        out: set[str] = set()
        for v in value:
            out |= set(_DETECTOR_CLASS_MAP.get(str(v).strip().lower(), frozenset()))
        return frozenset(out) or _DETECTOR_ALL_CLASSES
    return _DETECTOR_ALL_CLASSES

# FINDING-3 (ReDoS): mirror the control-plane write-time guard into the gateway
# hot path so a catastrophic-backtracking pattern that lands in a compiled
# bundle cannot pin the worker thread evaluating it.
#   * _MAX_REGEX_LEN      — reject absurdly long patterns at compile time.
#   * _NESTED_QUANTIFIER_RE / _has_redos_shape — reject the nested
#     unbounded-quantifier family ((a+)+, (a*)*, …) before re.compile.
#   * _REGEX_MATCH_TIMEOUT_S / _MAX_MATCH_INPUT_LEN — wall-clock + input-length
#     budget around the actual match so a slip-through can't run unbounded.
_MAX_REGEX_LEN = 1000
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*]\s*\)\s*[+*{]")
# F8 (parity with control serializer): the gateway guard previously caught only
# the nested-quantifier family and missed the quantified-wildcard ((.*a){n}) and
# quantified-alternation ((X|XY)+) ReDoS families, so a dangerous redaction_config
# regex reached apply_redaction. Mirror the control-side shapes.
_QUANTIFIED_WILDCARD_GROUP_RE = re.compile(r"\([^()]*\.[*+][^()]*\)\s*[+*{]")
_QUANTIFIED_ALTERNATION_GROUP_RE = re.compile(r"\([^()]*\|[^()]*\)[+*{]")
_REGEX_MATCH_TIMEOUT_S = 1.0
_MAX_MATCH_INPUT_LEN = 100_000


def _strip_for_redos_probe(pattern: str) -> str:
    """Neutralize escaped parens for group-boundary probing.

    Do NOT strip ``\\s``, ``\\d``, ``\\w``, etc. — the old ``re.sub(r'\\\\.', ...)``
    corrupted those shorthands (``\\s+`` → ``+``) and falsely flagged safe
    catalog patterns like PIPE_PHI MRN matchers as ReDoS.
    """
    return pattern.replace(r"\(", "(").replace(r"\)", ")")


def _has_redos_shape(pattern: str) -> bool:
    """True if ``pattern`` carries a nested unbounded-quantifier shape known to
    cause catastrophic backtracking (e.g. ``(a+)+``). Escaped literal parens are
    neutralized first so they cannot spoof a group boundary."""
    if not isinstance(pattern, str):
        return False
    stripped = _strip_for_redos_probe(pattern)
    return bool(
        _NESTED_QUANTIFIER_RE.search(stripped)
        or _QUANTIFIED_WILDCARD_GROUP_RE.search(stripped)
        or _QUANTIFIED_ALTERNATION_GROUP_RE.search(stripped)
    )


@functools.lru_cache(maxsize=256)
def _compile_regex(pattern: str) -> re.Pattern:
    """Compile and cache regex patterns for hot-path performance.

    FINDING-3: reject oversized or nested-unbounded-quantifier patterns by
    raising ``re.error`` — every caller already treats re.error as "skip this
    rule / no match", so a dangerous pattern is dropped fail-open rather than
    fed to the backtracking engine.
    """
    if len(pattern) > _MAX_REGEX_LEN:
        raise re.error(f"pattern exceeds {_MAX_REGEX_LEN} chars (possible ReDoS)")
    if _has_redos_shape(pattern):
        raise re.error("pattern has a nested unbounded quantifier (possible ReDoS)")
    return re.compile(pattern, re.IGNORECASE)


def _run_with_timeout(fn, timeout):
    """Run ``fn()`` in a daemon thread; return its result or None on
    timeout/exception. Frees the caller after ``timeout`` seconds even if a
    backtracking regex is still running (the worker is a daemon)."""
    box: dict[str, Any] = {}

    def _target():
        try:
            box["result"] = fn()
        except Exception:  # noqa: BLE001 — fail-open like the re.error path
            box["error"] = True

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive() or box.get("error"):
        return None
    return box.get("result")


def _search_with_budget(compiled: re.Pattern, text: str) -> bool:
    """``compiled.search(text)`` under input-length + wall-clock budgets.

    Returns False on timeout (treated as no match), freeing the worker thread."""
    if len(text) > _MAX_MATCH_INPUT_LEN:
        text = text[:_MAX_MATCH_INPUT_LEN]
    matched = _run_with_timeout(
        lambda _c=compiled, _t=text: _c.search(_t) is not None,
        _REGEX_MATCH_TIMEOUT_S,
    )
    if matched is None:
        LOG.warning("regex match exceeded %.1fs budget; abandoned (possible ReDoS)", _REGEX_MATCH_TIMEOUT_S)
        return False
    return matched


def _finditer_validate_with_budget(compiled: re.Pattern, text: str, validator) -> bool:
    """Validator-gated finditer under the same budgets. False on timeout."""
    if len(text) > _MAX_MATCH_INPUT_LEN:
        text = text[:_MAX_MATCH_INPUT_LEN]

    def _scan(_c=compiled, _t=text, _v=validator):
        for m in _c.finditer(_t):
            if _v(m.group(0)):
                return True
        return False

    matched = _run_with_timeout(_scan, _REGEX_MATCH_TIMEOUT_S)
    if matched is None:
        LOG.warning("validator-gated regex match exceeded %.1fs budget; abandoned (possible ReDoS)", _REGEX_MATCH_TIMEOUT_S)
        return False
    return matched

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
    # I-05: the matched REWRITE rules' conditions. §1.2 defines rewrite as "strip
    # harmful pattern, log original", but the gateway only PREPENDED an advisory
    # notice to the untouched prompt — and even that never reached the wire. Carrying
    # the rule conditions lets the rewrite genuinely remove the matched span using the
    # same masking machinery as redact (apply_redaction), instead of attesting a
    # strip that never happened.
    rewrite_hints: list[dict[str, Any]] = field(default_factory=list)
    # 3b (BACKSTOP finding #1): named response fields to mask for the MATCHED
    # actor-scoped policies. Mirrors control Policy.redaction_fields; the compiler
    # already emits these into the compiled bundle (compiler.py:521 under M-04) but
    # the gateway never consumed them — so the stdio/websocket ADAPTER path did
    # content-scan yet NO field-level RBAC masking (the HTTP path masks them via
    # control apply_field_redaction). Populated by evaluate() from every matched
    # policy that declares redaction_fields (D6 trigger = "policy matched AND has
    # non-empty redaction_fields", NOT gated on the verdict); applied to the OUTPUT
    # structured payload downstream (mcp_scan_orchestrator.scan_mcp_payload).
    redaction_fields: list[str] = field(default_factory=list)
    # FIX-1.2a: the winning model_downgrade rule's target model. Empty when the
    # final action is not model_downgrade (or the rule carries no downgrade_to).
    model_downgrade_target: str = ""
    message: str = ""

def _policy_applies_to_actor(
    policy: dict[str, Any],
    actor: dict[str, Any] | None,
) -> bool:
    """M-04: scope a compiled policy by the request's actor (user/agent/role).

    Mirrors control/ai_mesh_control/policy/engine._policy_applies_to_actor but
    reads the allowlists off the plain compiled ``policy`` dict.

    CRITICAL COMPAT: OLD bundles compiled before M-04 do NOT carry the
    allowed_* keys. ``policy.get(..., [])`` returns [] for those, which is
    treated as a wildcard, so an old bundle behaves exactly as before — every
    policy applies. Never raises KeyError on a missing key.

    ``actor`` (all optional):
      * ``user_id``  -> allowed_user_ids  (compared as-is then str-coerced);
      * ``agent_id`` -> allowed_agent_ids (string / API-key prefix);
      * ``roles``    -> must overlap allowed_roles.

    Each dimension constrains ONLY on a positive mismatch: a policy is skipped
    when the actor's identity for that dimension is KNOWN and not in the
    allowlist. An unknown dimension (missing key, None, empty string/list)
    leaves the policy applied. This is deliberate fail-closed security:
    ``user_id``/``roles`` arrive via client-supplied headers, so skipping on
    missing identity would let a caller dodge a scoped block policy simply by
    omitting the header. It also means a None/empty ``actor`` preserves exact
    pre-M-04 behaviour (every policy applies).
    """
    allowed_user_ids = policy.get("allowed_user_ids") or []
    allowed_agent_ids = policy.get("allowed_agent_ids") or []
    allowed_roles = policy.get("allowed_roles") or []

    # No actor-scoping configured on this policy -> applies to everyone.
    if not allowed_user_ids and not allowed_agent_ids and not allowed_roles:
        return True

    actor = actor or {}

    if allowed_user_ids:
        user_id = actor.get("user_id")
        # Match either the native type or the string form (bundle ints vs. str).
        if user_id is not None and (
            user_id not in allowed_user_ids
            and str(user_id) not in {str(u) for u in allowed_user_ids}
        ):
            return False

    if allowed_agent_ids:
        agent_id = actor.get("agent_id")
        if agent_id and str(agent_id) not in {str(a) for a in allowed_agent_ids}:
            return False

    if allowed_roles:
        raw_roles = actor.get("roles") or actor.get("actor_roles") or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        actor_roles = {str(r) for r in raw_roles if r}
        if actor_roles and not actor_roles.intersection({str(r) for r in allowed_roles}):
            return False

    return True


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
            compiled = _compile_regex(pattern)
        except re.error:
            return False
        # FINDING-3: run the actual match under input-length + wall-clock budgets.
        return _search_with_budget(compiled, text)
    
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
    actor: dict[str, Any] | None = None,
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

    ``actor`` — M-04: optional dict {user_id, agent_id, roles}. When a policy
    carries actor allowlists (allowed_user_ids/allowed_agent_ids/allowed_roles)
    it only applies to matching actors. Omitting ``actor`` or evaluating an OLD
    bundle without those keys preserves the previous "applies to all" behaviour.

    Returns an EvaluationResult with the highest-severity action
    among all matching rules.
    """
    result = EvaluationResult()
    best_action_rank = -1
    _blocker_policy_name = ""
    _blocker_rule_name = ""

    for entry in compiled_policies:
        policy = entry.get("policy", {})
        rules = entry.get("rules", [])

        # M-04: skip a policy whose actor allowlist excludes this request's
        # actor. Old bundles (no allowed_* keys) -> applies to all.
        if not _policy_applies_to_actor(policy, actor):
            continue

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
            # 3b: surface the MATCHED policy's response-field redaction list so the
            # adapter path can mask those named fields on the tool RESULT. Deduped +
            # order-preserving; idempotent across a policy's multiple matching rules.
            for _rf in (policy.get("redaction_fields") or []):
                if isinstance(_rf, str) and _rf and _rf not in result.redaction_fields:
                    result.redaction_fields.append(_rf)

            action = rule.get("action", "monitor")
            rank = ACTION_ORDER.get(action, 0)
            if rank > best_action_rank:
                best_action_rank = rank
                result.action = action
                # FIX-1.2a: when the WINNING action is model_downgrade, carry the
                # winning rule's downgrade target so the gateway downgrades to the
                # rule-specified model (not a same-model no-op default). The
                # compiled rule carries its redaction_config (where downgrade_to
                # lives); empty/missing -> "" (gateway then no-ops, see main.py).
                if action == "model_downgrade":
                    _rc = rule.get("redaction_config") or {}
                    result.model_downgrade_target = str(
                        (_rc.get("downgrade_to") if isinstance(_rc, dict) else "") or ""
                    )
                else:
                    # A higher-ranked non-downgrade action superseded an earlier
                    # downgrade; clear the now-stale target.
                    result.model_downgrade_target = ""
                if action == "block":
                    # Attribute the block to the RULE THAT BLOCKED — not the last
                    # rule that merely matched (e.g. a redact PII rule), which
                    # corrupted the audit trail + per-rule analytics.
                    _blocker_policy_name = policy.get("name", "")
                    _blocker_rule_name = rule.get("name", "")
            # Collect a redaction hint for EVERY matching redact rule — NOT only
            # the one that happens to raise the top action rank. The append used
            # to live inside the `rank > best_action_rank` block, so when several
            # redact rules matched (all rank 4) only the FIRST produced a hint and
            # apply_redaction masked just that one pattern; the remaining PII (SSN/
            # email/phone) leaked through to Tier-2 and the LLM. Deterministic
            # policy redaction must cover all matched patterns.
            # G5 ENFORCEMENT-CONSISTENCY: append a redaction hint for EVERY matched
            # redact rule — even one authored WITHOUT a redaction_config. Previously the
            # append was gated on redaction_config, so a redact rule with none yielded
            # action=='redact' but ZERO hints; apply_redaction was then a no-op and the
            # caller forwarded the sensitive content RAW (a redact-that-leaks). apply_
            # redaction falls back to the rule's own condition regex/keywords + the
            # default placeholder, so a redact verdict now always masks its matched span.
            if action == "redact":
                result.redaction_hints.append({
                    "rule_id": rule.get("id"),
                    "rule_name": rule.get("name"),
                    "config": rule.get("redaction_config") or {},
                    "condition": rule.get("condition") or {},
                })
            # I-05: same hint shape for REWRITE rules, kept in a SEPARATE list so a
            # rewrite verdict cannot be mistaken for a redact one downstream (the
            # redact path builds redacted_prompt/telemetry off redaction_hints).
            elif action == "rewrite":
                result.rewrite_hints.append({
                    "rule_id": rule.get("id"),
                    "rule_name": rule.get("name"),
                    "config": rule.get("redaction_config") or {},
                    "condition": rule.get("condition") or {},
                })

    if result.action == "block" and result.matched_rule_ids:
        # Prefer the rule whose action actually BLOCKED; fall back to the last
        # matched rule only if no block rule was captured (defensive).
        blocker_policy = _blocker_policy_name or (result.matched_policy_names[-1] if result.matched_policy_names else "Unknown")
        blocker_rule = _blocker_rule_name or (result.matched_rule_names[-1] if result.matched_rule_names else "Unknown")
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
    actor: dict[str, Any] | None = None,
) -> EvaluationResult:
    """Evaluate only rules that apply to a specific pipeline stage.

    Rules with an empty pipeline_stage apply to all stages.
    Rules with a specific pipeline_stage only apply to that stage.

    ``actor`` — M-04: forwarded to ``evaluate`` for actor-scoping (optional).
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
    return evaluate(prompt, response_text, filtered_policies, actor=actor)


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


# #31 (gateway twin of control #29): the old RECURSIVE depth-10 cap was a
# DETECTION BYPASS — a scope='key' rule targeting a field nested 11..500 deep was
# silently NOT matched, so its block/redact action never fired. The gateway admits
# payloads to _MCP_MAX_RESULT_DEPTH=500 and apply_field_redaction (CHG-0148) masks
# to 500 → this matcher was the inconsistent sibling. Iterative (explicit stack) so
# depth 500 is safe with no RecursionError; node cap bounds pathological width.
_KEY_COLLECT_MAX_DEPTH = 500
_KEY_COLLECT_MAX_NODES = 2_000_000


def _collect_key_values(obj: Any, key: str) -> list[str]:
    if not key:
        return []
    target = _normalize_key(key)
    out: list[str] = []
    stack: list[tuple[Any, int]] = [(obj, 0)]
    nodes = 0
    while stack:
        cur, depth = stack.pop()
        if depth > _KEY_COLLECT_MAX_DEPTH:
            continue
        nodes += 1
        if nodes > _KEY_COLLECT_MAX_NODES:
            break
        if isinstance(cur, dict):
            for k, v in cur.items():
                if _normalize_key(k) == target:
                    # Matched key: collect but do NOT descend (original semantics).
                    out.append(v if isinstance(v, str) else _safe_json(v))
                else:
                    stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for item in cur:
                stack.append((item, depth + 1))
    return out


def _leaf_value_texts(obj: Any) -> list[str]:
    """Collect the string (and stringified numeric) VALUE leaves of a structured payload —
    value content only, NEVER key names. Iterative + bounded (mirrors _collect_key_values).

    scope=entire MCP detection used to scan ``_safe_json(input_args)`` — the whole serialized
    blob, INCLUDING key names and JSON punctuation. That made detection see content the
    value-leaf redaction can never mask: a redact rule whose keyword/regex hit a KEY NAME
    (``arguments``, ``password``, ``url`` …) or a cross-field ``"key":"val"`` structure matched
    in detection but nothing in the values, so the cannot-mask fail-closed BLOCKED benign
    traffic, or a structural regex claimed a redact it couldn't fulfil. Scanning value leaves
    makes detection consistent with redaction: a rule matches iff its pattern is in actual
    value content, which is exactly what the value-leaf redactor can mask."""
    out: list[str] = []
    stack: list[tuple[Any, int]] = [(obj, 0)]
    nodes = 0
    while stack:
        cur, depth = stack.pop()
        if depth > _KEY_COLLECT_MAX_DEPTH:
            continue
        nodes += 1
        if nodes > _KEY_COLLECT_MAX_NODES:
            break
        if isinstance(cur, str):
            out.append(cur)
        elif isinstance(cur, bool):
            continue  # "true"/"false" carries no secret
        elif isinstance(cur, (int, float)):
            out.append(_safe_json(cur))  # numeric secret (SSN/card as a number)
        elif isinstance(cur, dict):
            for v in cur.values():  # values only — keys are never scanned
                stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for item in cur:
                stack.append((item, depth + 1))
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
    # Phase 2 (2026-07-23): a ``detector`` rule invokes the built-in detector SUITE (the
    # full 63-pattern detect_* engine) for an operator-facing class, so a single policy rule
    # can carry the coverage a server posture used to provide. detector_class resolves to the
    # redact_all_scoped class set; detection reuses the SAME engine (a class matches iff
    # redact_all_scoped changes the text), so detection and redaction can never disagree.
    detector_class = None
    if rule.get("rule_type") == "detector" or cond.get("detector_class"):
        detector_class = _resolve_detector_classes(cond.get("detector_class"))
    elif preset:
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
        "detector_class": detector_class,
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
            # entire: scan VALUE leaves (not the serialized blob's key names) so detection
            # matches only maskable value content — consistent with the value-leaf redactor.
            # Fall back to the chat-style ``prompt`` when input_args yields no leaves
            # (empty/None), then to a raw serialization for a non-container scalar.
            leaves = _leaf_value_texts(input_args) if isinstance(input_args, (dict, list)) else []
            if leaves:
                texts.extend(leaves)
            elif prompt:
                texts.append(prompt)
            elif input_args is not None:
                texts.append(_safe_json(input_args))
    if want_output:
        if scope == "key":
            texts.extend(_collect_key_values(output_data, key))
        else:
            leaves = _leaf_value_texts(output_data) if isinstance(output_data, (dict, list)) else []
            if leaves:
                texts.extend(leaves)
            elif response:
                texts.append(response)
            elif output_data is not None:
                texts.append(_safe_json(output_data))

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
        # FINDING-3: every match runs under input-length + wall-clock budgets so
        # a catastrophic pattern can't pin the worker thread.
        for text in texts:
            if validator is None:
                if _search_with_budget(compiled, text):
                    return True
            else:
                if _finditer_validate_with_budget(compiled, text, validator):
                    return True
        return False

    if matcher["keywords"]:
        # Keyword DETECTION is SUBSTRING (``kw in text``), and apply_redaction masks the same
        # substring occurrence (word-bounded first, then a literal-substring fallback), so the
        # two agree on what "matched" means. An earlier word-bounded DETECTION silently missed
        # boundary-hostile credential keywords (``ghp_``/``AKIA``/``-----BEGIN``: ``\bghp_\b``
        # can't match ``ghp_ABC…`` — no word boundary after ``_``), so those redact rules
        # DETECTED NOTHING and forwarded the secret RAW under every posture with no telemetry —
        # a far worse regression than the ``secret``-in-``secretary`` false-block it fixed
        # (now handled by the substring-fallback masking, not by narrowing detection). Empty
        # keywords are skipped (``"" in text`` is always True → would match every payload).
        for text in texts:
            tl = text.lower()
            for kw in matcher["keywords"]:
                if kw and kw.lower() in tl:
                    return True
        return False

    if matcher["detector_class"]:
        # A detector rule matches iff the built-in detector SUITE would mask something in this
        # class — reuse the SAME redact_all_scoped engine the rule redacts with, so detection
        # and redaction agree by construction (no detect-on-blob / mask-on-leaf asymmetry).
        from patterns import redact_all_scoped  # local: patterns has no import cycle here
        classes = set(matcher["detector_class"])
        for text in texts:
            if redact_all_scoped(text, classes) != text:
                return True
        return False

    return False


def evaluate_mcp_policies(
    compiled_policies: list[dict[str, Any]],
    context: dict[str, Any],
    tool_name: str | None = None,
    actor: dict[str, Any] | None = None,
) -> EvaluationResult:
    """Evaluate MCP tool-call context against compiled policy bundle entries.

    ``actor`` — M-04: optional {user_id, agent_id, roles}. When not provided,
    falls back to actor-ish keys already present in ``context``. Old bundles
    without allowed_* keys still apply to all actors.
    """
    result = EvaluationResult()
    best_action_rank = -1
    _blocker_policy_name = ""
    _blocker_rule_name = ""

    # M-04: MCP context already carries user_id/agent_id; reuse them as the
    # actor when the caller didn't pass one explicitly. Missing keys -> None.
    if actor is None:
        actor = {
            "user_id": context.get("user_id"),
            "agent_id": context.get("agent_id"),
            "roles": context.get("roles") or context.get("actor_roles"),
        }

    for entry in compiled_policies:
        policy = entry.get("policy", {})
        rules = entry.get("rules", [])

        # M-04: actor-scoping (old bundles without allowed_* keys apply to all).
        if not _policy_applies_to_actor(policy, actor):
            continue

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
            # 3b: surface the MATCHED policy's response-field redaction list so the
            # adapter path can mask those named fields on the tool RESULT. Deduped +
            # order-preserving; idempotent across a policy's multiple matching rules.
            for _rf in (policy.get("redaction_fields") or []):
                if isinstance(_rf, str) and _rf and _rf not in result.redaction_fields:
                    result.redaction_fields.append(_rf)

            action = rule.get("action", "monitor")
            rank = ACTION_ORDER.get(action, 0)
            if rank > best_action_rank:
                best_action_rank = rank
                result.action = action
                if action == "block":
                    _blocker_policy_name = policy.get("name", "")
                    _blocker_rule_name = rule.get("name", "")

            if action == "redact":
                matcher = _resolve_matcher_dict(rule)
                config: dict[str, Any] = {"replacement": matcher["replacement"]}
                if matcher["regex"]:
                    config["regex"] = matcher["regex"]
                if matcher["keywords"]:
                    config["keywords"] = matcher["keywords"]
                if matcher["detector_class"]:
                    # sorted list for a stable, JSON-serializable hint
                    config["detector_class"] = sorted(matcher["detector_class"])
                if matcher["regex"] or matcher["keywords"] or matcher["detector_class"]:
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
        # Prefer the rule whose action actually BLOCKED; fall back to the last
        # matched rule only if no block rule was captured (defensive).
        blocker_policy = _blocker_policy_name or (result.matched_policy_names[-1] if result.matched_policy_names else "Unknown")
        blocker_rule = _blocker_rule_name or (result.matched_rule_names[-1] if result.matched_rule_names else "Unknown")
        result.message = f"Blocked by policy '{blocker_policy}', rule '{blocker_rule}'"
    elif result.action == "redact" and result.matched_rule_ids:
        result.message = "Content redacted by policy"
    elif result.action == "monitor" and result.matched_rule_ids:
        result.message = "Request allowed; event recorded for monitoring"

    return result


# ── Smart-masking pattern-kind classification (M-17) ─────────────────────────
#
# Partial "smart" masks (j***@e***.com, ***-**-6789, …) keep a recognisable
# fragment of the original value, so they may ONLY be applied when we are
# CERTAIN what kind of value the rule targets. The old implementation sniffed
# substrings of the backslash-stripped regex string ("@" in pattern → email),
# which both misclassified custom patterns and crashed on values the email
# lambda could not split. Classification is now structured, in order:
#
#   1. explicit kind metadata on the hint ("pattern_kind" in config/hint/
#      condition),
#   2. the rule's preset (structured field emitted by the MCP hint builder and
#      optionally present in the rule condition),
#   3. rule name / category tokens — accepted only when they map to exactly
#      one kind,
#   4. regex-shape probe: the pattern must fullmatch the canonical exemplars
#      of exactly one kind,
#   5. otherwise → full mask (the rule's replacement / placeholder).
#
# Even after a kind is chosen, each per-match masker validates the MATCHED
# VALUE's shape and falls back to the full replacement when it does not fit
# (e.g. email mask on a value without exactly one "@"). Uncertainty never
# weakens masking.

_KIND_FULL = "full"

_SMART_MASK_KIND_ALIASES: dict[str, str] = {
    "email": "email",
    "e-mail": "email",
    "email_address": "email",
    "ssn": "ssn",
    "us_ssn": "ssn",
    "social_security": "ssn",
    "card": "card",
    "credit_card": "card",
    "creditcard": "card",
    "payment_card": "card",
    "phone": "phone",
    "phone_number": "phone",
    "telephone": "phone",
}

_KIND_NAME_TOKENS: dict[str, tuple[str, ...]] = {
    "email": ("email", "e-mail"),
    "ssn": ("ssn", "social security", "social_security"),
    "card": ("credit card", "credit_card", "card number", "card_number", "payment card"),
    "phone": ("phone", "telephone"),
}

_KIND_EXEMPLARS: dict[str, tuple[str, ...]] = {
    "email": ("john.doe@example.com",),
    "ssn": ("123-45-6789",),
    "card": ("4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111"),
    "phone": ("555-123-4567", "(555) 123-4567"),
}


def _mask_email_value(value: str, fallback: str) -> str:
    """j***@e***.com — full mask unless value is unambiguously email-shaped.

    Guards the old IndexError: values without '@' (split()[1] blew up) or
    with multiple '@' (would leak parts of the second segment) now get the
    full replacement instead.
    """
    local, sep, domain = value.partition("@")
    if not sep or not local or "@" in domain:
        return fallback
    domain_name, dot, _ = domain.partition(".")
    tld = domain.rsplit(".", 1)[-1]
    if not dot or not domain_name or not tld:
        return fallback
    return f"{local[:1]}***@{domain_name[:1]}***.{tld}"


def _mask_ssn_value(value: str, fallback: str) -> str:
    """***-**-6789 — full mask unless the value carries exactly 9 digits."""
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) != 9:
        return fallback
    return f"***-**-{digits[-4:]}"


def _mask_card_value(value: str, fallback: str) -> str:
    """****-****-****-1111 — full mask outside the 13–19 digit card range."""
    digits = re.sub(r"[^\d]", "", value)
    if not 13 <= len(digits) <= 19:
        return fallback
    return f"****-****-****-{digits[-4:]}"


def _mask_phone_value(value: str, fallback: str) -> str:
    """***-***-5309 — full mask unless 7–15 digits (E.164 bounds)."""
    digits = re.sub(r"[^\d]", "", value)
    if not 7 <= len(digits) <= 15:
        return fallback
    return f"***-***-{digits[-4:]}"


_SMART_MASKERS = {
    "email": _mask_email_value,
    "ssn": _mask_ssn_value,
    "card": _mask_card_value,
    "phone": _mask_phone_value,
}


def _probe_regex_kind(regex_pattern: str) -> str:
    """Classify a redaction regex by shape: it must fullmatch the canonical
    exemplars of exactly ONE kind. Zero or multiple kinds → uncertain → full
    mask. ``fullmatch`` (not search) so broad patterns that merely contain an
    email/SSN-shaped fragment do not get partial masking."""
    try:
        compiled = _compile_regex(regex_pattern)
    except re.error:
        return _KIND_FULL
    matched = {
        kind
        for kind, exemplars in _KIND_EXEMPLARS.items()
        if any(compiled.fullmatch(ex) for ex in exemplars)
    }
    if len(matched) == 1:
        return matched.pop()
    return _KIND_FULL


def _classify_pattern_kind(hint: dict[str, Any], regex_pattern: str) -> str:
    """Resolve the pattern kind for a redaction hint (see module comment).

    Returns one of the _SMART_MASKERS keys or _KIND_FULL. Any uncertain or
    conflicting signal resolves to _KIND_FULL — never weaken masking."""
    config = hint.get("config") or {}
    condition = hint.get("condition") or {}

    # 1. Explicit structured kind metadata.
    explicit = (
        config.get("pattern_kind")
        or hint.get("pattern_kind")
        or condition.get("pattern_kind")
    )
    if explicit is not None:
        if isinstance(explicit, str):
            return _SMART_MASK_KIND_ALIASES.get(explicit.strip().lower(), _KIND_FULL)
        return _KIND_FULL

    # 2. Rule preset (MCP hints carry it top-level; conditions may embed it).
    preset = hint.get("preset") or condition.get("preset")
    if preset is not None:
        if isinstance(preset, str):
            return _SMART_MASK_KIND_ALIASES.get(preset.strip().lower(), _KIND_FULL)
        return _KIND_FULL

    # 3. Rule name / category tokens — only when exactly one kind matches.
    label = " ".join(
        str(part)
        for part in (
            hint.get("rule_name"),
            hint.get("category"),
            config.get("category"),
            condition.get("category"),
        )
        if isinstance(part, str)
    ).lower()
    if label:
        token_kinds = {
            kind
            for kind, tokens in _KIND_NAME_TOKENS.items()
            if any(token in label for token in tokens)
        }
        if len(token_kinds) == 1:
            return token_kinds.pop()
        if token_kinds:
            return _KIND_FULL  # conflicting metadata → uncertain → full mask

    # 4. Regex-shape probe (explicit full-mask fallback inside).
    return _probe_regex_kind(regex_pattern)


def apply_redaction(
    text: str,
    redaction_hints: list[dict[str, Any]],
    placeholder: str = DEFAULT_REDACTION_PLACEHOLDER,
) -> str:
    """
    Apply redaction hints to text.  Each hint has a 'config' dict
    which may contain 'regex', 'keywords', and 'replacement'.

    Regex hints are smart-masked per pattern kind (email/ssn/card/phone)
    when classification is certain; otherwise the full replacement is
    substituted. See _classify_pattern_kind for the classification order.
    """
    if not text or not redaction_hints:
        return text

    result = text
    for hint in redaction_hints:
        config = hint.get("config") or {}
        condition = hint.get("condition") or {}
        repl = config.get("replacement") or placeholder

        # Phase 2: a ``detector`` hint masks its class(es) with the SAME redact_all_scoped
        # engine detection used — smart per-class masking of the full 63-pattern suite.
        detector_class = config.get("detector_class") or condition.get("detector_class")
        if detector_class:
            from patterns import redact_all_scoped  # local: no import cycle
            classes = set(_resolve_detector_classes(detector_class))
            result = redact_all_scoped(result, classes)
            continue

        regex_pattern = (
            config.get("regex")
            or config.get("pattern")
            or condition.get("regex")
            or condition.get("pattern")
        )
        if regex_pattern:
            try:
                compiled = _compile_regex(regex_pattern)
            except re.error:
                continue
            masker = _SMART_MASKERS.get(_classify_pattern_kind(hint, regex_pattern))
            # F8: bound the substitution wall-clock. _compile_regex already rejects
            # the known ReDoS shapes, but the heuristic is not a full solver — a
            # slip-through .sub() on a crafted long tail could pin the worker thread
            # (the original match-path budget covered _evaluate_rule but NOT the
            # redaction-application path). Run the .sub under the match budget; on
            # timeout skip THIS rule (others still apply) and log, never hang.
            if masker is None:
                _do_sub = (lambda _c=compiled, _r=repl, _t=result: _c.sub(_r, _t))
            else:
                _do_sub = (lambda _c=compiled, _m=masker, _r=repl, _t=result: _c.sub(
                    lambda m, _mask=_m, _repl=_r: _mask(m.group(0), _repl), _t,
                ))
            _new = _run_with_timeout(_do_sub, _REGEX_MATCH_TIMEOUT_S)
            if _new is None:
                LOG.warning(
                    "redaction .sub exceeded %.1fs budget; rule skipped (possible ReDoS)",
                    _REGEX_MATCH_TIMEOUT_S,
                )
            else:
                result = _new

        keywords = (
            config.get("keywords")
            or config.get("keywords_list")
            or condition.get("keywords")
            or condition.get("keywords_list")
            or []
        )
        for kw in keywords:
            # A keyword rule masks the LITERAL SUBSTRING wherever it appears — the same
            # semantics keyword DETECTION uses (``kw in text``), so a detected keyword is
            # ALWAYS fully masked (every occurrence, standalone AND embedded). This closes:
            #   * the prefix-credential SILENT BYPASS (``ghp_``/``AKIA``/``-----BEGIN`` — a
            #     word-bounded ``\b{kw}\b`` never matched them, so they egressed raw);
            #   * the partial-mask gap where a word-bounded pass masked one occurrence and
            #     skipped a second embedded one (``key`` in ``api_keyXYZ``).
            # Empty keywords are skipped (an empty ``.sub`` would blanket the whole text —
            # Finding B); the compile is guarded (a >_MAX_REGEX_LEN keyword raises re.error,
            # which used to crash the whole scan uncaught — Finding C). ``re.escape`` makes the
            # pattern a pure literal, so no ReDoS. Over-masking a benign word that contains the
            # keyword (``secret`` in ``secretary``) is the operator's substring choice and the
            # safe direction.
            if not isinstance(kw, str) or not kw:
                continue
            try:
                result = _compile_regex(re.escape(kw)).sub(repl, result)
            except re.error:
                continue

    return result


FIELD_REDACT_PLACEHOLDER = "[REDACTED]"


def _normalize_field_key(key: Any) -> str:
    """Normalize a dict key for case-insensitive, Unicode-safe matching.

    NFKC folds Cyrillic/Greek homoglyphs (e.g. Cyrillic 'е' U+0435 and Latin
    'e' U+0065) onto the same compatibility codepoint, defeating the
    lookalike-key bypass. Mirrors control/policy/redaction._normalize_key so the
    stdio/websocket adapter path masks the same field names the HTTP path does.
    """
    if not isinstance(key, str):
        return ""
    return unicodedata.normalize("NFKC", key).lower()


def apply_field_redaction(
    obj: Any,
    fields: Any,
    *,
    placeholder: str = FIELD_REDACT_PLACEHOLDER,
    # CHG-0148: max_depth was 10, but the gateway only rejects results deeper than
    # _MCP_MAX_RESULT_DEPTH (500) BEFORE redaction — so a name-based redaction target
    # nested at depth 11..500 evaded masking and egressed RAW. Raised to 500 to cover
    # everything that can reach here; the walk is now ITERATIVE (below) so a 500-deep
    # structure cannot blow the recursion limit (which would raise -> caller fail-open).
    max_depth: int = 500,
    # CHG-0150: was 100_000 — a wide result with a redaction_fields target beyond the 100k-th
    # node had the walk STOP early → the target egressed RAW (CHG-0148 residual #1). Raised
    # ABOVE the gateway's _MCP_MAX_RESULT_NODES guard (1M), which BLOCKS results wider than
    # that before this runs — so anything reaching here is ≤1M nodes and is now FULLY walked
    # (2M margin absorbs any node-counting discrepancy between the guard and this walk).
    max_nodes: int = 2_000_000,
) -> Any:
    """Return a deep-copied ``obj`` with values under matching keys replaced.

    3b (BACKSTOP finding #1): the gateway had NO field-name redactor, so the
    stdio/websocket adapter path could not honor a policy's ``redaction_fields``
    (named-field RBAC masking of tool RESULTS) even though the compiler emits
    them into the bundle. This is a Django-free port of
    control/ai_mesh_control/policy/redaction.apply_field_redaction so both
    transports mask identically.

    Walks dicts/lists recursively; whenever a dict key matches (after NFKC +
    casefold) any name in ``fields`` its value is replaced with ``placeholder``.
    Bounded by ``max_depth``/``max_nodes`` (safety over strictness on adversarial
    payloads). ``obj`` is never mutated — a deep copy is returned.
    """
    if not fields or not isinstance(obj, (dict, list)):
        return obj

    targets = {_normalize_field_key(f) for f in fields if isinstance(f, str)}
    targets.discard("")
    if not targets:
        return obj

    result = copy.deepcopy(obj)
    node_count = 0
    masked = False

    # CHG-0148: ITERATIVE walk (explicit stack) — the old recursion could not safely
    # descend to max_depth=500 (RecursionError -> fail-open leak), which is exactly why
    # max_depth was pinned at a shallow 10 that a nested field could hide beneath.
    stack: list = [(result, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth or node_count > max_nodes:
            continue
        if isinstance(node, dict):
            for k in list(node.keys()):
                node_count += 1
                if node_count > max_nodes:
                    break
                if _normalize_field_key(k) in targets:
                    node[k] = placeholder
                    masked = True
                else:
                    v = node[k]
                    if isinstance(v, (dict, list)):
                        stack.append((v, depth + 1))
        elif isinstance(node, list):
            for item in node:
                node_count += 1
                if node_count > max_nodes:
                    break
                if isinstance(item, (dict, list)):
                    stack.append((item, depth + 1))
    # Identity on a true no-op: when none of the declared fields were present the
    # caller must be able to tell nothing changed (``masked_out is payload``) so a
    # field-projection scan does not mislabel an unchanged result as "redacted".
    return result if masked else obj