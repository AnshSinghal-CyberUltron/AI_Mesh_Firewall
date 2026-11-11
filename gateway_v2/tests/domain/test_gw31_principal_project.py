"""GW31 Principal.project (D-4)."""
from __future__ import annotations

import random
import string

import pytest

from gateway_v2.admit.identity import ProjectInvalid, principal_of
from gateway_v2.domain.state import StoreDataUnavailable
from tests.admit.test_lgw05c_identity import _key_record


def _body(**extra: object) -> dict[str, object]:
    return {"key_id": "k", "org_id": "o", "rate_per_s": 1, "burst": 1, **extra}


def test_absent_and_null_default() -> None:
    for body in (_body(), _body(project=None)):
        principal = principal_of(_key_record("a", body=body))
        assert principal is not None and principal.project == "default"


def test_valid_project() -> None:
    principal = principal_of(_key_record("a", body=_body(project="proj-1_a")))
    assert principal is not None and principal.project == "proj-1_a"


@pytest.mark.parametrize("bad", ["", "  ", "Upper", "a b", "a/b", 5, True, ["x"], " pad", "é"])
def test_invalid_project_raises(bad: object) -> None:
    with pytest.raises(ProjectInvalid):
        principal_of(_key_record("a", body=_body(project=bad)))


def test_project_invalid_is_still_store_unavailable() -> None:
    assert issubclass(ProjectInvalid, StoreDataUnavailable)


def test_length_only_with_keyword() -> None:
    record = _key_record("a", body=_body(project="a" * 40))
    assert principal_of(record) is not None
    with pytest.raises(ProjectInvalid):
        principal_of(record, max_project_len=32)


# Feature: gw31-rag-pipeline, Property 11: project charset and length, with and without the keyword
def test_property_charset_and_length() -> None:
    rng = random.Random(11)
    alphabet = string.ascii_letters + string.digits + "_- /."
    for _ in range(1000):
        value = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        ok = (
            value == value.strip()
            and all(c in string.ascii_lowercase + string.digits + "_-" for c in value)
        )
        limit = rng.choice([None, 32])
        expect = ok and (limit is None or len(value) <= limit)
        record = _key_record("a", body=_body(project=value))
        try:
            principal_of(record, max_project_len=limit)
            got = True
        except ProjectInvalid:
            got = False
        assert got == expect
