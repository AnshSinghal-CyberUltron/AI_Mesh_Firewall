"""
Redaction helper: apply redaction hints to text and return redacted text.
Used when engine returns action=redact.
"""

import copy
import functools
import re
import unicodedata
from typing import Any, Iterable

from policy.constants import DEFAULT_REDACTION_PLACEHOLDER


@functools.lru_cache(maxsize=256)
def _compile_cached(pattern: str) -> re.Pattern[str]:
    """Compile and cache regex patterns for performance."""
    return re.compile(pattern, re.IGNORECASE)


def _mask_email(match: re.Match[str]) -> str:
    value = match.group(0)
    local, domain_full = value.split("@", 1)
    domain_parts = domain_full.rsplit(".", 1)
    domain_name = domain_parts[0]
    tld = domain_parts[1] if len(domain_parts) > 1 else "com"
    return f"{local[:1] or '*'}***@{domain_name[:1] or '*'}***.{tld}"


def _mask_credit_card(match: re.Match[str]) -> str:
    digits = re.sub(r"[^\d]", "", match.group(0))
    return f"****-****-****-{digits[-4:]}"


def _mask_ssn(match: re.Match[str]) -> str:
    value = match.group(0)
    return f"***-**-{value[-4:]}"


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"[^\d]", "", match.group(0))
    return f"***-***-{digits[-4:]}"


def _smart_substitute(result: str, regex_pattern: str, replacement: str) -> str:
    try:
        compiled = _compile_cached(regex_pattern)
    except re.error:
        return result

    normalized = regex_pattern.replace("\\", "")
    if "@" in normalized and "[A-Za-z" in regex_pattern:
        return compiled.sub(_mask_email, result)
    if "d{3}-d{2}-d{4}" in normalized:
        return compiled.sub(_mask_ssn, result)
    if "d{4}[s-]?d{4}[s-]?d{4}[s-]?d{4}" in normalized:
        return compiled.sub(_mask_credit_card, result)
    if "d{3}" in normalized and "d{4}" in normalized and "(?" in regex_pattern:
        return compiled.sub(_mask_phone, result)
    return compiled.sub(replacement, result)


