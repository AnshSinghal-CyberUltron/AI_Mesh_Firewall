"""Unit tests for ``edge/request_ids.py`` (spec request-id-uniqueness, task 3.2)."""

from __future__ import annotations

import random
import re

import pytest

from gateway_v2.edge.request_ids import RequestIdUnavailable, mint_tenant_request_id


def test_shape_and_tenant_scope() -> None:
    rid = mint_tenant_request_id("org-a")
    assert re.fullmatch(r"zs-org-a-[0-9a-f]{24}", rid)
    assert "org-b" not in rid


def test_ids_are_distinct() -> None:
    assert len({mint_tenant_request_id("o") for _ in range(500)}) == 500


def test_seeded_shape_over_many_orgs() -> None:
    rng = random.Random(7)
    for i in range(200):
        org = f"org{rng.randrange(10**6)}"
        rid = mint_tenant_request_id(org, token_hex=lambda n: rng.randbytes(n).hex())
        assert re.fullmatch(rf"zs-{org}-[0-9a-f]{{24}}", rid), i


def test_requests_width_in_bytes() -> None:
    seen: list[int] = []

    def fake(n: int) -> str:
        seen.append(n)
        return "a" * (2 * n)

    assert mint_tenant_request_id("o", token_hex=fake) == "zs-o-" + "a" * 24
    assert seen == [12]


def test_failing_entropy_raises() -> None:
    def boom(n: int) -> str:
        raise OSError("no entropy")

    with pytest.raises(RequestIdUnavailable):
        mint_tenant_request_id("o", token_hex=boom)


@pytest.mark.parametrize("bad", ["", "ab", "a" * 23, "a" * 25, "g" * 24, "A" * 24, "a" * 23 + " "])
def test_short_or_non_hex_output_raises(bad: str) -> None:
    with pytest.raises(RequestIdUnavailable):
        mint_tenant_request_id("o", token_hex=lambda n: bad)


def test_non_string_output_raises() -> None:
    with pytest.raises(RequestIdUnavailable):
        mint_tenant_request_id("o", token_hex=lambda n: b"a" * 24)  # type: ignore[arg-type,return-value]
