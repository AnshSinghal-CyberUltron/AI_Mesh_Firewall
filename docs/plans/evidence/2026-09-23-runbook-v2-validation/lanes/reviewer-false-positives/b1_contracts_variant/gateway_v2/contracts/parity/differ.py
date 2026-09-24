"""Four-bucket C2 differ. WIRE is never excused by the ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass

from gateway_v2.contracts.parity.ledger import lookup
from gateway_v2.contracts.parity.types import SDK_FIELDS, LedgerEntry, ReplayOutcome


@dataclass(frozen=True)
class Classification:
    bucket: str
    reason: str
    rule_id: str | None
    c1_gap: str | None


class DifferFail(RuntimeError):
    """UNEXPECTED or WIRE — the GW02 run must fail."""


def diff_signature(left: ReplayOutcome, right: ReplayOutcome) -> str:
    return (
        f"disp:{left.disposition}->{right.disposition}"
        f"|tx:{','.join(left.transformations)}->{','.join(right.transformations)}"
    )


def _parse(raw: bytes) -> dict[str, object]:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def sdk_delta(left: ReplayOutcome, right: ReplayOutcome) -> tuple[str, ...]:
    changed: list[str] = []
    named = (
        ("object", left.client_object, right.client_object),
        ("model", left.client_model, right.client_model),
        ("id", left.client_id, right.client_id),
        ("created", str(left.created), str(right.created)),
    )
    for key, a, b in named:
        if a != b:
            changed.append(key)
    parsed_l = _parse(left.provider_bytes)
    parsed_r = _parse(right.provider_bytes)
    for key in sorted(SDK_FIELDS):
        if key in changed:
            continue
        if parsed_l.get(key) != parsed_r.get(key):
            changed.append(key)
    return tuple(changed)


def _identical(left: ReplayOutcome, right: ReplayOutcome) -> bool:
    return (
        left.disposition == right.disposition
        and left.transformations == right.transformations
        and left.provider_bytes == right.provider_bytes
        and left.client_object == right.client_object
        and left.client_model == right.client_model
        and left.client_id == right.client_id
        and left.created == right.created
    )


def classify(
    left: ReplayOutcome,
    right: ReplayOutcome,
    ledger: tuple[LedgerEntry, ...],
    run_started_at: int,
    git_commit_time: int | None = None,
) -> Classification:
    if _identical(left, right):
        return Classification("IDENTICAL", "byte-stable replay", None, None)
    sdk = sdk_delta(left, right)
    if sdk:
        gap = f"C1 gap: SDK field(s) {','.join(sdk)} drifted"
        return Classification("WIRE", gap, None, gap)
    signature = diff_signature(left, right)
    entry = lookup(signature, ledger, run_started_at, git_commit_time)
    if entry is not None:
        return Classification("EXPECTED", entry.row_id, entry.rule_id, None)
    return Classification("UNEXPECTED", signature, None, None)


def require_clean(result: Classification) -> None:
    if result.bucket in {"UNEXPECTED", "WIRE"}:
        raise DifferFail(f"{result.bucket}: {result.reason}")
