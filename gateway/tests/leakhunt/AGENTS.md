# gateway/tests/leakhunt — leak-hunt egress capture rig (A2)

This is the pytest gate for every leak (B-) story. Run it with the gateway venv:

```bash
cd gateway && ./.venv/bin/python -m pytest tests/leakhunt -q
```

## Why this dir lives OUTSIDE `ai_mesh_gateway/`
The CLAUDE.md gate path is literally `tests/leakhunt` (resolved from `gateway/`), so
the suite must sit at `gateway/tests/leakhunt/`. Because it is outside the package,
`ai_mesh_gateway/conftest.py` does NOT load — `conftest.py` here puts
`ai_mesh_gateway` on `sys.path` (so `import main` / `from llm_router import ...`
resolve like the in-tree tests) and adds this dir (so `import corpus` /
`import recording_provider` resolve as flat sibling modules). Do not add `__init__.py`.

The repo-root `tests/leakhunt/capture_addon.py` is a SEPARATE mitmproxy addon for
live-fleet capture — it is NOT part of this pytest suite.

## Files
- `corpus.py` — `PiiItem` + `CORPUS`, the single source of truth for raw PII values
  that must never egress. `.covered` = neutralized by today's redactor;
  `KNOWN_LEAKS` = real gaps the B-stories fix; `intentional=True` = kept raw BY
  DESIGN (e.g. bare 10-digit with no phone cue — ambiguous order-id shape).
- `recording_provider.py` — `RecordingProvider.install(monkeypatch)` patches
  `litellm.acompletion/aembedding/aresponses`, the egress choke point reached when
  `LLMRouter({"org_only_inference": True})` runs with an empty router. `wires` =
  captured request bytes. `assert_no_pii_egressed(corpus)` substring-scans them;
  `independent_pii_scan` is an oracle whose regexes are deliberately NOT `patterns.py`.
  `RecordingVectorStore` records at-rest documents+metadata for the B5 at-rest test.

## Conventions / gotchas
- Egress = truth: only assert on the captured **wire bytes**, never on the trace or
  the client response (those are pre-redaction / model output).
- Mark a documented-but-unfixed leak `@pytest.mark.xfail(strict=False)` so the gate
  stays green and the fix shows up as an XPASS when a B-story lands.
