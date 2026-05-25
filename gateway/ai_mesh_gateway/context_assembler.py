"""
Context Assembler: token-budget-based message pruning and field-level
sensitivity redaction for the Gateway.

Enforces least-privilege context by:
1. Redacting sensitive fields in structured (JSON) content based on
   the caller's data sensitivity clearance level.
2. Trimming conversation history to stay within a per-key
   ``max_context_tokens`` budget.

All functions are synchronous and CPU-only (no I/O) -- safe to call
from the async hot path without an executor.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

LOG = logging.getLogger("gateway.context_assembler")

try:
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
    _USE_TIKTOKEN = True
except Exception:
    _ENCODER = None
    _USE_TIKTOKEN = False

# ---- Sensitivity levels (higher = more restricted) ----
SENSITIVITY_LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

# ---- Field sensitivity map ----
FIELD_SENSITIVITY_MAP: Dict[str, str] = {
    "ssn": "restricted",
    "social_security": "restricted",
    "social_security_number": "restricted",
    "credit_card": "restricted",
    "credit_card_number": "restricted",
    "password": "restricted",
    "secret": "restricted",
    "api_key": "restricted",
    "bank_account": "restricted",
    "routing_number": "restricted",
    "medical_record": "restricted",
    "diagnosis": "restricted",
    "private_key": "restricted",
    "token": "confidential",
    "email": "confidential",
    "phone": "confidential",
    "phone_number": "confidential",
    "address": "confidential",
    "date_of_birth": "confidential",
    "dob": "confidential",
    "salary": "confidential",
    "ip_address": "confidential",
    "name": "internal",
    "full_name": "internal",
    "first_name": "internal",
    "last_name": "internal",
    "employee_id": "internal",
    "user_id": "internal",
}

_JSON_BLOCK_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for a text string.

    Uses tiktoken (cl100k_base) when available for accuracy,
    otherwise falls back to a fast ``len(text) // 4`` heuristic.
    """
    if not text:
        return 0
    if _USE_TIKTOKEN and _ENCODER is not None:
        return len(_ENCODER.encode(text))
    return len(text) // 4


def _message_tokens(msg: Dict[str, Any]) -> int:
    """Estimate tokens for a single chat message (role + content)."""
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = " ".join(
            c.get("text", str(c)) for c in content if isinstance(c, dict)
        )
    role = msg.get("role") or ""
    return estimate_tokens(role) + estimate_tokens(content) + 4


def prune_messages(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> List[Dict[str, Any]]:
    """
    Sliding-window pruner that respects a token budget.

    Strategy:
    1. Always keep the first system message (if present).
    2. Always keep the latest user message.
    3. Drop oldest non-system messages until the total fits
       within ``max_tokens``.
    4. If ``max_tokens <= 0``, return messages unchanged (no limit).
    """
    if max_tokens <= 0 or not messages:
        return messages

    system_msgs: List[Dict[str, Any]] = []
    other_msgs: List[Dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            other_msgs.append(msg)

    if not other_msgs:
        return messages

    latest_user_msg = None
    latest_user_idx = None
    for i in range(len(other_msgs) - 1, -1, -1):
        if other_msgs[i].get("role") == "user":
            latest_user_msg = other_msgs[i]
            latest_user_idx = i
            break

    reserved_tokens = sum(_message_tokens(m) for m in system_msgs)
    if latest_user_msg is not None:
        reserved_tokens += _message_tokens(latest_user_msg)

    if reserved_tokens >= max_tokens:
        result = system_msgs + ([latest_user_msg] if latest_user_msg else [])
        LOG.warning(
            "Context budget exhausted by system+latest user message alone "
            "(%d tokens vs %d budget). Returning minimal context.",
            reserved_tokens,
            max_tokens,
        )
        return result

    remaining_budget = max_tokens - reserved_tokens
    middle_msgs = [
        m for i, m in enumerate(other_msgs) if i != latest_user_idx
    ]

    kept: List[Dict[str, Any]] = []
    for msg in reversed(middle_msgs):
        msg_cost = _message_tokens(msg)
        if msg_cost <= remaining_budget:
            kept.append(msg)
            remaining_budget -= msg_cost
        else:
            break

    kept.reverse()

    result = system_msgs + kept
    if latest_user_msg is not None:
        result.append(latest_user_msg)

    dropped = len(messages) - len(result)
    if dropped > 0:
        LOG.info(
            "Context pruned: %d messages dropped to fit %d token budget "
            "(%d messages retained)",
            dropped,
            max_tokens,
            len(result),
        )

    return result


def minimize_context(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    max_sensitivity: str = "",
) -> List[Dict[str, Any]]:
    """
    Orchestrator: apply field-level redaction then token-budget pruning.

    If ``max_sensitivity`` is set and not "restricted", structured JSON
    fields in messages are redacted based on the caller's clearance.
    If ``max_tokens <= 0``, token pruning is skipped.
    """
    result = messages
    if max_sensitivity and max_sensitivity != "restricted":
        result = redact_messages(result, max_sensitivity)
    if max_tokens > 0:
        result = prune_messages(result, max_tokens)
    return result


# ---- Field-level redaction ----


def _redact_dict(obj: dict, max_level: int) -> dict:
    """Recursively redact dictionary fields exceeding clearance level."""
    result = {}
    for key, value in obj.items():
        key_lower = key.lower().replace("-", "_").replace(" ", "_")
        field_sensitivity = FIELD_SENSITIVITY_MAP.get(key_lower)
        if (
            field_sensitivity
            and SENSITIVITY_LEVELS.get(field_sensitivity, 0) > max_level
        ):
            result[key] = f"[REDACTED:{key}]"
        elif isinstance(value, dict):
            result[key] = _redact_dict(value, max_level)
        elif isinstance(value, list):
            result[key] = [
                _redact_dict(item, max_level) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def _replace_json_match(match: re.Match, max_level: int) -> str:
    """Replace a JSON match with redacted version."""
    try:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict):
            redacted = _redact_dict(obj, max_level)
            return json.dumps(redacted)
    except (json.JSONDecodeError, TypeError):
        pass
    return match.group(0)


def redact_structured_fields(
    content: str,
    max_sensitivity: str = "public",
) -> str:
    """
    Detect JSON objects in message content and redact fields
    whose sensitivity exceeds ``max_sensitivity``.
    """
    max_level = SENSITIVITY_LEVELS.get(max_sensitivity, 0)
    return _JSON_BLOCK_RE.sub(
        lambda m: _replace_json_match(m, max_level), content
    )


def redact_messages(
    messages: List[Dict[str, Any]],
    max_sensitivity: str = "public",
) -> List[Dict[str, Any]]:
    """Apply field-level redaction to all messages in a conversation."""
    if max_sensitivity == "restricted":
        return messages

    result: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            redacted = redact_structured_fields(content, max_sensitivity)
            result.append({**msg, "content": redacted})
        else:
            result.append(msg)
    return result
