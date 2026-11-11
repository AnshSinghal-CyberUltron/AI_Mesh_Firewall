"""GW06 budget-lease: the live-counter store key family on `StoreKeys`.

These are shape tests. What matters for R2-09 / GW06 is:

* each method yields the exact `{rv2}:budget:<org>:...` string documented in the design's
  "Store key layout" table (the LIVE counter family, distinct from the published
  `StateKind.BUDGET` config record);
* all three keys for one org share ONE hash-tag slot (the `{...}` segment), so the atomic
  acquire/return `EVAL` touches only keys in that slot on a cluster-mode store;
* an empty org is refused (an unattributed budget cannot be leased or counted), mirroring
  `audit_stream`.
"""

from __future__ import annotations

import re

import pytest

from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, KEYS, StoreKeys

_HASH_TAG = re.compile(r"\{[^}]*\}")


def _slot_tag(key: str) -> str:
    """The hash-tag segment the store hashes on — the text inside the first `{...}`."""
    match = _HASH_TAG.search(key)
    assert match is not None, f"{key!r} carries no hash tag"
    return match.group(0)


def test_budget_remaining_exact_key() -> None:
    assert KEYS.budget_remaining("acme") == "{rv2}:budget:acme:remaining"


def test_budget_generation_exact_key() -> None:
    assert KEYS.budget_generation("acme") == "{rv2}:budget:acme:generation"


def test_budget_lease_exact_key() -> None:
    assert KEYS.budget_lease("acme", "worker-7") == "{rv2}:budget:acme:lease:worker-7"


def test_all_budget_keys_carry_the_namespace_hash_tag() -> None:
    for key in (
        KEYS.budget_remaining("acme"),
        KEYS.budget_generation("acme"),
        KEYS.budget_lease("acme", "worker-7"),
    ):
        assert key.startswith(f"{DEFAULT_NAMESPACE}:")
        assert _slot_tag(key) == DEFAULT_NAMESPACE


def test_all_budget_keys_for_one_org_share_one_slot() -> None:
    org = "acme"
    tags = {
        _slot_tag(KEYS.budget_remaining(org)),
        _slot_tag(KEYS.budget_generation(org)),
        _slot_tag(KEYS.budget_lease(org, "worker-7")),
        _slot_tag(KEYS.budget_lease(org, "worker-8")),
    }
    assert tags == {DEFAULT_NAMESPACE}


def test_budget_keys_honour_a_custom_namespace() -> None:
    keys = StoreKeys(namespace="{tenant}")
    assert keys.budget_remaining("acme") == "{tenant}:budget:acme:remaining"
    assert keys.budget_generation("acme") == "{tenant}:budget:acme:generation"
    assert keys.budget_lease("acme", "w1") == "{tenant}:budget:acme:lease:w1"


@pytest.mark.parametrize(
    "call",
    [
        lambda: KEYS.budget_remaining(""),
        lambda: KEYS.budget_generation(""),
        lambda: KEYS.budget_lease("", "worker-7"),
    ],
)
def test_empty_org_is_refused(call) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        call()


def test_empty_worker_id_is_refused() -> None:
    with pytest.raises(ValueError):
        KEYS.budget_lease("acme", "")