- The fail-closed digit backstop (`_redact_text_with_backstop`) NOW also catches
  separator-split digit runs (`\d[\d .()\-]{5,}\d`) on top of contiguous `\d{7,}`
  (B1). It is the EGRESS = TRUTH choke for chat / chat-stream / Responses-tools: it
  masks any run the firewall's `redacted_content` REMOVED that `redact_all` left raw.
  It is gated on `run not in redacted_content`, so it only enforces what the firewall
  already decided — it does NOT improve detection coverage (that's B2).
- B2 LANDED: `patterns.py` `phone_us_bare_contextual` now masks separator-split
  10-digit phones behind a phone cue (`_BARE_PHONE_10_SPLIT`), so the 5+5 G0 case is
  CLOSED. `corpus.us_5_5_spaced` is `covered=True` and `KNOWN_LEAKS` is now empty;
  `test_repro_g0.py` is a GREEN closure regression (no longer xfail). When you add a
  NEW documented-but-unfixed leak, set `covered=False` and xfail its repro as before.
- Add a new leaking format by appending a `PiiItem` to `corpus.py`; the existing
  `assert_no_pii_egressed` gate picks it up automatically.
- B3 LANDED: `test_b3_embeddings_scan.py` is the egress-truth gate for /v1/embeddings
  input redaction (G1). It drives the FULL path `main._scan_redact_embedding_inputs`
  -> `LLMRouter.aembedding` and asserts no `corpus.REDACTABLE` value reached the
  captured EMBEDDING wire. EMBEDDING ROUTER GOTCHA: `aembedding` validates `model`
  against `_allowed_embedding_model_names()`; with an empty (org_only_inference)
  router that set is ONLY `_DEFAULT_EMBEDDING_MODEL` ("text-embedding-3-small") — use
  it, or you get a clean 404 BEFORE any egress and capture nothing. Fail-closed proof:
  on an unmaskable input the helper returns a block and the handler never dispatches,
  so assert `provider.embed == []` (nothing egressed), not just "raw absent".
- conftest now also puts repo-root `shared/` on sys.path (`_HERE.parents[2]/"shared"`)
  so `import main` resolves `ai_mesh_shared` and the CLAUDE.md gate
  (`cd gateway && pytest tests/leakhunt -q`) runs with NO manual PYTHONPATH.
- B4 LANDED: `test_b4_unify_redaction_paths.py` is the DIFFERENTIAL gate — it proves
  the chat/simulator egress and the embedding/RAG-ingest egress apply IDENTICAL
  redaction (byte-for-byte) over the whole corpus, so no path leaks while another
  redacts. ROOT FIX: `main._scan_redact_embedding_inputs` now delegates to the chat
  redactor `llm_router._redact_text_with_backstop(text, red_pii)` (red_pii =
  `INPUT_SCANNER.redact_pii`) instead of its own blanket `\d{7,}`-on-no-op backstop,
  which over-redacted the corpus's `bare10_no_cue` order-id (chat KEEPS it). To test a
  new egress surface, drive REAL egress via RecordingProvider and assert
  `chat_out == surface_out` (not just "raw absent") — divergence is the bug. The
  operator simulator IS the chat path (LLM_ROUTER.acompletion); there's no separate
  simulator handler. Identity-of-callable across `import main` vs
  `from ai_mesh_gateway import main` is UNRELIABLE in this env (two module instances) —
  assert functional equivalence (`__qualname__` + byte-identical output), not `is`.
- B1 END-TO-END cell (`test_b1_chat_endpoint_honors_tier2_evidence_digit_span`): the
  router-level B1 cells hand-feed an already-correct `redacted_content` to
  `acompletion`, so they prove the ROUTER honors a good verdict but NOT that main.py
  PRODUCES one. To test the CALLER, reuse the SDK harness
  (`from ai_mesh_gateway.tests import test_openai_sdk_compat as H`; call
  `H._make_sdk_app(monkeypatch, redis_client=None)` for a real ASGI app + auth), stub
  `INPUT_SCANNER.scan_prompt` (+ `scan_prompt_with_tier2`) to return a Tier-2-evidence
  `ScanVerdict`, and patch `LLM_ROUTER.acompletion` to CAPTURE the `redacted_prompt`
  main.py forwards. CRITICAL: patch on the SAME module object the harness patches —
  `from ai_mesh_gateway import main as gateway_main` (flat `import main` is a different
  instance; its `INPUT_SCANNER` is None and the patch is invisible). Root invariant the
  cell guards: ANY egress redaction call must pass `verdict=` — `redact_pii(text)`
  without it == plain `redact_all` (scanner.py:854), silently dropping
  `redact_evidence_digit_spans` for Tier-2-flagged bare digit runs.
- B5 LANDED: `test_b5_rag_at_rest.py` is the AT-REST gate (G2) — nothing raw is
  embedded/stored in the vector store (documents OR metadata). It drives the EXACT
  redaction rag_ingest funnels every doc+metadata through before `client.add()`
  (`main._scan_redact_embedding_inputs` for content + `main._scan_redact_metadata`
  for metadata — the unified B4 helpers, gated by `input_scan_enabled` default True),
  PERSISTS the result into a REAL chromadb collection, and QUERIES the store directly
  (`col.get(include=['documents','metadatas'])`). Backend: prefers the live :8001
  server when a full create/add/get round-trip works, else an in-process chromadb
  engine (EphemeralClient) — both real stores. CHROMA GOTCHAS: (1) create the client
  ONCE per module via a `scope="module"` fixture handing out freshly-named
  collections; a 2nd `EphemeralClient(...)` raises "instance already exists for
  ephemeral with different settings". (2) chromadb metadata values must be SCALAR —
  serialize the (nested) redacted metadata to one JSON string field for storage, then
  scan that blob. (3) pass explicit per-doc embeddings to avoid an ONNX model
  download. METADATA CUE GOTCHA: a context-gated PII value (bare-10-digit
  `8929554991`, 5+5 split, no-sep SSN `123456789`) stored in metadata WITHOUT a
  surrounding phone/ssn cue is order-id-shaped and intentionally KEPT (FP protection,
  the corpus `intentional` pass-through). Build metadata values in their natural cued
  phrasing (`item.prompt()` == cue+raw), NOT bare `item.raw`, or the at-rest assertion
  forces over-redaction of order-ids. Closing the bare-value-in-metadata edge would be
  a separate metadata-key-aware-redaction story, not B5. SAFE-BY-DEFAULT: B5 flipped
  the rag_ingest `rag_redaction_enabled` default to True (main.py ~10182), adding the
  typed-placeholder pass as defense-in-depth on top of the input_scan-forced unified
  redaction — but the at-rest PII guarantee is already policy-forced by
  `input_scan_enabled` (default True) regardless of that toggle.
