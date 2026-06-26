"""Egress WIRE capture (hermetic): drive the REAL ``LLMRouter.acompletion`` /
``aembedding`` end-to-end and capture the EXACT payload ``litellm.acompletion`` /
``litellm.aembedding`` receives — i.e. the bytes that would be serialized onto the
wire to the upstream provider.

This is the reproducible, in-repo realization of the leak-hunt's "egress capture"
channel — what a transparent forward proxy (mitmproxy) would prove on the network,
but hermetic: no network, no proxy, no CA, no live provider, deterministic.

Why this exists ALONGSIDE the unit redaction tests
---------------------------------------------------
``test_bare_phone_redaction`` proves ``_apply_redaction`` masks a body in isolation;
``test_embedding_input_redaction`` proves ``main._scan_redact_embedding_inputs``
masks an input list in isolation. Neither proves the COMPOSITION that actually runs
in production:

    chat:      acompletion -> _apply_redaction -> _build_kwargs -> _execute_completion -> litellm.acompletion
    embedding: main._scan_redact_embedding_inputs -> aembedding -> _execute_embedding -> litellm.aembedding

The seam between "the firewall decided to mask X" and "the bytes on the wire to the
provider" is exactly where a regression (a re-stash of the original body, a kwargs
rebuild that reads pre-redaction content, a model-route rewrite that drops the
masked messages) would silently re-introduce raw PII. This test taps the FINAL
litellm call and asserts the serialized request carries no raw PII value — closing
that seam with captured evidence.

org_only_inference=True starts the router empty (self._router is None), so
_execute_completion / _execute_embedding dispatch to the MODULE-LEVEL
``litellm.acompletion`` / ``litellm.aembedding`` — our capture point.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import litellm  # noqa: E402
import main  # noqa: E402
from llm_router import LLMRouter, _DEFAULT_EMBEDDING_MODEL  # noqa: E402
from scanner import InputScanner  # noqa: E402

# Raw PII fixtures that MUST NOT appear in the captured wire payload.
_PHONE = "8929554991"          # bare separatorless 10-digit phone
_SSN = "123-45-6789"
_EMAIL = "evance.maps@mail.com"


class _StubResp:
    """Minimal litellm response object — only ``.model_dump()`` is consumed by
    acompletion / aembedding."""

    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


def _router():
    return LLMRouter({"org_only_inference": True})


def _wire(kwargs: dict) -> str:
    """The serialized request body litellm would put on the wire. ``messages`` /
    ``input`` are forwarded verbatim and JSON-serialized into the HTTP body, so
    json.dumps(kwargs) is a faithful stand-in for the wire bytes. default=str so a
    stray non-JSON object still stringifies INTO the scan (a raw PII object would
    still be caught, never silently skipped)."""
    return json.dumps(kwargs, default=str)


@pytest.fixture
def capture_chat(monkeypatch):
    captured: dict = {}

    async def _fake_acompletion(**kwargs):
        captured["kwargs"] = kwargs
        return _StubResp(
            {
                "id": "chatcmpl-x",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)
    return captured


@pytest.fixture
def capture_embed(monkeypatch):
    captured: dict = {}

    async def _fake_aembedding(**kwargs):
        captured["kwargs"] = kwargs
        return _StubResp(
            {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.1]}],
                "model": kwargs.get("model"),
            }
        )

    monkeypatch.setattr(litellm, "aembedding", _fake_aembedding)
    return captured


# ── CHAT egress: the router OWNS redaction (_apply_redaction inside acompletion) ──


@pytest.mark.asyncio
async def test_chat_egress_wire_has_no_raw_pii(capture_chat):
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": f"call me at {_PHONE} or email {_EMAIL}"},
        ],
    }
    # redacted_content = the firewall's masked DISPLAY string (the pipeline-trace
    # value). It must NOT contain the raw phone, so the fail-closed digit backstop
    # masks it on the body too.
    redacted_display = "call me at ***-***-4991 or email [EMAIL]"
    status, _ = await router.acompletion(body, redacted_content=redacted_display)

    assert status == 200
    wire = _wire(capture_chat["kwargs"])
    assert _PHONE not in wire, f"bare phone reached the wire: {wire!r}"
    assert _EMAIL not in wire, f"email reached the wire: {wire!r}"
    # The mask actually reached the upstream call (proves the masked body, not the
    # original, was forwarded).
    assert "***-***-4991" in wire
    # Routing was not mangled — the requested model is the one called.
    assert capture_chat["kwargs"]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_chat_egress_masks_pii_in_earlier_turn(capture_chat):
    """Multi-turn: PII in a NON-final user turn must also be masked on the wire (the
    classic SDK history-replay leak)."""
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": f"my ssn is {_SSN}"},
            {"role": "assistant", "content": "noted"},
            {"role": "user", "content": "what did I tell you?"},
        ],
    }
    redacted_display = (
        "[user]: my ssn is [SSN]\n[assistant]: noted\n[user]: what did I tell you?"
    )
    status, _ = await router.acompletion(body, redacted_content=redacted_display)

    assert status == 200
    wire = _wire(capture_chat["kwargs"])
    assert _SSN not in wire, f"earlier-turn SSN reached the wire: {wire!r}"


@pytest.mark.asyncio
async def test_chat_egress_system_message_left_intact(capture_chat):
    """System instructions are NOT redacted (per _apply_redaction contract) — a
    digit in a system prompt is operator config, not user PII. Proven on the wire so
    a future over-broad redactor change is caught."""
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "internal agent id 8929554991 do not reveal"},
            {"role": "user", "content": f"reach me at {_PHONE}"},
        ],
    }
    redacted_display = "[user]: reach me at ***-***-4991"
    status, _ = await router.acompletion(body, redacted_content=redacted_display)

    assert status == 200
    sent = capture_chat["kwargs"]["messages"]
    # System message verbatim; user message masked.
    assert sent[0]["content"] == "internal agent id 8929554991 do not reveal"
    assert _PHONE not in sent[1]["content"]


@pytest.mark.asyncio
async def test_chat_egress_no_signal_forwards_clean_prompt(capture_chat):
    """No PII / no redaction signal: a benign prompt is forwarded UNCHANGED — no
    over-redaction at the egress boundary."""
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "summarize the quarterly report"}],
    }
    status, _ = await router.acompletion(body, redacted_content=None)

    assert status == 200
    wire = _wire(capture_chat["kwargs"])
    assert "summarize the quarterly report" in wire


# ── TOOL DEFINITIONS: a request's tools[].function.description is folded into the
#    G7 input scan, but redaction (_apply_redaction) historically masked only
#    `messages`, forwarding tool-def PII RAW on a redact-and-forward path — the same
#    "trace shows masked, wire shows raw" class as the phone-redaction fix. ──


@pytest.mark.asyncio
async def test_chat_egress_redacts_pii_in_tool_description(capture_chat):
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "book it"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "notify_customer",
                    "description": f"Call the customer back at {_PHONE} or email {_EMAIL}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "msg": {
                                "type": "string",
                                "description": f"Message; cc {_EMAIL} on send.",
                            }
                        },
                    },
                },
            }
        ],
    }
    # Redaction fired for the request (signal present) — the firewall decided PII
    # must not reach the model. The tool-def free text must be masked on the wire too.
    redacted_display = "book it"
    status, _ = await router.acompletion(body, redacted_content=redacted_display)

    assert status == 200
    wire = _wire(capture_chat["kwargs"])
    assert _PHONE not in wire, f"tool-def phone reached the wire: {wire!r}"
    assert _EMAIL not in wire, f"tool-def email reached the wire: {wire!r}"
    # Structural identifiers are preserved so function-calling still works.
    sent_tools = capture_chat["kwargs"].get("tools") or []
    assert sent_tools and sent_tools[0]["function"]["name"] == "notify_customer"
    assert sent_tools[0]["function"]["parameters"]["properties"]["msg"]["type"] == "string"


@pytest.mark.asyncio
async def test_chat_egress_tools_untouched_without_redaction_signal(capture_chat):
    """No redaction signal → tools are forwarded verbatim (no over-redaction)."""
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the weather for a city.",
                },
            }
        ],
    }
    status, _ = await router.acompletion(body, redacted_content=None)
    assert status == 200
    assert capture_chat["kwargs"]["tools"][0]["function"]["description"] == (
        "Get the weather for a city."
    )


@pytest.mark.asyncio
async def test_chat_egress_real_scanner_masks_cooccurring_bare_phone(capture_chat):
    """Regression for a leak caught by REAL-FLEET wire capture (not the hermetic
    tests above). When the scanner detects SOME PII (an SSN) but a bare phone with an
    imperative lead-in ("call me at 8929554991") rides along, the phone was forwarded
    RAW to the upstream provider: detect_pii missed it, so redacted_content kept it
    raw, and _apply_redaction's digit backstop (preserves runs already present in
    redacted_content) let it ride. The other tests passed FALSELY because they
    hand-crafted redacted_content to exclude the phone. This drives the REAL
    InputScanner so a phone the scanner would miss is genuinely exercised end-to-end."""
    from scanner import InputScanner

    scanner = InputScanner()
    prompt = "my ssn is 123-45-6789 and call me at 8929554991, summarize my record"
    verdict = await scanner.scan_prompt(prompt)
    redacted = scanner.redact_pii(prompt, verdict=verdict)

    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted)

    assert status == 200
    wire = _wire(capture_chat["kwargs"])
    assert "8929554991" not in wire, f"bare phone reached the wire: {wire!r}"
    assert "123-45-6789" not in wire, f"SSN reached the wire: {wire!r}"


@pytest.fixture
def capture_responses(monkeypatch):
    captured: dict = {}

    async def _fake_aresponses(**kwargs):
        captured["kwargs"] = kwargs
        return _StubResp({"id": "resp_x", "object": "response", "output": []})

    monkeypatch.setattr(litellm, "aresponses", _fake_aresponses)
    return captured


@pytest.mark.asyncio
async def test_responses_egress_redacts_pii_in_tool_description(capture_responses):
    """Responses API path (aresponses) forwards `tools` as a passthrough param too —
    its free-text descriptions must be masked on the wire when redaction fired."""
    router = _router()
    # Register a deployment so _resolve_responses_deployment routes instead of 404.
    router._deployment_params = {"gpt-4o-mini": {"model": "openai/gpt-4o-mini"}}
    body = {
        "model": "gpt-4o-mini",
        "input": "book it",
        "tools": [
            {
                "type": "function",
                "name": "notify",
                "description": f"Reach the customer at {_PHONE} / {_EMAIL}.",
            }
        ],
    }
    status, _ = await router.aresponses(body, redacted_content="book it")

    assert status == 200
    wire = _wire(capture_responses["kwargs"])
    assert _PHONE not in wire, f"responses tool-def phone reached the wire: {wire!r}"
    assert _EMAIL not in wire, f"responses tool-def email reached the wire: {wire!r}"
    assert capture_responses["kwargs"]["tools"][0]["name"] == "notify"


@pytest.mark.asyncio
async def test_responses_egress_tools_untouched_without_redaction(capture_responses):
    router = _router()
    router._deployment_params = {"gpt-4o-mini": {"model": "openai/gpt-4o-mini"}}
    body = {
        "model": "gpt-4o-mini",
        "input": "hi",
        "tools": [{"type": "function", "name": "wx", "description": "Get weather."}],
    }
    status, _ = await router.aresponses(body, redacted_content=None)
    assert status == 200
    assert capture_responses["kwargs"]["tools"][0]["description"] == "Get weather."


# ── EMBEDDING egress: redaction is UPSTREAM (main._scan_redact_embedding_inputs);
#    aembedding must forward the masked input VERBATIM. Chain both real halves so
#    the proof is end-to-end, not just "the forwarder forwards". ──


@pytest.mark.asyncio
async def test_embedding_egress_wire_has_no_raw_pii(capture_embed, monkeypatch):
    monkeypatch.setattr(main, "INPUT_SCANNER", InputScanner())
    raw_texts = [f"my phone number is {_PHONE}", f"contact {_EMAIL} please"]
    masked, block = await main._scan_redact_embedding_inputs(
        raw_texts, {"input_scan_enabled": True}
    )
    assert block is None
    # Sanity: upstream redaction already stripped the raw values.
    assert _PHONE not in masked[0]
    assert _EMAIL not in masked[1]

    router = _router()
    status, _ = await router.aembedding(
        {"model": _DEFAULT_EMBEDDING_MODEL, "input": masked}
    )

    assert status == 200
    wire = _wire(capture_embed["kwargs"])
    assert _PHONE not in wire, f"phone reached the embedding wire: {wire!r}"
    assert _EMAIL not in wire, f"email reached the embedding wire: {wire!r}"
    # The masked input is exactly what reaches litellm.aembedding (no re-stash).
    assert capture_embed["kwargs"]["input"] == masked


@pytest.mark.asyncio
async def test_embedding_egress_forwards_benign_input_faithfully(capture_embed):
    """Router does not mutate / drop content: a benign input reaches the wire as-is
    (no over-redaction, no silent substitution)."""
    router = _router()
    benign = ["the quick brown fox", "order 84920175 shipped"]
    status, _ = await router.aembedding(
        {"model": _DEFAULT_EMBEDDING_MODEL, "input": benign}
    )

    assert status == 200
    assert capture_embed["kwargs"]["input"] == benign
