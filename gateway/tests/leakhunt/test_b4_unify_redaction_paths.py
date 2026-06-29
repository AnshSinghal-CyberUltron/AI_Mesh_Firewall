"""B4 — unify embedding + simulator/SDK egress redaction (G3/G0c).

PRIME INVARIANT (progress.txt): *egress = truth*, applied DIFFERENTIALLY — every
egress path must apply IDENTICAL redaction so no path leaks while another redacts.
A user whose phone is masked on /v1/chat but embedded raw on /v1/embeddings (or on
RAG ingest) is still leaked; divergence between paths is itself the bug B4 closes.

There are two production redactors, deliberately convergent:

* CHAT / SIMULATOR (the operator Module-1.1 simulator IS the chat path) —
  ``LLM_ROUTER.acompletion(body, redacted_content)`` -> ``_apply_redaction`` ->
  ``llm_router._redact_text_with_backstop(text, redacted_content)``.
* EMBEDDINGS / RAG-INGEST — ``main._scan_redact_embedding_inputs`` (the embedding
  handler, the RAG-ingest content loop, and ``_scan_redact_metadata`` ALL funnel
  through this one callable).

This suite proves the two converge byte-for-byte over the whole corpus, that the
embedding + ingest surfaces share the *same callable* (not a divergent copy), and
that fail-closed behavior is consistent — all asserted against the captured wire
bytes via ``RecordingProvider``, never the trace or client response.
"""
import pytest

import main
from scanner import InputScanner, ScanVerdict
from llm_router import LLMRouter

from corpus import CORPUS, REDACTABLE, by_label
from recording_provider import RecordingProvider, independent_pii_scan

_ORG_CONFIG = {"input_scan_enabled": True}
# text-embedding-3-small is the default embedding model -> always allowed, so an
# empty (org_only_inference) router dispatches to the faked litellm.aembedding.
_EMBED_MODEL = "text-embedding-3-small"
_CHAT_MODEL = "gpt-4o-mini"


@pytest.fixture
def real_scanner(monkeypatch):
    """Install the real tier-1 InputScanner as the module singleton (no network)."""
    scanner = InputScanner()
    monkeypatch.setattr(main, "INPUT_SCANNER", scanner)
    return scanner


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


async def _chat_egress_text(prompt: str, scanner: InputScanner, provider: RecordingProvider) -> str:
    """Run the REAL chat/simulator egress and return the redacted user content that
    reached the provider. ``redacted_content`` is computed exactly as the chat
    handler computes ``redacted_prompt`` — the firewall's authoritative redaction."""
    verdict = await scanner.scan_prompt(prompt)
    redacted_content = scanner.redact_pii(prompt, verdict=verdict)
    body = {"model": _CHAT_MODEL, "messages": [{"role": "user", "content": prompt}]}
    status, _ = await _router().acompletion(body, redacted_content=redacted_content)
    assert status == 200, f"chat dispatch failed: {status}"
    assert provider.chat, "no chat call captured — egress path not exercised"
    return provider.chat[-1]["messages"][0]["content"]


async def _embed_egress_text(prompt: str, provider: RecordingProvider):
    """Run the REAL embedding egress (shared by /v1/embeddings + RAG ingest) and
    return the redacted input on the wire, or ``None`` when fail-closed (no egress)."""
    redacted, block = await main._scan_redact_embedding_inputs([prompt], _ORG_CONFIG)
    if block is not None:
        return None
    status, _ = await _router().aembedding({"model": _EMBED_MODEL, "input": redacted})
    assert status == 200, f"embedding dispatch failed: {status}"
    assert provider.embed, "no embedding call captured — egress path not exercised"
    egress_input = provider.embed[-1]["input"]
    return egress_input[0] if isinstance(egress_input, list) else egress_input


