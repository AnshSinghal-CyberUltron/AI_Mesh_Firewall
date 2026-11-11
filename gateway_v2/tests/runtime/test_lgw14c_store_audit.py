"""GW14c phase 2: the audit key layout and the append-and-trim adapter.

The assertions that matter here are about COMMAND SHAPE, not about results:

* `XADD` must carry no `maxlen`. Capping on the append is cheaper and is what RC2 did, and it is
  unusable for this card because `XADD` does not report what it displaced. The exact trim count
  is the whole mechanism behind R2-11's M3 finding (a flush erased 36% of records while
  completeness read 1.0), so a future "optimisation" that moves the cap onto the `XADD` has to
  fail a test rather than pass review.
* One round trip per batch, whatever the batch holds. Nothing proportional to the stream length,
  no `MEMORY USAGE` per entry.
* The trim count is read back from `XTRIM`'s own answer, per org.

The adapter is also driven against a REAL Valkey 8 in `test_lgw14c_live_docker.py`; these are the
shape tests that run everywhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest

from gateway_v2.runtime.store_audit import FIELD, ValkeyAuditStore
from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, KEYS, StoreKeys


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class FakePipeline:
    """Records the commands rather than executing them. The answers are scripted per test."""

    def __init__(self, owner: FakeClient) -> None:
        self._owner = owner

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def xadd(self, key: str, fields: Mapping[str, Any], **kwargs: Any) -> None:
        self._owner.commands.append(("xadd", key, fields, kwargs))

    def xtrim(self, key: str, **kwargs: Any) -> None:
        self._owner.commands.append(("xtrim", key, None, kwargs))

    async def execute(self) -> list[object]:
        self._owner.executes += 1
        return self._owner.answers


class FakeClient:
    def __init__(self, answers: Sequence[object] | None = None) -> None:
        self.commands: list[tuple[str, str, Mapping[str, Any] | None, dict[str, Any]]] = []
        self.answers: list[object] = list(answers or [])
        self.executes = 0
        self.ranges: list[dict[str, Any]] = []
        self.range_result: list[tuple[object, object]] = []

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        self.transaction = transaction
        return FakePipeline(self)

    async def xlen(self, key: str) -> int:
        self.commands.append(("xlen", key, None, {}))
        return 7

    async def xrange(self, key: str, **kwargs: Any) -> list[tuple[object, object]]:
        self.ranges.append({"key": key, **kwargs})
        return self.range_result


def _store(answers: Sequence[object] | None = None) -> tuple[ValkeyAuditStore, FakeClient]:
    client = FakeClient(answers)
    return ValkeyAuditStore(client), client


# --- the key layout ------------------------------------------------------------------------------


def test_audit_streams_are_per_org_inside_the_namespace() -> None:
    assert KEYS.audit_stream("org-a") == f"{DEFAULT_NAMESPACE}:audit:org-a"
    assert StoreKeys("{lane7}").audit_stream("org-a") == "{lane7}:audit:org-a"


def test_audit_streams_keep_the_namespace_hash_tag() -> None:
    """A batch spanning several tenants has to stay ONE round trip on a cluster-mode store."""
    keys = [KEYS.audit_stream(org) for org in ("a", "b", "c")]
    assert all(key.startswith(DEFAULT_NAMESPACE) for key in keys)


def test_an_audit_stream_without_an_org_is_refused() -> None:
    """An unattributed record cannot be budgeted, trimmed per tenant, or exported anywhere."""
    with pytest.raises(ValueError, match="needs an org"):
        KEYS.audit_stream("")


def test_the_org_can_be_recovered_from_the_key() -> None:
    """The exporter walks keys and has to attribute each stream to a tenant."""
    assert KEYS.audit_org(KEYS.audit_stream("org-b")) == "org-b"
    assert StoreKeys("{lane7}").audit_org("{lane7}:audit:org-z") == "org-z"


def test_audit_org_ignores_keys_that_are_not_audit_streams() -> None:
    assert KEYS.audit_org(f"{DEFAULT_NAMESPACE}:meta:plan") is None
    assert KEYS.audit_org("something:else") is None
    assert KEYS.audit_org(KEYS.audit_prefix) is None


# --- command shape -------------------------------------------------------------------------------


def test_append_issues_one_xadd_per_record_and_one_xtrim_per_touched_org() -> None:
    store, client = _store(["1-1", "1-2", "1-3", 0, 0])
    result = _run(
        store.append(
            [("org-a", b"r1"), ("org-b", b"r2"), ("org-a", b"r3")],
            {"org-a": 100, "org-b": 100},
        ),
    )

    kinds = [command[0] for command in client.commands]
    assert kinds == ["xadd", "xadd", "xadd", "xtrim", "xtrim"]
    assert client.executes == 1, "one round trip per batch, whatever the batch holds"
    assert result.written == 3


def test_xadd_never_carries_a_maxlen() -> None:
    """The cap lives in XTRIM because XTRIM reports what it removed. XADD does not.

    If this test ever fails because someone moved the cap onto the append, the exact
    `audit_trimmed_records` counter has silently become an estimate, and M3 is back.
    """
    store, client = _store(["1-1", 0])
    _run(store.append([("org-a", b"r")], {"org-a": 100}))

    adds = [command for command in client.commands if command[0] == "xadd"]
    assert adds
    for _, _, fields, kwargs in adds:
        assert "maxlen" not in kwargs
        assert "approximate" not in kwargs
        assert fields == {FIELD: b"r"}


def test_xtrim_is_approximate_and_carries_the_derived_cap() -> None:
    store, client = _store(["1-1", 0])
    _run(store.append([("org-a", b"r")], {"org-a": 4242}))

    trims = [command for command in client.commands if command[0] == "xtrim"]
    assert len(trims) == 1
    assert trims[0][3] == {"maxlen": 4242, "approximate": True}


def test_append_is_not_a_transaction() -> None:
    """Nothing to make atomic: an untrimmed batch is trimmed by the next one."""
    store, client = _store(["1-1", 0])
    _run(store.append([("org-a", b"r")], {"org-a": 10}))

    assert client.transaction is False


def test_an_org_with_no_derived_cap_is_appended_and_not_trimmed() -> None:
    """When the budget is unknown, not trimming is the honest answer. Inventing a cap is not."""
    store, client = _store(["1-1"])
    result = _run(store.append([("org-a", b"r")], {}))

    assert [command[0] for command in client.commands] == ["xadd"]
    assert result.trimmed == 0
    assert result.written == 1


def test_an_empty_batch_does_not_touch_the_store() -> None:
    store, client = _store()
    result = _run(store.append([], {"org-a": 10}))

    assert client.executes == 0
    assert client.commands == []
    assert (result.written, result.trimmed, result.ids) == (0, 0, ())


# --- the exact trim count ------------------------------------------------------------------------


def test_the_trim_count_is_read_back_per_org_and_summed() -> None:
    store, _ = _store(["1-1", "1-2", 11, 5])
    result = _run(store.append([("org-a", b"x"), ("org-b", b"y")], {"org-a": 10, "org-b": 10}))

    assert result.trimmed_by_org == {"org-a": 11, "org-b": 5}
    assert result.trimmed == 16


def test_a_zero_trim_is_not_recorded_as_an_org_that_trimmed() -> None:
    store, _ = _store(["1-1", "1-2", 0, 3])
    result = _run(store.append([("org-a", b"x"), ("org-b", b"y")], {"org-a": 10, "org-b": 10}))

    assert result.trimmed_by_org == {"org-b": 3}
    assert result.trimmed == 3


def test_assigned_ids_come_back_in_batch_order() -> None:
    """The exporter's high-water mark needs them, and XADD already returns each one."""
    store, _ = _store([b"5-1", b"5-2", 0])
    result = _run(store.append([("org-a", b"x"), ("org-a", b"y")], {"org-a": 10}))

    assert result.ids == ("5-1", "5-2")


