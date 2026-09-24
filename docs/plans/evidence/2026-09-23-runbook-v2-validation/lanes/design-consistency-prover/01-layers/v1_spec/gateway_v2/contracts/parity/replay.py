"""Replay C2 through a disposition oracle with a frozen clock."""

from __future__ import annotations

import json
from collections.abc import Callable

from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.types import C2Record, ReplayOutcome

Oracle = Callable[[str], str]


def replay(record: C2Record, clock: FrozenClock, oracle: Oracle) -> ReplayOutcome:
    disposition = oracle(record.prompt)
    created = clock.now()
    client_id = f"cmp-{record.request_id}"
    if disposition == "block":
        payload = {
            "id": client_id,
            "object": "error",
            "created": created,
            "model": "t02-recorder",
            "error": {
                "message": "blocked",
                "type": "invalid_request_error",
                "code": "content_filter",
            },
        }
        transformations: tuple[str, ...] = ("withhold",)
    else:
        payload = {
            "id": client_id,
            "object": "chat.completion",
            "created": created,
            "model": "t02-recorder",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                },
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9},
        }
        transformations = ()
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return ReplayOutcome(
        request_id=record.request_id,
        disposition=disposition,
        transformations=transformations,
        provider_bytes=raw,
        client_object=str(payload["object"]),
        client_model=str(payload["model"]),
        client_id=client_id,
        created=created,
    )


def replay_twice(
    records: tuple[C2Record, ...],
    clock: FrozenClock,
    oracle: Oracle,
) -> tuple[tuple[ReplayOutcome, ...], tuple[ReplayOutcome, ...]]:
    first = tuple(replay(rec, clock, oracle) for rec in records)
    second = tuple(replay(rec, clock, oracle) for rec in records)
    return first, second