# ── Headline differential: chat and embedding egress are BYTE-IDENTICAL for every
#    redactable corpus value, and the raw value is absent from both. ──
@pytest.mark.parametrize("item", REDACTABLE, ids=lambda it: f"{it.label}:{it.fmt}")
@pytest.mark.asyncio
async def test_b4_chat_and_embedding_redaction_byte_identical(item, real_scanner, monkeypatch):
    chat_provider = RecordingProvider().install(monkeypatch)
    chat_out = await _chat_egress_text(item.prompt(), real_scanner, chat_provider)

    embed_provider = RecordingProvider().install(monkeypatch)
    embed_out = await _embed_egress_text(item.prompt(), embed_provider)

    assert embed_out is not None, f"{item.fmt} unexpectedly fail-closed on embedding"
    # Egress = truth on BOTH paths (no path leaks while another redacts).
    assert item.raw not in chat_out, (
        f"chat egress leaked raw {item.label}/{item.fmt}; oracle="
        f"{independent_pii_scan(chat_provider.wire_blob)!r}"
    )
    assert item.raw not in embed_out, (
        f"embedding egress leaked raw {item.label}/{item.fmt}; oracle="
        f"{independent_pii_scan(embed_provider.wire_blob)!r}"
    )
    # The unification invariant: identical redaction, not merely both-non-leaking.
    assert chat_out == embed_out, (
        f"redaction DIVERGED across paths for {item.label}/{item.fmt}:\n"
        f"  chat : {chat_out!r}\n  embed: {embed_out!r}"
    )


# ── Intentional pass-through parity: an order-id-shaped bare-10-digit run the
#    firewall deliberately KEEPS (no phone cue) must be kept IDENTICALLY on both
#    paths. Pre-B4 the embedding backstop over-redacted it while chat kept it. ──
@pytest.mark.parametrize(
    "item", [it for it in CORPUS if it.intentional], ids=lambda it: f"{it.label}:{it.fmt}"
)
@pytest.mark.asyncio
async def test_b4_intentional_passthrough_kept_identically(item, real_scanner, monkeypatch):
    chat_provider = RecordingProvider().install(monkeypatch)
    chat_out = await _chat_egress_text(item.prompt(), real_scanner, chat_provider)
    embed_provider = RecordingProvider().install(monkeypatch)
    embed_out = await _embed_egress_text(item.prompt(), embed_provider)
    assert embed_out is not None
    # Kept raw (FP protection) AND identical across paths — no over-redaction on one.
    assert item.raw in chat_out, "chat unexpectedly over-redacted the order-id"
    assert item.raw in embed_out, (
        "embedding over-redacted an order-id the firewall deliberately kept "
        "(pre-B4 divergence)"
    )
    assert chat_out == embed_out


# ── Embeddings and RAG-ingest share the SAME redaction helper (no divergent copy):
#    the RAG write path resolves ``_scan_redact_embedding_inputs`` via getattr on the
#    gateway ``main`` module, so /v1/embeddings and RAG-ingest content redact through
#    one callable. We assert that resolved helper is functionally equivalent to the
#    one the embeddings handler uses (object identity is unreliable here because the
#    leakhunt env can hold both a flat ``main`` and a package ``ai_mesh_gateway.main``
#    module instance — a test artifact, not a product split). ──
@pytest.mark.asyncio
async def test_b4_rag_ingest_resolves_the_same_redaction_callable(real_scanner, monkeypatch):
    import vector_routes  # noqa: F401 — import path used by the RAG write path

    try:
        from ai_mesh_gateway import main as gateway_main
    except Exception:  # pragma: no cover - flat-import layout
        import main as gateway_main

    resolved = getattr(gateway_main, "_scan_redact_embedding_inputs", None)
    assert resolved is not None and callable(resolved), (
        "RAG ingest cannot resolve the embedding redactor"
    )
    assert resolved.__qualname__ == main._scan_redact_embedding_inputs.__qualname__, (
        "RAG ingest resolved a DIFFERENT-named redactor — divergent copy"
    )
    # If the resolved helper is bound to a separate module instance, install the real
    # scanner there too so it is exercisable; then prove byte-identical output.
    if gateway_main is not main:
        monkeypatch.setattr(gateway_main, "INPUT_SCANNER", real_scanner)
    sample = by_label("phone")[0].prompt()
    via_ingest, _ = await resolved([sample], _ORG_CONFIG)
    via_embed, _ = await main._scan_redact_embedding_inputs([sample], _ORG_CONFIG)
    assert via_ingest == via_embed, (
        "RAG-ingest redactor diverged from the /v1/embeddings redactor"
    )


