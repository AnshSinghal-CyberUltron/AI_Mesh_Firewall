"""v2-enforcement-leaks Group E: each rule is resolved only in the phase its scope names.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 8; design Property 1 (E) and
Property 11). Bug condition ``C_E``: some rule in the plan has ``scope`` ``input`` or ``output``.
The plan under test has exactly two rules:

* ``e-pii-redact-input``: ``pii`` -> ``redact``, ``scope: input``;
* ``e-secret-block-output``: ``secret`` -> ``block``, ``scope: output``.

Expected (fixed) behaviour, clauses 2.13 / 2.14:

* the dev boot accepts the plan (case 1);
* an input carrying a SECRET is NOT blocked at input: the output-only rule does not select there;
* a provider reply carrying a SECRET IS blocked at output: the output-only rule selects there;
* a provider reply carrying PII is NOT redacted: the input-only rule does not select on output.

**Seam.** Every case drives the REAL dev-profile wiring (``serve.build_boot_dependencies`` with
``AMF_DEV_PROFILE=1`` and the plan written to ``tmp_path``) over raw ASGI, with only the provider
swapped for a scripted one that records what it received and replies with a fixed text. On the
unfixed tree the boot refuses this plan (``require_phase_agnostic``, clause 1.14), which would
hide the per-phase defect (clause 1.13). The phase cases therefore neutralise that one boot gate
with ``monkeypatch`` so the shipped wiring -- ``plan_resolver(plan)`` =
``PlanResolver(plan, Phase.INPUT)`` serving the input scan, the non-streaming output guard and
the streaming pipeline alike -- is what gets exercised. ``raising=False`` keeps the patch harmless
once the fix removes or reshapes the gate; then the fixed dev profile wires a per-phase resolver
and the same assertions hold with no patch effect. Case 1 never patches.

Streaming and non-streaming share the input scan but take different output paths
(``openai_nonstream._enforce_channels`` vs the holdback ``StreamPipeline``), so every phase case
runs in both modes. Failing cases carry a strict xfail naming the observed counterexample.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import dev_components, serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

KEY = "sk-dev-phase"
ORG = "dev-org"
SECRET = "AKIAIOSFODNN7EXAMPLE"
SSN = "123-45-6789"
REDACTED = "[REDACTED]"

_LEAK = "v2-enforcement-leaks E: "


def _rule(rule_id: str, category: str, action: str, scope: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "category": category,
        "mode": "enforce",
        "action": action,
        "threshold": None,
        "priority": 100,
        "scope": scope,
        "on_unavailable": "fail_closed",
        "surfaces": ["chat", "embeddings"],
    }


#: The plan under test, in the signed ``plan.document`` shape (``dev/plan.example.json``).
PHASE_PLAN: dict[str, object] = {
    "org_id": ORG,
    "streaming_mode": "incremental",
    "rules": [
        _rule("e-pii-redact-input", "pii", "redact", "input"),
        _rule("e-secret-block-output", "secret", "block", "output"),
    ],
}


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


def _env(plan_path: Path) -> dict[str, str]:
    return {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_ORG_ID": ORG,
        "AMF_DEV_PLAN_PATH": str(plan_path),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }


def _write_plan(tmp_path: Path) -> Path:
    path = tmp_path / "plan.phase.json"
    path.write_text(json.dumps(PHASE_PLAN), encoding="utf-8")
    return path


class _ScriptedProvider:
    """Records every forwarded input text, then streams ``reply`` back word by word."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.received: list[str] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        self.received.append("\n".join(str(item[1]) for item in raw))
        words = self._reply.split(" ")
        last = len(words) - 1
        for index, word in enumerate(words):
            text = word if index == last else word + " "
            yield UpstreamEvent(text_deltas=(("content", text),), final=index == last)

    async def abort(self) -> None:
        return None


