# Feature: gw29-killswitch-isolation, Property 9: a refusal charges nothing and calls no provider
# Validates: Requirements 2.1, 2.2, 6.1 (phase 4b runs before the admission slot and the budget)
"""P9 over random requests: a phase-4b refusal holds no slot, spends nothing, opens no provider."""

from __future__ import annotations

import asyncio
import random

from tests.edge._lgw29_app import ENDPOINTS, ORG, Harness

SEED = 29009
ITERATIONS = 300
POOL = (
    "global",
    f"org:{ORG}",
    "org:zz",
    "model:gpt-test",
    "model:zz",
    f"om:{ORG}:gpt-test",
    "om:zz:gpt-test",
    f"om:{ORG}:zz",
)
APPLIES = {"global", f"org:{ORG}", "model:gpt-test", f"om:{ORG}:gpt-test"}


def test_property_9_refusal_holds_no_slot_spends_nothing_and_calls_no_provider() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        scopes = rng.sample(POOL, rng.randint(0, 4))
        path, build = rng.choice(ENDPOINTS)
        stream = bool(rng.getrandbits(1))
        tag = f"seed={SEED} i={i} scopes={scopes} path={path} stream={stream}"
        h = Harness()
        if scopes:
            h.engage(*scopes)
        result = asyncio.run(h.call(path, build(stream=stream)))
        if APPLIES & set(scopes):
            assert result.status == 503, tag
            assert (h.admission.admitted, h.budget.spent, h.provider.open_calls) == (0, 0, 0), tag
            assert h.budget.refund_calls == [] and len(h.records) == 1, tag
        else:
            assert result.status == 200 and h.provider.open_calls == 1, tag
            assert h.budget.net > 0 and h.admission.completed == h.admission.admitted == 1, tag