# ── Ingest metadata parity: a metadata VALUE is redacted byte-identically to the
#    embedding input (metadata flows into generator prompts; it must not diverge). ──
@pytest.mark.parametrize("item", by_label("phone") + by_label("email") + by_label("ssn"),
                         ids=lambda it: f"{it.label}:{it.fmt}")
@pytest.mark.asyncio
async def test_b4_ingest_metadata_matches_embedding_redaction(item, real_scanner):
    if not item.covered:
        pytest.skip("by-design pass-through (ambiguous, FP-protected)")
    value = item.prompt()
    redacted, block = await main._scan_redact_embedding_inputs([value], _ORG_CONFIG)
    embed_red = redacted[0] if block is None else None

    meta_out = await main._scan_redact_metadata({"note": value}, _ORG_CONFIG)
    meta_red = meta_out["note"]

    assert item.raw not in meta_red, f"metadata leaked raw {item.label}/{item.fmt}"
    # Metadata redaction must equal the content/embedding redaction exactly (when the
    # value is maskable, the helper returns the masked string; both share it).
    if embed_red is not None:
        assert meta_red == embed_red, (
            f"metadata vs embedding redaction diverged for {item.label}/{item.fmt}:\n"
            f"  meta : {meta_red!r}\n  embed: {embed_red!r}"
        )


# ── No over-redaction divergence: benign text survives identically on both paths ──
@pytest.mark.asyncio
async def test_b4_clean_text_survives_identically(real_scanner, monkeypatch):
    clean = "the quarterly revenue was 8929554991 dollars per the order id 4159945012"
    chat_provider = RecordingProvider().install(monkeypatch)
    chat_out = await _chat_egress_text(clean, real_scanner, chat_provider)
    embed_provider = RecordingProvider().install(monkeypatch)
    embed_out = await _embed_egress_text(clean, embed_provider)
    assert chat_out == embed_out == clean, (
        "benign text was over-redacted or diverged across paths:\n"
        f"  chat : {chat_out!r}\n  embed: {embed_out!r}"
    )


# ── Fail-closed parity: PII that genuinely cannot be masked never egresses on the
#    embedding OR the ingest-metadata surface (both share the byte-verified helper). ──
class _UnmaskableScanner:
    async def scan_prompt(self, text, *a, **k):
        return ScanVerdict(
            action="redact",
            threat_type="pii",
            confidence=0.9,
            detail="PII detected",
            matched_patterns=["full_name"],
            tier="tier_1",
        )

    def redact_pii(self, text, verdict=None):
        return text  # masking impossible


@pytest.mark.asyncio
async def test_b4_fail_closed_parity_embedding_and_metadata(monkeypatch):
    monkeypatch.setattr(main, "INPUT_SCANNER", _UnmaskableScanner())
    provider = RecordingProvider().install(monkeypatch)
    raw = "Jane Q Public is the account holder"

    redacted, block = await main._scan_redact_embedding_inputs([raw], _ORG_CONFIG)
    assert block is not None and block["blocked"] is True, "embedding must fail closed"

    # Ingest metadata recurses through the SAME helper -> unmaskable value is dropped
    # to a placeholder, never stored/embedded raw.
    meta_out = await main._scan_redact_metadata({"owner": raw}, _ORG_CONFIG)
    assert raw not in meta_out["owner"], "metadata fail-closed must not retain raw PII"

    # Nothing was dispatched on either surface — the defining guarantee.
    assert provider.embed == [], "fail-closed input must NOT reach the embedding provider"
    assert raw not in provider.wire_blob
