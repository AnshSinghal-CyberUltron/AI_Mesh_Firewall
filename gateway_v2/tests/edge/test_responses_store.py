# Feature: gw15-responses-close, Property 4: Store keys do not cross orgs
# Validates: Requirements 8.1, 8.4, 8.6, 8.7
"""Org-scoped Responses store.

House idiom: a seeded ``random.Random``, no ``hypothesis``, at least 10,000 iterations.
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest

from gateway_v2.edge.responses_store import (
    MemoryResponseStore,
    StoredResponse,
    ValkeyResponseStore,
)
from gateway_v2.runtime.store_keys import KEYS

_SEED = 0x6C15_0005
_ITERATIONS = 10_000
_FIREWALL_PLAN_KEY = "{rv2}:dev:firewall-plan"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{key}={value!r}" for key, value in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _record(response_id: str, text: str = "hello") -> StoredResponse:
    return StoredResponse(
        response={"id": response_id, "object": "response", "output_text": text},
        replay_messages=({"role": "user", "content": "hi"}, {"role": "assistant", "content": text}),
        input_items=({"type": "message", "role": "user", "content": "hi"},),
    )


def test_property_store_keys_do_not_cross_orgs() -> None:
    """Property 4: another org's get and delete never see or remove this org's record.

    Validates: Requirements 8.4, 8.7
    """
    rng = random.Random(_SEED)

    async def run() -> None:
        store = MemoryResponseStore()
        for i in range(_ITERATIONS):
            org_a = f"org-{rng.randrange(50)}"
            org_b = f"org-{rng.randrange(50)}"
            response_id = f"resp_{rng.randrange(1_000_000):06d}"
            record = _record(response_id)
            assert await store.put(org_a, record), _ctx(_SEED, i, org=org_a)
            if org_b != org_a:
                assert await store.get(org_b, response_id) is None, _ctx(
                    _SEED, i, org_a=org_a, org_b=org_b
                )
                assert not await store.delete(org_b, response_id), _ctx(_SEED, i)
            assert await store.get(org_a, response_id) == record, _ctx(_SEED, i)
            assert await store.delete(org_a, response_id), _ctx(_SEED, i)
            assert await store.get(org_a, response_id) is None, _ctx(_SEED, i)

    asyncio.run(run())


def test_put_refuses_a_record_without_a_resp_id() -> None:
    """A write the store cannot key returns False and leaves nothing to read."""

    async def run() -> None:
        store = MemoryResponseStore()
        assert not await store.put("org-a", _record("chatcmpl-1"))
        assert not await store.put("", _record("resp_1"))
        assert await store.get("org-a", "chatcmpl-1") is None

    asyncio.run(run())


def test_valkey_store_round_trip_and_org_scope() -> None:
    """The Valkey store keys by org, applies the TTL, and leaves the firewall plan key alone."""
    fakeredis = pytest.importorskip("fakeredis")

    async def run() -> None:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        await client.set(_FIREWALL_PLAN_KEY, "saved-settings")
        store = ValkeyResponseStore(client, ttl_s=60)
        record = _record("resp_abc")
        assert await store.put("org-a", record)
        key = KEYS.response("org-a", "resp_abc")
        assert key == "{rv2}:responses:org-a:resp_abc"
        ttl = await client.ttl(key)
        assert 0 < ttl <= 60
        stored = json.loads(await client.get(key))
        assert stored["org_id"] == "org-a"
        got = await store.get("org-a", "resp_abc")
        assert got is not None
        assert got.response["output_text"] == "hello"
        assert got.replay_messages[1] == {"role": "assistant", "content": "hello"}
        assert len(got.input_items) == 1
        assert await store.get("org-b", "resp_abc") is None
        assert not await store.delete("org-b", "resp_abc")
        assert await store.delete("org-a", "resp_abc")
        assert await store.get("org-a", "resp_abc") is None
        assert await client.get(_FIREWALL_PLAN_KEY) == "saved-settings"

    asyncio.run(run())


def test_valkey_store_rejects_a_record_written_under_another_org() -> None:
    """A record whose stored org differs from the key's org is treated as missing."""
    fakeredis = pytest.importorskip("fakeredis")

    async def run() -> None:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        body = json.dumps({"org_id": "org-b", "response": {"id": "resp_x"}})
        await client.set(KEYS.response("org-a", "resp_x"), body)
        store = ValkeyResponseStore(client, ttl_s=60)
        assert await store.get("org-a", "resp_x") is None

    asyncio.run(run())


class _BrokenClient:
    async def set(self, *args: object, **kwargs: object) -> object:
        raise ConnectionError("down")

    async def get(self, *args: object, **kwargs: object) -> object:
        raise ConnectionError("down")

    async def delete(self, *args: object, **kwargs: object) -> object:
        raise ConnectionError("down")


def test_valkey_store_failure_is_observable() -> None:
    """A client error is a False put and a None get. It never reads as stored."""

    async def run() -> None:
        store = ValkeyResponseStore(_BrokenClient(), ttl_s=60)
        assert not await store.put("org-a", _record("resp_1"))
        assert await store.get("org-a", "resp_1") is None
        assert not await store.delete("org-a", "resp_1")

    asyncio.run(run())


def test_valkey_store_rejects_a_zero_ttl() -> None:
    with pytest.raises(ValueError):
        ValkeyResponseStore(_BrokenClient(), ttl_s=0)
