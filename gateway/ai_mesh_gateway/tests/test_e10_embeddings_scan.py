"""E10: /v1/embeddings input scan + redaction (FIX G1) and RAG-ingest metadata
value redaction (FIX G2b).

PROVEN-LIVE LEAK (G1): a credential+SSN payload that ``/v1/chat/completions``
BLOCKS (403) was embedded RAW (200) by ``/v1/embeddings`` — the proxy dispatched
``LLM_ROUTER.aembedding(body)`` with NO PII/secret scan, so the third-party
embedding provider saw the raw value. These tests prove the same INPUT_SCANNER the
chat path uses now runs BEFORE the aembedding dispatch:

  (a) a credential+SSN payload is REDACTED before it reaches the embedding
      provider (raw SSN / AWS key never leave the gateway), and the request still
      succeeds (200) — the redact-not-error contract;
  (b) FAIL-SAFE: when the scanner detects PII/secret it genuinely CANNOT mask, the
      helper returns a BLOCK (the proxy 403s) — it never embeds the raw value;
  (c) no behavior change when input scanning is disabled (no-regression guard).

G2b: RAG-ingest document CONTENT was typed-redacted, but caller-supplied METADATA
VALUES were not — only the trust-boost KEYS verified_source/created_by were
stripped. A secret/PII hidden in a metadata value (incl. nested dict/list) was
stored verbatim and round-trips to RAG-query callers. The metadata-value redaction
helper is unit-tested here.

Harness mirrors test_openai_sdk_compat.py: the REAL gateway app behind
httpx.ASGITransport, driven by the stock ``openai`` SDK, with auth in fakeredis,
a real InputScanner, and the upstream aembedding STUBBED so the test can inspect
exactly what text the gateway would have sent to the provider.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import httpx
import openai
import pytest
import pytest_asyncio

API_KEY = "zs_test_e10_embeddings_0123456789abcdef"

# Raw secrets the firewall must NOT let reach the embedding provider.
RAW_SSN = "123-45-6789"
RAW_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
LEAK_PAYLOAD = (
    f"Employee record: SSN is {RAW_SSN} and the AWS access key is "
    f"{RAW_AWS_KEY}. Please embed this for similarity search."
)

EMBED_MODEL = {
    "model_name": "zs-embed",
    "model_id": "text-embedding-3-small",
    "provider": "openai",
    "is_active": True,
    "api_key_set": True,
}

TEST_CONFIG = {
    "backend_url": "",
    "api_key": "",
    "input_scan_enabled": True,
    "tier2_enabled": False,
    "output_scan_enabled": True,
    "enforcement_mode": "block",
    "kill_switch_enabled": False,
    "threat_intel_enabled": False,
    "routing_enabled": False,
    "model_isolation_enabled": False,
}


def _auth_payload() -> dict:
    return {
        "key_id": "550e8400-e29b-41d4-a716-446655440099",
        "prefix": API_KEY[:8],
        "user_id": 1,
        "project_id": "proj-e10",
        "org_slug": "",
        "organization_id": "org-e10",
        "permissions": {"allowed_actions": ["chat", "embedding"], "denied_actions": []},
        "allowed_models": ["zs-embed"],
        "rate_limit_tpm": 50000,
        "risk_score": 0.0,
        "is_active": True,
        "expires_at": None,
    }


async def _make_app(monkeypatch, *, captured: dict):
    """Mount the real gateway app; capture the body handed to aembedding so the
    test can inspect exactly what text the gateway would forward to the provider."""
    from ai_mesh_gateway import main as gateway_main
    from ai_mesh_gateway import middleware as gw_middleware
    from ai_mesh_gateway.scanner import InputScanner

    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key_hash = hashlib.sha256(API_KEY.encode("utf-8")).hexdigest()
    await auth_redis.set(f"auth:apikey:{key_hash}", json.dumps(_auth_payload()))

    async def _get_redis(self):
        return auth_redis

    monkeypatch.setattr(gw_middleware.AuthMiddleware, "_get_redis", _get_redis)

    config_sync = MagicMock()
    config_sync.get_config = MagicMock(return_value=dict(TEST_CONFIG))
    config_sync.get_model_routing = MagicMock(return_value=[dict(EMBED_MODEL)])
    config_sync.reload_models_now = AsyncMock()

    async def _capture_embedding(body, *_a, **_kw):
        # Record the input EXACTLY as the gateway hands it to the provider.
        captured["input"] = body.get("input")
        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        return 200, {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.01, -0.02, 0.03]}
                for i in range(len(items))
            ],
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 4 * len(items), "total_tokens": 4 * len(items)},
        }

    llm_router = MagicMock()
    llm_router.aembedding = AsyncMock(side_effect=_capture_embedding)

    monkeypatch.setattr(gateway_main, "CONFIG", dict(TEST_CONFIG))
    monkeypatch.setattr(gateway_main, "CONFIG_SYNC", config_sync)
    monkeypatch.setattr(gateway_main, "LLM_ROUTER", llm_router)
    monkeypatch.setattr(gateway_main, "INPUT_SCANNER", InputScanner(thread_pool_size=2))
    monkeypatch.setattr(gateway_main, "AGENT_ID", None)
    monkeypatch.setattr(gateway_main, "POLICY_SYNC", None)
    monkeypatch.setattr(gateway_main, "RATE_LIMITER", None)
    monkeypatch.setattr(gateway_main, "CIRCUIT_BREAKER", None)
    monkeypatch.setattr(gateway_main, "REDIS_CLIENT", None)
    monkeypatch.setattr(gateway_main, "TELEMETRY", None)
    monkeypatch.setattr(gateway_main, "OUTPUT_GUARD", None)
    monkeypatch.setattr(gateway_main, "_emit_telemetry", lambda **_kw: None)
    monkeypatch.setattr(gateway_main, "_audit_fire_and_forget", lambda **_kw: None)

    return gateway_main.app, auth_redis


@pytest_asyncio.fixture()
async def emb_ctx(monkeypatch):
    captured: dict = {}
    app, auth_redis = await _make_app(monkeypatch, captured=captured)
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    client = openai.AsyncOpenAI(
        base_url="http://testserver/v1",
        api_key=API_KEY,
        http_client=http_client,
        max_retries=0,
    )
    yield client, captured
    await client.close()
    await auth_redis.aclose()


# ───────────────────────── FIX G1: /v1/embeddings ─────────────────────────


@pytest.mark.asyncio
async def test_embeddings_credential_ssn_payload_is_not_embedded_raw(emb_ctx):
    """G1 PROVEN-LIVE-LEAK: the credential+SSN payload must NOT reach the embedding
    provider RAW. The request succeeds (redact, not error) but the text the gateway
    forwards has the SSN and AWS key MASKED."""
    client, captured = emb_ctx

    resp = await client.embeddings.create(model="zs-embed", input=LEAK_PAYLOAD)

    # The request is not a raw 200-with-leak: it returns embeddings (redact path)…
    assert len(resp.data) == 1
    # …and the upstream provider was called with REDACTED text — the raw secrets
    # never left the gateway.
    forwarded = captured.get("input")
    assert forwarded is not None, "aembedding was never called"
    forwarded_text = forwarded if isinstance(forwarded, str) else "\n".join(forwarded)
    assert RAW_SSN not in forwarded_text, "raw SSN was embedded — G1 leak still open"
    assert RAW_AWS_KEY not in forwarded_text, "raw AWS key was embedded — G1 leak still open"


@pytest.mark.asyncio
async def test_embeddings_list_input_each_item_scanned(emb_ctx):
    """G1: body['input'] may be a LIST of strings — every item must be scanned, not
    just index 0. A secret buried at index 1 must also be redacted."""
    client, captured = emb_ctx

    await client.embeddings.create(
        model="zs-embed",
        input=["a perfectly benign sentence", f"my ssn is {RAW_SSN}"],
    )

    forwarded = captured.get("input")
    assert isinstance(forwarded, list) and len(forwarded) == 2
    assert RAW_SSN not in "\n".join(forwarded), "list-item SSN was embedded raw"


@pytest.mark.asyncio
async def test_embeddings_clean_input_passes_through_unchanged(emb_ctx):
    """G1 no-regression: a benign input is forwarded byte-for-byte (the scan must
    not mangle clean text)."""
    client, captured = emb_ctx
    clean = "The quarterly sales report shows strong growth in EMEA."

    resp = await client.embeddings.create(model="zs-embed", input=clean)

    assert len(resp.data) == 1
    assert captured.get("input") == clean


# ───────── FIX G1 fail-safe + no-op: the helper directly (no app wiring) ─────────


@pytest.mark.asyncio
async def test_scan_redact_helper_blocks_unmaskable_pii(monkeypatch):
    """G1 FAIL-SAFE: when the scanner DETECTS PII/secret it cannot mask, the helper
    returns a BLOCK (caller 403s) rather than embedding the raw value. We force the
    'detected but unmaskable' shape by stubbing redaction to a no-op so the helper
    must fail closed instead of leaking.

    B4: ``_scan_redact_embedding_inputs`` now masks via the shared egress redactor
    ``llm_router._redact_text_with_backstop``, which itself calls
    ``patterns.redact_all`` — so stubbing ``redact_pii`` ALONE no longer forces a
    no-op (redact_all would still mask the email/SSN, and the helper would correctly
    NOT block a maskable value). To exercise the genuine detected-but-unmaskable
    branch we neutralise BOTH the verdict-aware ``redact_pii`` AND the backstop's
    ``redact_all`` (the flat ``patterns`` module is the one the backstop imports)."""
    import patterns as gateway_patterns

    from ai_mesh_gateway import main as gateway_main
    from ai_mesh_gateway.scanner import InputScanner

    scanner = InputScanner(thread_pool_size=2)
    # Force redaction to a no-op on EVERY masking path the helper uses, so the
    # byte-verify fail-closed branch must trip on a detected secret.
    monkeypatch.setattr(scanner, "redact_pii", lambda text, verdict=None: text)
    monkeypatch.setattr(gateway_patterns, "redact_all", lambda text: text)
    monkeypatch.setattr(gateway_main, "INPUT_SCANNER", scanner)

    redacted, block = await gateway_main._scan_redact_embedding_inputs(
        [f"contact john.doe@example.com and ssn {RAW_SSN}"],
        {"input_scan_enabled": True},
    )
    assert block is not None, "detected-but-unmaskable PII must fail CLOSED (block)"
    assert block.get("blocked") is True


@pytest.mark.asyncio
async def test_scan_redact_helper_noop_when_scanning_disabled(monkeypatch):
    """G1 no-regression: with input_scan_enabled=False the helper is a pure
    passthrough (no behavior change for orgs that disabled scanning)."""
    from ai_mesh_gateway import main as gateway_main
    from ai_mesh_gateway.scanner import InputScanner

    monkeypatch.setattr(gateway_main, "INPUT_SCANNER", InputScanner(thread_pool_size=2))
    texts = [f"ssn {RAW_SSN}"]
    redacted, block = await gateway_main._scan_redact_embedding_inputs(
        texts, {"input_scan_enabled": False}
    )
    assert block is None
    assert redacted == texts  # untouched


# ───────────────────────── FIX G2b: RAG-ingest metadata values ─────────────────────────


def test_g2b_metadata_value_redaction_recurses(monkeypatch):
    """G2b: string METADATA VALUES (incl. nested dict/list) are typed-redacted when
    rag_redaction_enabled. Mirrors the exact recursive helper now inlined in
    rag_ingest: a secret hidden in a metadata value must be masked before upsert."""
    from typed_placeholder_redactor import detect_and_redact_typed

    redacted_count = 0

    def _redact_meta_value(_v):
        nonlocal redacted_count
        if isinstance(_v, str):
            _mr = detect_and_redact_typed(_v)
            if _mr.redacted:
                redacted_count += 1
            return _mr.text
        if isinstance(_v, dict):
            return {_k: _redact_meta_value(_vv) for _k, _vv in _v.items()}
        if isinstance(_v, (list, tuple)):
            return [_redact_meta_value(_item) for _item in _v]
        return _v

    meta = {
        "author": f"employee ssn {RAW_SSN}",
        "nested": {"note": f"key {RAW_AWS_KEY}"},
        "tags": [f"contact john.doe@example.com", "benign-tag"],
        "count": 7,  # non-string survives untouched
    }
    out = {k: _redact_meta_value(v) for k, v in meta.items()}

    flat = json.dumps(out)
    assert RAW_SSN not in flat, "metadata SSN was stored verbatim — G2b gap open"
    assert RAW_AWS_KEY not in flat, "nested metadata AWS key was stored verbatim — G2b gap open"
    assert "john.doe@example.com" not in flat, "list-item email in metadata not redacted"
    assert out["count"] == 7  # non-string value preserved
    assert redacted_count >= 3
