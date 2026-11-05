"""Embeddings fail closed when the request id cannot be minted (request-id-uniqueness, 3.6)."""
from __future__ import annotations

import asyncio
import json

import pytest

from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.request_ids import RequestIdUnavailable
from tests.edge._lgw29_app import ORG, Harness
from tests.edge.test_lgw29_misc_surfaces import EMBED, VECTOR


def test_failing_mint_refuses_embeddings_with_no_dispatch_admit_or_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(self: RequestHolds, org_id: str, *a: object, **k: object) -> str:
        raise RequestIdUnavailable()

    monkeypatch.setattr(RequestHolds, "begin_request", failing)
    h = Harness(VECTOR, org=ORG)
    result = asyncio.run(h.call("/v1/embeddings", dict[str, object](EMBED)))
    assert result.status == 503
    assert json.loads(result.body)["error"]["code"] == "request_id_unavailable"
    assert json.loads(result.body)["error"]["request_id"] is None
    assert "x-request-id" not in {k.lower() for k in result.headers}
    assert h.provider.open_calls == 0
    assert h.admission.admitted == 0
    assert h.budget.spent == 0 and h.budget.refunded == 0
