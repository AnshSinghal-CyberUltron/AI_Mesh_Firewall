"""Expected-diff ledger. Git commit time beats a back-dated field."""

from __future__ import annotations

import json
from pathlib import Path

from gateway_v2.contracts.parity.types import LedgerEntry

_DEFAULT = Path(__file__).resolve().parent / "expected_diff_ledger.json"


class LedgerTimestampError(RuntimeError):
    """Entry would excuse a run that predates the real commit."""


def load_ledger(path: Path | None = None) -> tuple[LedgerEntry, ...]:
    blob = json.loads((path or _DEFAULT).read_text(encoding="utf-8"))
    raw = blob.get("entries") if isinstance(blob, dict) else None
    if not isinstance(raw, list):
        raise ValueError("ledger entries must be a list")
    out: list[LedgerEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("ledger entry must be an object")
        out.append(
            LedgerEntry(
                rule_id=str(item["rule_id"]),
                row_id=str(item["row_id"]),
                c3_score=str(item["c3_score"]),
                diff_signature=str(item["diff_signature"]),
                commit_timestamp=int(item["commit_timestamp"]),
            ),
        )
    return tuple(out)


def timestamp_ok(
    entry: LedgerEntry,
    run_started_at: int,
    git_commit_time: int | None,
) -> None:
    claimed = entry.commit_timestamp
    if claimed > run_started_at:
        raise LedgerTimestampError(
            f"{entry.rule_id} commit_timestamp {claimed} post-dates run {run_started_at}",
        )
    if git_commit_time is None:
        return
    if git_commit_time > run_started_at:
        raise LedgerTimestampError(
            f"{entry.rule_id} git commit {git_commit_time} post-dates run {run_started_at}",
        )
    if claimed != git_commit_time:
        raise LedgerTimestampError(
            f"{entry.rule_id} back-dated field {claimed} != git {git_commit_time}",
        )


def lookup(
    signature: str,
    ledger: tuple[LedgerEntry, ...],
    run_started_at: int,
    git_commit_time: int | None,
) -> LedgerEntry | None:
    for entry in ledger:
        if entry.diff_signature != signature:
            continue
        timestamp_ok(entry, run_started_at, git_commit_time)
        return entry
    return None
