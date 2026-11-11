"""Deterministic, plan-derived upstream selection (GW12, R8.3).

The `DispatchRouter` turns a resolved `ExecutionPlan` into the string naming the upstream
destination the egress loop reads from. The one invariant the card signs for is purity: the
same plan always yields the same destination, with no clock, no randomness, no I/O and no
module-level mutable state (R8.3). That is what lets a signed safe retry/fallback (R7) re-run
routing and land on the identical upstream, and what makes the determinism property testable
without a harness.

Layer: `dispatch` sits above `egress`/`runtime`/`contracts`/`domain` and BELOW `plan` in the
import-linter contract, so this module imports `ExecutionPlan` from `domain.plan` (where the
plan vocabulary lives) and never from the `plan` compilation layer above it.

Selection key. `ExecutionPlan` carries no `model`/`provider`/`destination` field of its own —
routing is derived, not stored. The destination is a pure function of the plan's IDENTITY:
`org_id` plus the plan `version` (`epoch.sequence.content_hash[:16]`). Two plans that are the
same compiled content for the same tenant route to the same place; a new epoch, a new sequence
or a changed `content_hash` re-derives independently. `compiled_at` (a wall-clock stamp) and
`feed_seq` (a shared cursor, provenance only — see `domain.plan.is_newer`) are deliberately
EXCLUDED so the choice depends only on tenant-stable plan identity, never on time or on another
tenant's writes.
"""

from __future__ import annotations

import hashlib

from gateway_v2.domain.plan import ExecutionPlan

__all__: tuple[str, ...] = ("DispatchRouter",)

# Field separator for the canonical identity string. A control character that cannot occur in an
# org id or a hex content hash, so the join is unambiguous (no two distinct identities collide on
# a shared boundary). Not a capacity value; a formatting constant.
_IDENTITY_SEP = "\x1f"

# Width of the hex fingerprint folded into the destination label. Enough to make an accidental
# collision between two distinct plan identities negligible while keeping the label short and
# human-legible in a trace. Not a capacity/limit literal.
_FINGERPRINT_HEX_WIDTH = 16


class DispatchRouter:
    """Deterministic plan-derived upstream selector (R8.3).

    `select(plan)` is a pure function of the plan's identity: identical plan inputs always
    produce the identical destination string, every time. There is no instance state, no clock,
    no `random`, and no I/O, so the determinism is a property of the function itself rather than
    of any configuration carried on the router.
    """

    __slots__ = ()

    def select(self, plan: ExecutionPlan) -> str:
        """Return the upstream destination for ``plan``.

        Pure and deterministic: the same `ExecutionPlan` identity always maps to the same
        string. The returned label is ``amf://upstream/{org_id}/{version}#{fingerprint}`` where
        ``version`` is the plan's own `epoch.sequence.content_hash[:16]` and ``fingerprint`` is a
        stable (non-random) hex digest of the canonical identity — the digest disambiguates
        identities that could otherwise share a prefix and keeps the choice a total function of
        the plan.
        """
        identity = self._canonical_identity(plan)
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_WIDTH]
        return f"amf://upstream/{plan.org_id}/{plan.version}#{fingerprint}"

    @staticmethod
    def _canonical_identity(plan: ExecutionPlan) -> str:
        """Join the plan-identity fields into one unambiguous string.

        Only tenant-stable identity participates: `org_id`, `epoch`, `sequence`, `content_hash`.
        The separator cannot appear in any part, so distinct identities never serialize to the
        same string (injective encoding).
        """
        return _IDENTITY_SEP.join(
            (
                plan.org_id,
                str(plan.epoch),
                str(plan.sequence),
                plan.content_hash,
            ),
        )
