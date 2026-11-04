"""GW17b credential value types do not reveal, copy or serialise their secret."""

from __future__ import annotations

import copy
import pickle

import pytest

from gateway_v2.domain.mcp_credential import RenderedHeaders, UpstreamCredential

SECRET = "s3cr3t-value-123"


def test_repr_and_str_never_contain_the_secret() -> None:
    cred = UpstreamCredential(kind="bearer", token=SECRET, username="user-name")
    rendered = RenderedHeaders((("Authorization", f"Bearer {SECRET}"),))
    for obj in (cred, rendered):
        assert SECRET not in repr(obj) and SECRET not in str(obj) and SECRET not in f"{obj!r}"
    assert "user-name" not in repr(cred)


@pytest.mark.parametrize("op", [pickle.dumps, copy.copy, copy.deepcopy])
def test_credentials_cannot_be_serialised_or_copied(op: object) -> None:
    for obj in (
        UpstreamCredential(kind="bearer", token=SECRET),
        RenderedHeaders((("a", "b"),)),
    ):
        with pytest.raises(TypeError):
            op(obj)  # type: ignore[operator]
