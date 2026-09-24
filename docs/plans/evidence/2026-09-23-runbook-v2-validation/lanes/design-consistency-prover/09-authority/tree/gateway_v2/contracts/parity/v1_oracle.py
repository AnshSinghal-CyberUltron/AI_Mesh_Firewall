"""v1 Tier-1 oracle — imports ATTACK_PATTERNS, never edits v1 modules."""

from __future__ import annotations

import re
from functools import cache


@cache
def _compiled() -> tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]:
    from ai_mesh_gateway.scanner import ATTACK_PATTERNS

    rows: list[tuple[str, tuple[re.Pattern[str], ...]]] = []
    for category, patterns in ATTACK_PATTERNS.items():
        compiled = tuple(re.compile(item, re.IGNORECASE) for item in patterns)
        rows.append((str(category), compiled))
    return tuple(rows)


def v1_tier1_categories(text: str) -> tuple[str, ...]:
    hits: list[str] = []
    for category, patterns in _compiled():
        if any(pat.search(text) for pat in patterns):
            hits.append(category)
    return tuple(hits)


def v1_disposition(text: str) -> str:
    return "block" if v1_tier1_categories(text) else "allow"
