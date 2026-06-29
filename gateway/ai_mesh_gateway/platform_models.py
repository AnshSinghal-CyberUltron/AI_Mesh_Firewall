"""
Platform-owned Bedrock models vs org inference models.

Guard/adjudicator/Tier-2 scanners must never route through LiteLLM BYOK.
"""
from __future__ import annotations

import os
import re

DEFAULT_HAIKU_45 = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# Collapse any run of whitespace / underscore / hyphen into a single hyphen so
# that display, slug, and snake/kebab variants of the same reserved name fold to
# one canonical token. Deliberately narrow — only separator characters are
# folded, so distinct tenant names (e.g. "gemma-free") never collide.
_NAME_SEPARATOR_RUN = re.compile(r"[\s_-]+")


def _canonical_model_name(name: str) -> str:
    """Lowercase, trim, and fold separator runs to a single hyphen.

    'ZeroShield Model', 'zeroshield_model', and 'zeroshield-model' all canonicalize
    to 'zeroshield-model'. Leading/trailing separators are stripped after folding.
    """
    folded = _NAME_SEPARATOR_RUN.sub("-", (name or "").strip().lower())
    return folded.strip("-")


def guard_model_names() -> frozenset[str]:
    """Logical guard names — display / pipeline trace only, not LiteLLM routes.

    Returned in canonical form (see ``_canonical_model_name``) so that display,
    slug, and snake/kebab variants of a reserved name are all recognized.
    """
    raw = {
        os.getenv("ZEROSHIELD_GUARD_MODEL_NAME", "zeroshield-model"),
        # Current user-facing platform model name: canonical slug + the display
        # form ("ZeroShield Model"). Both fold to 'zeroshield-model'.
        "zeroshield-model",
        "ZeroShield Model",
    }
    return frozenset(c for n in raw if (c := _canonical_model_name(n)))


def is_platform_model_name(name: str) -> bool:
    """True when a model alias is reserved for platform ML, not org inference."""
    normalized = (name or "").strip().lower()
    if not normalized:
        return False
    # Canonicalize (fold whitespace/underscore/hyphen) before the reserved-name
    # check so the display name "ZeroShield Model", the slug "zeroshield-model",
    # and "zeroshield_model" all match — a tenant cannot reference the platform
    # model by an alternate spelling to bypass the reservation.
    if _canonical_model_name(normalized) in guard_model_names():
        return True
    # Strip a leading LiteLLM/provider routing prefix (e.g. "bedrock/") before
    # the reserved-id check. Without this a prefixed reserved id such as
    # "bedrock/global.anthropic.claude-haiku-4-5..." slips past the guard and
    # the platform Haiku could be routed as org inference.
    bare = normalized.split("/", 1)[1] if normalized.startswith("bedrock/") else normalized
    return is_bedrock_model_id(bare)


def is_bedrock_model_id(name: str) -> bool:
    """True for AWS Bedrock foundation model IDs (not org LiteLLM aliases)."""
    normalized = (name or "").strip().lower()
    prefixes = (
        "anthropic.",
        "global.anthropic",
        "global.amazon",
        "amazon.",
        "meta.",
        "openai.",
        "cohere.",
        "ai21.",
        "mistral.",
    )
    return any(normalized.startswith(p) for p in prefixes)


def default_tier2_scanner_model() -> str:
    return (
        os.getenv("BEDROCK_TIER2_SCANNER_MODEL", "").strip()
        or os.getenv("BEDROCK_MODEL", "").strip()
        or DEFAULT_HAIKU_45
    )


def default_adjudicator_model() -> str:
    return (
        os.getenv("BEDROCK_ADJUDICATOR_MODEL", "").strip()
        or default_tier2_scanner_model()
    )


def resolve_platform_bedrock_model(
    role: str,
    requested: str | None = None,
) -> str:
    """Resolve env-backed Bedrock model ID for platform call sites."""
    candidate = (requested or "").strip()
    if candidate and is_bedrock_model_id(candidate):
        return candidate
    if role == "adjudicator":
        return default_adjudicator_model()
    return default_tier2_scanner_model()


def uses_converse_api(model_id: str) -> bool:
    """Models that should use bedrock-runtime Converse instead of invoke_model."""
    mid = (model_id or "").lower()
    return mid.startswith(("anthropic.", "global.anthropic", "global.amazon", "amazon.nova"))