def _app(tmp_path: Path, provider: _ScriptedProvider) -> object:
    deps, _logs = serve.build_boot_dependencies(
        contract=_contract(), env=_env(_write_plan(tmp_path))
    )
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _post(app: object, content: str, *, stream: bool) -> tuple[int, bytes]:
    body = json.dumps(
        {"model": "echo-1", "stream": stream, "messages": [{"role": "user", "content": content}]}
    ).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
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
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    chunks: list[bytes] = []
    for message in sent:
        if message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            assert isinstance(chunk, bytes)
            chunks.append(chunk)
    return status, b"".join(chunks)


@pytest.fixture
def _phase_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the unfixed boot's scope-``both`` gate so the phase wiring is observable."""

    def _accept(plan: object) -> None:
        del plan

    monkeypatch.setattr(dev_components, "require_phase_agnostic", _accept, raising=False)


def _modes(reason: str | None) -> list[object]:
    marks = () if reason is None else (pytest.mark.xfail(strict=True, reason=_LEAK + reason),)
    return [
        pytest.param(stream, id="stream" if stream else "json", marks=marks)
        for stream in (False, True)
    ]


# --- case 1: the dev boot accepts a phase-scoped plan ----------------------------------------- #


def test_dev_boot_accepts_a_plan_with_phase_scoped_rules(tmp_path: Path) -> None:
    """**Validates: Requirements 2.14**"""
    deps, logs = serve.build_boot_dependencies(
        contract=_contract(), env=_env(_write_plan(tmp_path))
    )
    assert logs[0].startswith("boot: DEV PROFILE ACTIVE")
    assert callable(serve._assemble_app(deps))


# --- case 2: each rule acts only in its declared phase ---------------------------------------- #


@pytest.mark.usefixtures("_phase_gate_open")
@pytest.mark.parametrize("stream", _modes(None))
def test_output_only_block_does_not_block_input(tmp_path: Path, stream: bool) -> None:
    """A SECRET in the INPUT is not blocked: ``e-secret-block-output`` is output-only.

    **Validates: Requirements 2.13, 2.14**
    """
    provider = _ScriptedProvider("all good")
    status, payload = asyncio.run(
        _post(_app(tmp_path, provider), f"key {SECRET} please", stream=stream)
    )
    assert b"input_blocked" not in payload, f"output-only rule blocked input: {payload!r}"
    assert status == 200, f"expected 200, got {status}: {payload!r}"
    assert len(provider.received) == 1, "the request must reach the provider"


@pytest.mark.usefixtures("_phase_gate_open")
@pytest.mark.parametrize("stream", _modes(None))
def test_output_only_block_blocks_output(tmp_path: Path, stream: bool) -> None:
    """A SECRET in the provider REPLY is blocked: ``e-secret-block-output`` selects on output.

    **Validates: Requirements 2.13, 2.14**
    """
    provider = _ScriptedProvider(f"here is {SECRET} ok")
    status, payload = asyncio.run(_post(_app(tmp_path, provider), "hello there", stream=stream))
    assert len(provider.received) == 1, "a clean input must reach the provider"
    assert SECRET.encode() not in payload, f"raw secret released to the client: {payload!r}"
    assert b"output_blocked" in payload, f"no output_blocked (status {status}): {payload!r}"


@pytest.mark.usefixtures("_phase_gate_open")
@pytest.mark.parametrize("stream", _modes(None))
def test_input_only_redact_does_not_touch_output(tmp_path: Path, stream: bool) -> None:
    """PII in the provider REPLY is released unchanged: ``e-pii-redact-input`` is input-only.

    **Validates: Requirements 2.13, 2.14**
    """
    provider = _ScriptedProvider(f"my SSN {SSN} ok")
    status, payload = asyncio.run(_post(_app(tmp_path, provider), "hello there", stream=stream))
    assert status == 200, f"expected 200, got {status}: {payload!r}"
    assert REDACTED.encode() not in payload, f"input-only rule redacted output: {payload!r}"
    assert SSN.encode() in payload, f"reply bytes altered on output: {payload!r}"
