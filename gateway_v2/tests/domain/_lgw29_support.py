"""Shared builders for the GW29 domain tests (no signing: the parsers only read `body`)."""

from __future__ import annotations

import json

from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, Version


def ks_record(key: str, body: dict[str, object] | bytes, *, feed_seq: int = 7) -> SignedRecord:
    raw = body if isinstance(body, bytes) else json.dumps(body, allow_nan=True).encode()
    return SignedRecord(
        StateKind.KS, key, Version(1, 1), feed_seq, "h", False, StateOp.PUT, "s", raw,
    )
