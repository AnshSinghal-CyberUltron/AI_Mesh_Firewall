"""Owner locks from the v3 initialization. Later cards import these; they do not re-decide them."""

from __future__ import annotations

from enum import StrEnum

# §0.7-2, GW05b. Freshness bound and Postgres ride-through.
FRESH_MS: int = 5_000
PG_GRACE_MS: int = 16_000

# §0.7-4. These classes may exceed the 3-token hold cap. GW12b implements the scanner.
RELAXED_HOLDBACK_CLASSES: frozenset[str] = frozenset(
    {"uuid", "sha256", "url", "base64"},
)


class InFlightKill(StrEnum):
    """§0.7-1. A stream already started when the org is killed."""

    CUT_NEXT_CHUNK = "cut_next_chunk"
