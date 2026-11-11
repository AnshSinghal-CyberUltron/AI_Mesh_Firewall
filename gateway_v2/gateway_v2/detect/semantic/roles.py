"""Role axis and pure role admission for semantic detection (GW10).

This module defines the GW10-local message-role axis and the pure role-admission
algebra (Resolution 2). The shipped ``DetectorContext(phase, rules, budget)``
carries no message-role field -- GW04 built it minimal, and the C21 full channel
typing plus the real request-message -> role-segment folding are a GW04/GW15
concern. GW10 therefore defines its OWN typed role input: a sequence of
role-tagged ``RoleScopedSegment`` values that the semantic detector windows and
classifies with role awareness, applying each rule only to the roles the rule's
scope admits (user input by default; system/assistant history excluded unless the
rule opts in).

Everything here is a pure function of its inputs: no clock, no I/O, no mutation.
``admit_segments`` is the pure core of Requirement 5.4 and Property P7 -- a rule
scoped to user input never fires on a system/assistant-only input.

Layering: this module imports only the standard library. It never imports a
higher layer (``edge`` / ``admit`` / ``plan``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = (
    "DEFAULT_ROLE_SCOPE",
    "RoleScope",
    "RoleScopedSegment",
    "SegmentRole",
    "admit_segments",
)


class SegmentRole(StrEnum):
    """The GW10-local message-role axis (the shipped domain carries none)."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class RoleScopedSegment:
    """A single role-tagged input segment -- the typed role input (Resolution 2)."""

    role: SegmentRole
    text: str


# A rule's role-admission set: the message roles a semantic rule is allowed to
# fire on. The default admits user input only, so system prompts and assistant
# history are not treated as user input unless a rule opts a role in.
RoleScope = frozenset[SegmentRole]

DEFAULT_ROLE_SCOPE: Final[RoleScope] = frozenset({SegmentRole.USER})


def admit_segments(
    segments: Sequence[RoleScopedSegment],
    role_scope: RoleScope = DEFAULT_ROLE_SCOPE,
) -> tuple[RoleScopedSegment, ...]:
    """Return only the segments whose role is in ``role_scope``.

    Pure filter: input order is preserved, inputs are not mutated, and no segment
    outside the admitted scope is returned.
    """

    return tuple(segment for segment in segments if segment.role in role_scope)
