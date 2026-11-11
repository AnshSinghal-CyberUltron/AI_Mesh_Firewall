"""v2-enforcement-leaks Group A (embeddings): input REDACT must be applied per item.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 3; design Property 1 (A) and
Property 15). Under the dev plan (``dev/plan.example.json``: pii → redact, secret/credential →
block, every rule ``scope: both`` on ``chat`` and ``embeddings``) an SSN or an email in any
``/v1/embeddings`` input item resolves to an input REDACT decision with no BLOCK, so the request
is admitted and dispatched. The PROVIDER must then receive (Property 15):

* exactly one ``("embeddings_input", item)`` delta per input item, in request order, and
  exactly one delta for a string input (shape clause, 2.20);
* ``[REDACTED]`` in place of each finding's bytes in every redacted item (2.19);
* every clean item byte-identical to the parsed item (2.20);
* no raw planted value in any delta.

The shape clauses and the masking clauses are separate tests, so a clause that already holds on
the unfixed tree stays unmarked. On the unfixed tree ``EmbeddingsSurface._input_scan`` acts only
on BLOCK (REDACT returns ``None``) and ``_input_as_deltas`` packs the raw parsed items, so every
masking case leaks; those cases carry a strict xfail naming the observed counterexample and flip
when Group A lands.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# Same dev plan, key and contract as the sibling chat exploration test (kept local so this file
# type-checks on its own under ``mypy --strict``).
EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
SSN = "123-45-6789"
EMAIL = "jane@example.com"
KEY = "sk-dev-pipeline"
REDACTED = "[REDACTED]"
CHANNEL = "embeddings_input"

Delta = tuple[str, str]


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


class _RecordingProvider:
    """Records the exact ``(channel, text)`` deltas of every body, replies one vector per delta.

    ``EmbeddingsSurface._drain_vectors`` decodes each reply delta's text as a JSON float array
    (one embedding vector, arrival order = batch index), so the reply is one scripted vector per
    forwarded item and the request completes with HTTP 200.
    """

    def __init__(self) -> None:
        self.received: list[tuple[Delta, ...]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        deltas = tuple((str(item[0]), str(item[1])) for item in raw)
        self.received.append(deltas)
        for index, _delta in enumerate(deltas):
            vector = json.dumps([float(index), 0.5])
            yield UpstreamEvent(text_deltas=((CHANNEL, vector),), final=index == len(deltas) - 1)

    async def abort(self) -> None:
        return None


def _app(provider: _RecordingProvider) -> object:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _post(app: object, embeddings_input: str | list[str]) -> int:
    body = json.dumps({"model": "echo-1", "input": embeddings_input}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/embeddings",
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    return next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")


def _forwarded(case: str) -> tuple[Delta, ...]:
    """Drive one case through the dev app; return the one recorded provider body's deltas."""
    embeddings_input, _expected, _raw = _CASES[case]
    provider = _RecordingProvider()
    status = asyncio.run(_post(_app(provider), embeddings_input))
    assert status == 200, f"{case}: an input REDACT (no BLOCK) must proceed, got {status}"
    assert len(provider.received) == 1, f"{case}: exactly one provider call"
    return provider.received[0]


# Each case: the request ``input``, the EXACT provider deltas Property 15 requires (one per item,
# in order; each finding's bytes replaced by ``[REDACTED]``; clean items byte-identical), and the
# raw planted values that must appear in no delta.
_CASES: dict[str, tuple[str | list[str], tuple[Delta, ...], tuple[str, ...]]] = {
    "list_one_redacted": (
        ["release notes", f"My SSN is {SSN}"],
        ((CHANNEL, "release notes"), (CHANNEL, f"My SSN is {REDACTED}")),
        (SSN,),
    ),
    "string_input": (
        f"mail {EMAIL}",
        ((CHANNEL, f"mail {REDACTED}"),),
        (EMAIL,),
    ),
    "list_two_redacted": (
        [f"SSN {SSN} on file", "release notes", f"contact {EMAIL} today"],
        (
            (CHANNEL, f"SSN {REDACTED} on file"),
            (CHANNEL, "release notes"),
            (CHANNEL, f"contact {REDACTED} today"),
        ),
        (SSN, EMAIL),
    ),
}

@pytest.mark.parametrize("case", list(_CASES))
def test_embeddings_redact_keeps_one_delta_per_item_in_order(case: str) -> None:
    """Property 15 (shape): one ``embeddings_input`` delta per item, order kept, clean items kept.

    **Validates: Requirements 2.20**
    """
    embeddings_input, expected, _raw = _CASES[case]
    deltas = _forwarded(case)
    items = [embeddings_input] if isinstance(embeddings_input, str) else embeddings_input
    assert len(deltas) == len(items), f"{case}: one delta per input item, got {deltas!r}"
    if isinstance(embeddings_input, str):
        assert len(deltas) == 1, f"{case}: a string input must give exactly one delta"
    for position, ((channel, text), (_want_channel, want_text)) in enumerate(
        zip(deltas, expected, strict=True)
    ):
        assert channel == CHANNEL, f"{case}: delta {position} channel {channel!r}"
        if REDACTED not in want_text:
            assert text == items[position], (
                f"{case}: clean item {position} must be byte-identical: {text!r}"
            )


@pytest.mark.parametrize("case", list(_CASES))
def test_embeddings_redact_masks_each_item_before_the_provider(case: str) -> None:
    """Property 15 (masking): each finding becomes ``[REDACTED]``; no raw value in any delta.

    **Validates: Requirements 1.18, 2.19, 2.20**
    """
    _input, expected, raw_values = _CASES[case]
    deltas = _forwarded(case)
    for channel, text in deltas:
        for raw in raw_values:
            assert raw not in text, f"{case}: raw {raw!r} in provider delta {(channel, text)!r}"
    redacted_items = sum(1 for _c, want in expected if REDACTED in want)
    got_redacted = sum(1 for _c, text in deltas if REDACTED in text)
    assert got_redacted == redacted_items, (
        f"{case}: {REDACTED} expected in {redacted_items} item(s), found in {got_redacted}: "
        f"{deltas!r}"
    )
    assert deltas == expected, (
        f"{case}: each delta must hold exactly its own sanitized item: "
        f"got {deltas!r}, expected {expected!r}"
    )
