"""GW01 Expected_Diff_Ledger: the deliberate v2-vs-v1 wire behaviour changes.

This module is the frozen, declarative record of the deliberate v2-vs-v1 behaviour
changes enumerated in runbook §10.2.2, plus the frozen OpenAI SDK version provenance
the conformance suite is pinned against (Requirements 6.1, 4.4).

The ported OpenAI conformance suite "IS the specification of gateway-v2's public
surface". A handful of §10.2.2 rows are *deliberate* departures from v1's old
behaviour, not conformance failures. A conformance cell that observes one of these
consults the ledger (through the App_Resolver's resolver-aware reader, added in a
sibling module) and asserts the value expected for the app currently selected, so the
identical cell passes under both apps and the deliberate difference is *recorded*,
never silently suppressed.

Layering: this is product code in the lowest-but-one ``contracts/`` layer. It imports
**only the standard library** — no higher layer, no test code. The behaviour tokens are
short, stable, serializable sentinels; the frozen mapping is exposed exclusively through
:data:`LEDGER` / :func:`ledger` as an immutable :class:`~types.MappingProxyType` view, so
there is no module-level mutable binding. The ledger holds behaviour expectations and
version strings only — never a capacity (pool/queue/worker) number.

The resolver reader ``expected_diff(key)`` is intentionally NOT defined here; it lives on
the App_Resolver (``harness.py``) so a cell consults the ledger through the one entry
point that already knows which app is selected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = (
    "ExpectedDiff",
    "SDK_FROZEN_VERSION",
    "SDK_INSTALLED_FLOOR",
    "LEDGER",
    "ledger",
)


@dataclass(frozen=True, slots=True)
class ExpectedDiff:
    """One deliberate v2-vs-v1 behaviour change from runbook §10.2.2.

    ``key`` is the stable ledger key a cell passes to the resolver's ``expected_diff``
    reader; ``v1`` is the value a ``v1`` run expects (the old behaviour); ``v2`` is the
    value a ``v2`` run expects (the deliberate §10.2.2 behaviour); ``runbook_row`` names
    the §10.2.2 row this entry encodes, for provenance.
    """

    key: str
    v1: object
    v2: object
    runbook_row: str


# Frozen OpenAI SDK version provenance (single source of truth; Requirement 4.4).
#: The SDK version the frozen wire contract was originally frozen against.
SDK_FROZEN_VERSION: Final[str] = "2.38.0"
#: The stock SDK major floor the suite must prove still works against the live venv.
SDK_INSTALLED_FLOOR: Final[str] = "3.29.0"


# The seven §10.2.2 rows. Behaviour values are short, lowercase, serializable sentinel
# tokens — distinct, frozen, and documented — so wave-4 conformance cells can assert the
# resolver-selected ``.v1`` / ``.v2`` value directly. The exact tokens matter less than
# their being distinct and consistent with how the design describes each row.
LEDGER: Final[Mapping[str, ExpectedDiff]] = MappingProxyType(
    {
        "injection_detection": ExpectedDiff(
            key="injection_detection",
            v1="block_on_match",
            v2="finding_published_recall_fpr",
            runbook_row=(
                "§10.2.2: injection/jailbreak measured against corpus, "
                "published recall/FPR (measured gate deferred)"
            ),
        ),
        "benign_inline_code": ExpectedDiff(
            key="benign_inline_code",
            v1="terminal_block",
            v2="signal_request_completes",
            runbook_row="§10.2.2: benign inline code -> SIGNAL finding, not terminal block",
        ),
        "degraded_scan_status": ExpectedDiff(
            key="degraded_scan_status",
            v1="implicit_legacy",
            v2="explicit_unavailable",
            runbook_row=(
                "§10.2.2: degraded scan -> explicit UNAVAILABLE finding status "
                "with signed fail posture"
            ),
        ),
        "policy_precedence": ExpectedDiff(
            key="policy_precedence",
            v1="severity_max",
            v2="signed_priority",
            runbook_row=(
                "§10.2.2: org-policy precedence by signed rule priority "
                "(C22 most-restrictive), not severity max"
            ),
        ),
        "tri_state_one_plan": ExpectedDiff(
            key="tri_state_one_plan",
            v1="per_surface",
            v2="one_plan_all_surfaces",
            runbook_row="§10.2.2: unset tri-state -> one plan, identical on every surface",
        ),
        "plan_unavailable": ExpectedDiff(
            key="plan_unavailable",
            v1="implicit_error",
            v2="explicit_plan_unavailable",
            runbook_row="§10.2.2: missing tenant config -> explicit PLAN_UNAVAILABLE",
        ),
        "stream_block_disposition": ExpectedDiff(
            key="stream_block_disposition",
            v1="truncate_after_partial",
            v2="withhold_before_first_byte",
            runbook_row=(
                "§10.2.2: streaming block -> withhold before first byte / strict mode, "
                "not truncate after partial emit"
            ),
        ),
    }
)


def ledger() -> Mapping[str, ExpectedDiff]:
    """Return the frozen Expected_Diff_Ledger as an immutable mapping view.

    A function accessor alongside :data:`LEDGER` so callers may consult the ledger
    without touching a module-level binding directly; both expose the same frozen
    :class:`~types.MappingProxyType` so the ledger cannot be mutated through either.
    """

    return LEDGER