def apply_redaction(
    text: str,
    redaction_hints: list[dict[str, Any]],
    *,
    placeholder: str = DEFAULT_REDACTION_PLACEHOLDER,
) -> str:
    """
    Apply redaction hints to text. Each hint can have:
    - config: dict with 'regex' (pattern to replace) or 'keywords' (list to mask),
      and optional 'replacement' (default placeholder).
    - rule condition can be used to derive regex/keywords if not in config.

    Returns redacted text.
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
            result = _smart_substitute(result, regex_pattern, repl)

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
            pattern = re.escape(kw)
            try:
                compiled = _compile_cached(rf"\b{pattern}\b")
                result = compiled.sub(repl, result)
            except re.error:
                continue

    return result


# ─────────────────────────────────────────────────────────────────────────
# G7: response-field redaction (recursive, key-based)
# ─────────────────────────────────────────────────────────────────────────

_FIELD_REDACT_PLACEHOLDER = "[REDACTED]"


def _normalize_key(key: Any) -> str:
    """Normalize a dict key for case-insensitive, Unicode-safe matching.

    NFKC folds Cyrillic/Greek homoglyphs (e.g. Cyrillic 'е' U+0435 and
    Latin 'e' U+0065) onto the same compatibility codepoint, defeating
    the lookalike-bypass attack flagged in security review.
    """
    if not isinstance(key, str):
        return ""
    return unicodedata.normalize("NFKC", key).lower()


def apply_field_redaction(
    obj: Any,
    fields: Iterable[str],
    *,
    placeholder: str = _FIELD_REDACT_PLACEHOLDER,
    # CHG-0148: was 10, but a name-based redaction target nested at depth 11..N (N up to
    # the gateway's _MCP_MAX_RESULT_DEPTH=500 result-depth guard) evaded masking and
    # egressed RAW — opaque name-redacted fields are NOT caught by the content/pattern
    # scan, so this was the only layer protecting them. Raised to 500; the walk below is
    # now ITERATIVE so a deep structure can't blow the recursion limit.
    max_depth: int = 500,
    # CHG-0150: was 100_000 — a wide result with a redaction target beyond the 100k-th node
    # had the walk STOP early → the target egressed RAW. The gateway's _MCP_MAX_RESULT_NODES
    # guard (1M) blocks wider results upstream on the chat path; raised here (2M margin) so any
    # result that reaches this redactor is FULLY walked. (Defense-in-depth for the control path.)
    max_nodes: int = 2_000_000,
) -> Any:
    """Return a deep-copied version of ``obj`` with matching keys redacted.

    Walks dicts and lists recursively. Whenever a dict key matches (after
    NFKC + casefold normalization) any name in ``fields``, the value at
    that key is replaced with ``placeholder``. Other keys, list items,
    and scalar values are preserved.

    Bounded by ``max_depth`` and ``max_nodes`` as belt-and-suspenders
    against pathological inputs (deeply nested or huge fan-out
    responses); on overflow the traversal stops and returns what was
    redacted so far rather than raising — the goal is safety, not strict
    correctness on adversarial payloads.

    Caller-supplied ``obj`` is **not** mutated; a deep copy is made up
    front so the original tool response is preserved for audit.
    """
    if not fields or not isinstance(obj, (dict, list)):
        return obj

    targets = {_normalize_key(f) for f in fields if isinstance(f, str)}
    targets.discard("")
    if not targets:
        return obj

    result = copy.deepcopy(obj)
    node_count = 0

    # CHG-0148: ITERATIVE walk (explicit stack) so max_depth can safely be 500 without a
    # RecursionError (which would propagate to the caller and fail-OPEN, un-redacted).
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
                if _normalize_key(k) in targets:
                    node[k] = placeholder
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

    return result


# ─────────────────────────────────────────────────────────────────────────
# MCP: direction-aware structured redaction (input args / tool response)
# ─────────────────────────────────────────────────────────────────────────


def _hint_targets_side(hint: dict[str, Any], side: str) -> bool:
    """True if a redaction hint applies to the given side (input|output)."""
    direction = (hint.get("direction") or "both").lower()
    return direction == "both" or direction == side


def _redact_string_leaves(node: Any, hints: list[dict[str, Any]], placeholder: str, depth: int = 0) -> Any:
    """Recursively apply ``apply_redaction`` (regex/keyword) to every string
    leaf in a dict/list/str structure. Used for scope='entire' rules where
    the operator wants the pattern scrubbed anywhere it appears."""
    if depth > 10:
        return node
    if isinstance(node, str):
        return apply_redaction(node, hints, placeholder=placeholder)
    if isinstance(node, dict):
        return {k: _redact_string_leaves(v, hints, placeholder, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_string_leaves(v, hints, placeholder, depth + 1) for v in node]
    return node


def redact_structured(
    obj: Any,
    hints: list[dict[str, Any]],
    side: str,
    *,
    placeholder: str = DEFAULT_REDACTION_PLACEHOLDER,
) -> Any:
    """Redact a structured payload (dict/list/str) using engine hints.

    ``side`` is ``"input"`` (tool arguments, pre-call) or ``"output"``
    (tool response, post-call). Only hints whose direction includes the
    side are applied.

    Two granularities:
      * scope='key'    → the value under the named key is replaced wholesale
                         (reuses ``apply_field_redaction``).
      * scope='entire' → the hint's regex/keywords are scrubbed from every
                         string leaf in the structure.

    The input ``obj`` is never mutated; a redacted copy is returned.
    """
    relevant = [h for h in hints if _hint_targets_side(h, side)]
    if not relevant or obj is None:
        return obj

    result = copy.deepcopy(obj)

    # scope='key' — replace the whole value at the named field.
    for hint in relevant:
        if (hint.get("scope") or "entire") == "key" and hint.get("key"):
            repl = (hint.get("config") or {}).get("replacement") or placeholder
            result = apply_field_redaction(result, [hint["key"]], placeholder=repl)

    # scope='entire' — regex/keyword scrub across all string leaves.
    leaf_hints = [h for h in relevant if (h.get("scope") or "entire") != "key"]
    if leaf_hints:
        if isinstance(result, str):
            result = apply_redaction(result, leaf_hints, placeholder=placeholder)
        else:
            result = _redact_string_leaves(result, leaf_hints, placeholder)

    return result


