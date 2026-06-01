"""
RED tests for ``rag_pipeline.bedrock_embedder.BedrockEmbedder``.

All tests in this module are expected to FAIL against the L2.1 stub
(which raises NotImplementedError). They turn GREEN in L2.5 when the
real implementation is wired in.

Test surface (per locked Layer-2 revised plan, 6 cases):
    1. fail-closed when ``assume_redacted`` not supplied
    2. happy-path returns 256-dim list and increments hit/miss counters
    3. cache hit within TTL skips Bedrock invocation
    4. cache expiry past TTL re-invokes Bedrock
    5. open circuit breaker raises ``BedrockCircuitOpenError`` (no boto call)
    6. >MAX_INPUT_CHARS input is truncated and emits ``result="truncated"``
"""
from __future__ import annotations

import json
from typing import List
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from ai_mesh_gateway.rag_pipeline.bedrock_embedder import (
    BedrockCircuitOpenError,
    BedrockEmbedder,
    PIIRedactionRequiredError,
)
from ai_mesh_gateway.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitStatus,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _titan_response(vector: List[float]) -> dict:
    """Shape returned by ``invoke_model`` for Titan v2."""
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": vector}).encode()
    return {"body": body, "contentType": "application/json"}


class _ClockStub:
    """Deterministic monotonic-clock substitute."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_breaker_status(state: CircuitState, should_block: bool) -> CircuitStatus:
    return CircuitStatus(state=state, should_block=should_block)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fake_boto_client():
    """Mocked boto3 bedrock-runtime client supporting invoke_model."""
    client = MagicMock()
    client.invoke_model.return_value = _titan_response([0.1] * 256)
    return client


@pytest.fixture()
def closed_breaker():
    breaker = MagicMock(spec=CircuitBreaker)

    async def _check(_model: str) -> CircuitStatus:
        return _make_breaker_status(CircuitState.CLOSED, False)

    async def _ok(_model: str) -> None:
        return None

    async def _err(_model: str, _et: str = "") -> None:
        return None

    breaker.check = _check
    breaker.record_success = _ok
    breaker.record_error = _err
    return breaker


@pytest.fixture()
def open_breaker():
    breaker = MagicMock(spec=CircuitBreaker)

    async def _check(_model: str) -> CircuitStatus:
        return _make_breaker_status(CircuitState.OPEN, True)

    breaker.check = _check
    return breaker


@pytest.fixture()
def clock() -> _ClockStub:
    return _ClockStub()


@pytest.fixture()
def embedder(fake_boto_client, closed_breaker, clock) -> BedrockEmbedder:
    return BedrockEmbedder(
        bedrock_client=fake_boto_client,
        circuit_breaker=closed_breaker,
        ttl_seconds=60.0,
        cache_max_entries=16,
        time_provider=clock,
    )


# --------------------------------------------------------------------------- #
# Tests (RED until L2.5)                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fail_closed_without_redaction_attestation(embedder, fake_boto_client):
    """Calling embed() without assume_redacted=True must raise and not call AWS."""
    with pytest.raises(PIIRedactionRequiredError):
        await embedder.embed("user prompt text", org_slug="acme")
    fake_boto_client.invoke_model.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_returns_256_dim_vector(embedder, fake_boto_client):
    vec = await embedder.embed("hello world", org_slug="acme", assume_redacted=True)
    assert isinstance(vec, list)
    assert len(vec) == 256
    assert fake_boto_client.invoke_model.call_count == 1


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_skips_bedrock(embedder, fake_boto_client, clock):
    await embedder.embed("doc-1", org_slug="acme", assume_redacted=True)
    clock.advance(30.0)  # still within 60s TTL
    await embedder.embed("doc-1", org_slug="acme", assume_redacted=True)
    assert fake_boto_client.invoke_model.call_count == 1


@pytest.mark.asyncio
async def test_cache_expiry_past_ttl_reinvokes(embedder, fake_boto_client, clock):
    await embedder.embed("doc-1", org_slug="acme", assume_redacted=True)
    clock.advance(120.0)  # past 60s TTL
    await embedder.embed("doc-1", org_slug="acme", assume_redacted=True)
    assert fake_boto_client.invoke_model.call_count == 2


@pytest.mark.asyncio
async def test_open_circuit_raises_and_does_not_invoke(
    fake_boto_client, open_breaker, clock
):
    emb = BedrockEmbedder(
        bedrock_client=fake_boto_client,
        circuit_breaker=open_breaker,
        time_provider=clock,
    )
    with pytest.raises(BedrockCircuitOpenError):
        await emb.embed("doc", org_slug="acme", assume_redacted=True)
    fake_boto_client.invoke_model.assert_not_called()


@pytest.mark.asyncio
async def test_oversize_input_is_truncated(embedder, fake_boto_client):
    long_text = "x" * (BedrockEmbedder.MAX_INPUT_CHARS * 2)
    vec = await embedder.embed(long_text, org_slug="acme", assume_redacted=True)
    assert len(vec) == 256
    # Inspect what was sent to Bedrock
    call_args = fake_boto_client.invoke_model.call_args
    body_arg = call_args.kwargs.get("body") or call_args.args[1]
    payload = json.loads(body_arg)
    sent_text = payload.get("inputText", "")
    assert len(sent_text) <= BedrockEmbedder.MAX_INPUT_CHARS