# --- the exporter's reads ------------------------------------------------------------------------


def test_read_pages_above_an_exclusive_cursor() -> None:
    """Exclusive, because the cursor names a record already made durable. Re-reading it would
    double-count it against the durable high-water mark."""
    store, client = _store()
    client.range_result = [(b"3-1", {FIELD.encode(): b"one"}), (b"3-2", {FIELD: "two"})]

    page = _run(store.read("org-a", after="2-9", limit=64))

    assert client.ranges == [
        {"key": KEYS.audit_stream("org-a"), "min": "(2-9", "max": "+", "count": 64},
    ]
    assert page == (("3-1", b"one"), ("3-2", b"two"))


def test_read_skips_an_entry_without_the_payload_field() -> None:
    store, client = _store()
    client.range_result = [(b"3-1", {"other": b"junk"}), (b"3-2", {FIELD: b"ok"})]

    assert _run(store.read("org-a")) == (("3-2", b"ok"),)


def test_first_id_is_the_oldest_surviving_record() -> None:
    """A durable cursor below this means the gap between them was trimmed before export, and
    that gap is what `records_lost` counts."""
    store, client = _store()
    client.range_result = [(b"9-4", {FIELD: b"x"})]

    assert _run(store.first_id("org-a")) == "9-4"
    assert client.ranges == [
        {"key": KEYS.audit_stream("org-a"), "min": "-", "max": "+", "count": 1},
    ]


def test_first_id_of_an_empty_stream_is_none() -> None:
    store, client = _store()
    client.range_result = []

    assert _run(store.first_id("org-a")) is None


def test_length_is_one_command() -> None:
    store, client = _store()

    assert _run(store.length("org-a")) == 7
    assert [command[0] for command in client.commands] == ["xlen"]
