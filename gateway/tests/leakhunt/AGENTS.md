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

## B4 — multi-engine redaction parity (typed redactor span-narrowing)
This repo has TWO redaction engines that MUST stay byte-structurally identical, or a
leak slips through one while the other reports clean:
- `patterns.redact_all` — partial `***` mask, per-pattern sequential `re.sub`. For
  `phone_us_bare_contextual` the masker (`_mask_phone_bare_contextual`) rewrites ONLY
  the trailing digit run and PRESERVES the cue/gap prefix.
- `typed_placeholder_redactor.detect_and_redact_typed` — bare `[TYPE]` label
  (RAG-ingest / at-rest / e11 returned-document). It collects ALL matches then MERGES
  overlapping spans (longest span wins the placeholder) and WHOLE-SPAN replaces.
GOTCHA: when you BROADEN a context-gated pattern whose match span includes a cue/gap
(e.g. `phone_us_bare_contextual`'s `[^\d\n]{0,40}?` gap between the contact verb and
`at/on`), the typed engine's whole-span replace + longest-span-wins merge will SWALLOW
any nested PII match sitting in that gap — e.g. `contact <email>, call back on <phone>`
collapses to `[PHONE]`, dropping `[EMAIL]`. Fix = narrow the typed engine's collected
span for that type to the VALUE sub-match (re-search `_BARE_PHONE_10_SPLIT` inside the
match), mirroring `_mask_phone_bare_contextual`. After ANY patterns.py span change run
BOTH `test_e11_egress_default_path` (typed/at-rest) AND `tests/leakhunt/test_b4_*`
(chat-vs-embed differential) — e10/leakhunt alone won't catch a typed-engine swallow.

## B2 — phone coverage rigor (egress-byte adversarial method + FP boundary)
When re-proving a redactor-coverage story, drive the REAL InputScanner + LLMRouter
egress path with formats NOT in the corpus (the corpus is the spec UNDER test) and
classify each leak: is it PHANTOM (scanner removed it from redacted_content but it still
rode the wire — an egress=truth/B1 honesty violation, ALWAYS a real gap) or a pure
COVERAGE gap (scanner never flagged it, raw stayed in redacted_content, no redact claim
— judged against B2's conservative FP contract)? In this repo every phone leak found was
COVERAGE-only, never phantom. ORACLE NOTE: `aidefence_has_pii` does NOT classify phone
numbers as PII (its set = email/SSN/keys/passwords), so for PHONE leaks the relevant
independent oracle is the corpus `phone_sep` regex / `independent_pii_scan`, not aidefence.
REAL GAP FIXED: `phone_intl`'s grouped branch `\d{1,3}(?:[\s\-]\d{1,4}){2,6}` capped each
group at 4 digits, missing the very common 5+5 international grouping (+91 98765 43210).
Widened to `\d{1,5}`; FP-safe because the whole pattern is `+`-anchored (E.164 marker —
order-ids/revenue never carry it). BY-DESIGN pass-throughs to LEAVE (non-phantom + high-FP
+ not-PII-per-oracle): bare 2+4+4 grouping, slash/underscore separators, and `00`-prefix
intl (ambiguous with long account nums).
REAL GAP FIXED (2026-06-30 rigor, direct-adjacency): a STRONG dialing/messaging verb placed
IMMEDIATELY before the number with NO "at/on" connector ("sms 89295 54991",
"whatsapp 8929554991", "dial 4155550142") leaked RAW to the wire (COVERAGE gap, never
phantom) — the imperative cue branch demanded "...<=40 non-digit chars> (at|on)" and Tier-2
did not flag it. Added a curated direct-adjacency branch to `phone_us_bare_contextual`:
`\b(?:call|text|dial|ring|sms|whatsapp|telegram|imessage)\b[:\s]+` directly on
`_BARE_PHONE_10_SPLIT`. This SUPERSEDES the prior "whatsapp = pass-through" note: whatsapp
(and telegram/imessage) are now masked, but ONLY under the FP-safe direct-adjacency gate —
the gap is whitespace/colon ONLY, so the 10-digit grouping must START right after the cue
("call 5000 customers", "text the 1234567890 line", "we will call 89295 customers in batch
54991" stay verbatim). `message`/`msg`/`reach`/`contact` were deliberately KEPT OUT of the
direct-adjacency list (still gated on at/on) because a bare run after them is more
order-id-shaped. Do NOT re-restrict these to at/on without re-proving the leak via the FULL
egress path (InputScanner.redact_pii + LLMRouter backstop + RecordingProvider), not redact_all.

## B1 — non-digit fail-closed (the chat egress gap the digit round missed)
Egress=truth on the input/egress path has TWO sub-cases. The 2026-06-29 round closed the
DIGIT case (Tier-2 evidence digit spans → main.py passes the verdict to redact_pii). The
2026-06-30 round found the NON-DIGIT case open: a Tier-2 guard that RECOMMENDS redaction
becomes action="flag"/threat_type="sensitive_content" (scanner.py:1370), main.py's flag
branch calls redact_pii (= redact_all + redact_evidence_digit_spans, DIGIT-ONLY), and a
natural-language credential/name/address is a TOTAL redact no-op → raw egressed while
telemetry said redact. Criterion 1 demands fail-closed. FIX (main.py, after the redact/flag
branches): byte-verify with `_redact_text_with_backstop(pre, post)`; if == pre and
enforcement_mode=="block" → 403 block before acompletion. Mirrors the embeddings guard
exactly. Cell: test_b1_chat_endpoint_fails_closed_on_unmaskable_flagged_span (stub
scan_prompt → flag/sensitive_content, prompt carries an oracle-positive passphrase
"correcthorsebatterystaple"; assert APIStatusError raised AND acompletion never called).
ORACLE: aidefence_has_pii flags passwords/keys/SSN/email but NOT bare names/addresses — pick
an oracle-positive unmaskable value or the leak proof is vacuous.
