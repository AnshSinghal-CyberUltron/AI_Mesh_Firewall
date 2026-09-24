"""Signed rule priority, no severity max — rb.md L2159, L2224."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_v2.plan.model import Rule

# L2196 Rule.priority 'conflicts resolve by this, never by severity'


def by_priority(rules: Sequence[Rule]) -> tuple[Rule, ...]:
    return tuple(sorted(rules, key=lambda r: (-r.priority, r.rule_id)))
